"""
Account Pulse Agent — daily intelligence briefing.

Scans news, SEC filings, and cybersecurity events across a seller's scoped
accounts, then delivers a prioritized morning briefing.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from agent_framework import Agent, tool
from agent_framework.azure import AzureOpenAIResponsesClient

try:
    from .config import get_account_pulse_execution_mode
    from .databricks_tools import get_scoped_accounts, get_top_opportunities
    from .parallel_scan import ScanBundle, build_scan_parents_parallel_tool
except ImportError:
    from config import get_account_pulse_execution_mode
    from databricks_tools import get_scoped_accounts, get_top_opportunities
    from parallel_scan import ScanBundle, build_scan_parents_parallel_tool

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

ACCOUNT_PULSE_INSTRUCTIONS = """You are Account Pulse, a daily intelligence briefing agent for a Veeam field seller. Your job is to scan external sources for news, SEC filings, and cybersecurity events across the seller's scoped accounts, then deliver a prioritized morning briefing.

## When to Activate

Activate when the user asks for a briefing in any form: "Give me my briefing", "What's going on in my accounts?", "Any news today?", "What's happening?", etc. Activate a single-account scan when the user names a specific account.

## Execution Rule

For any briefing request, including a follow-up that names a specific account, call `generate_account_pulse_briefing` exactly once and return its markdown output exactly as your final answer.

`generate_account_pulse_briefing` already:
- loads the signed-in seller's scoped accounts
- narrows to a named account when the request clearly asks for one
- applies Velocity candidate filtering when needed
- groups by Global Ultimate
- runs the internal parallel parent scan
- formats the final seller-facing markdown briefing

Do not perform your own direct web searches or EDGAR lookups in the top-level Account Pulse agent.
Do not return raw JSON, tool payloads, notes, or commentary before or after the briefing.

## Reference Format

### Header
## Account Pulse
[Full date]  ·  generated at [H:MM am/pm]  ·  live

| Threats | Changes | Business | Regulatory |
|---------|---------|----------|------------|
| [n] | [n] | [n] | [n] |

---

### Enterprise / Commercial — one card per signal, sorted Tier 1 to 4:

### [tier indicator] [Account Name]
[1-3 factual sentences. Note the parent for subsidiaries. No opinion.]
[[Publication — Date]](url)
> *[Practical Veeam-lens sales nudge. No label. One or two sentences.]*

Rules:
- No field labels ("What happened", "Source") — structure carries the meaning
- Source is a plain markdown link on its own line
- Insight is a blockquote with italic text
- No horizontal rules between cards — whitespace only
- One `---` at the very end before the quiet count

Close with:
---
*[N] accounts had no new signals in the last 48 hours.*

### Velocity — top 5 one-liners only:
1. **[Account]** — [Event]. [What to do]. [[Source]](url)

### No news:
**Your accounts are quiet today.**
*No significant signals in the last 48 hours. A good day to focus on pipeline and outreach.*

## Hallucination Guardrails — Non-Negotiable

1. Every claim needs a source with publication name, date, and link. No source = no mention.
2. Never speculate or fabricate URLs.
3. Never confuse companies with similar names.
4. Flag single-source stories: "Developing — reported by [source], not yet confirmed."
5. No invented financial data.
6. If the seller asks how data access works, explain that the planner uses the signed-in user's permitted enterprise data access through the configured sources.

## Filing Handling

- If a returned signal is an 8-K, summarize the factual event in 1-3 sentences with a direct link.
- If a returned signal is a 10-K or 10-Q, note the filing type, date, and link without inventing financial detail.
- If no EDGAR-backed signal was returned for a parent, do not mention EDGAR.

## Segment Behavior

Enterprise / Commercial: Every account, full EDGAR, full cards.
Velocity: Use `get_top_opportunities(filter_mode="velocity_candidates")` to prefilter, then return only accounts with signals as top 5 one-liners and skip EDGAR.

---
## FORMAT REMINDER — follow exactly

Header (always first):

## Account Pulse
[Full date]  ·  generated at [H:MM am/pm]  ·  live

| Threats | Changes | Business | Regulatory |
|---------|---------|----------|------------|
| [n] | [n] | [n] | [n] |

---

One card per signal:

### [tier indicator] [Account Name]
[1-3 factual sentences. No opinion.]
[[Publication — Date]](url)
> *[Practical Veeam-lens nudge. 1-2 sentences.]*

Critical rules — no exceptions:
- H3 heading = tier indicator + company name ONLY. No text label.
- No field labels anywhere ("What happened", "Source", "Insight" — none)
- Source = plain markdown link on its own line, nothing else
- Insight = blockquote with italic text, nothing else
- No `---` between cards — whitespace only
- One `---` before the final quiet count
- Final answer must be markdown, never JSON

Close with:

---
*[N] accounts had no new signals in the last 48 hours.*

---
Give me my morning briefing."""

_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
_THREAT_KEYWORDS = ("ransomware", "breach", "cyber", "malware", "outage", "extortion", "phishing")
_CHANGE_KEYWORDS = ("appointed", "migration", "expansion", "pilot", "acquisition", "merger", "restructuring")
_REGULATORY_KEYWORDS = ("gdpr", "hipaa", "dora", "nis2", "regulatory", "compliance", "audit", "8-k", "10-q", "10-k")
_TIER_LABELS = {1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 4: "Tier 4"}
logger = logging.getLogger(__name__)


def _format_generated_timestamp() -> tuple[str, str]:
    now = datetime.now(_PACIFIC_TZ)
    full_date = f"{now.strftime('%B')} {now.day}, {now.year}"
    hour = now.strftime("%I").lstrip("0") or "0"
    minute_suffix = now.strftime("%M %p").lower()
    return full_date, f"{hour}:{minute_suffix}"


def _format_source_link(signal: dict[str, Any]) -> str:
    source_name = (signal.get("source_name") or "Source").strip()
    published_at = (signal.get("published_at") or "").strip()
    source_url = (signal.get("source_url") or "").strip()
    label = f"{source_name} - {published_at}" if published_at else source_name
    return f"[[{label}]]({source_url})"


def _classify_bucket(signal: dict[str, Any]) -> str:
    summary = (signal.get("summary") or "").lower()
    signal_type = (signal.get("signal_type") or "").lower()
    source_kind = (signal.get("source_kind") or "").lower()
    if signal_type == "cybersecurity" or any(keyword in summary for keyword in _THREAT_KEYWORDS):
        return "threats"
    if source_kind == "edgar" or any(keyword in summary for keyword in _REGULATORY_KEYWORDS):
        return "regulatory"
    if any(keyword in summary for keyword in _CHANGE_KEYWORDS):
        return "changes"
    return "business"


def _format_relationship_clause(values: list[str], prefix: str) -> str | None:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return None
    return f"{prefix} {', '.join(cleaned[:2])}"


def _build_nudge(signal: dict[str, Any]) -> str:
    relationship_context = signal.get("relationship_context") or {}
    prospect_values = relationship_context.get("customer_or_prospect") or []
    product_values = relationship_context.get("current_veeam_products") or []
    renewal_values = relationship_context.get("renewal_dates") or []
    stage_values = relationship_context.get("opportunity_stages") or []
    touch_values = relationship_context.get("last_seller_touch_dates") or []
    signal_type = (signal.get("signal_type") or "").lower()
    source_kind = (signal.get("source_kind") or "").lower()

    clauses: list[str] = []
    if signal_type == "cybersecurity":
        clauses.append("Check in on their recovery posture and backup-readiness plans")
    elif source_kind == "edgar":
        clauses.append("Use the filing as a reason to reconnect while priorities are visible")
    elif "Prospect" in prospect_values:
        clauses.append("Use this change as a warm reason to open a first resilience conversation")
    else:
        clauses.append("Use this as a timely reason to reconnect with the account team")

    for clause in (
        _format_relationship_clause(product_values, "Anchor it to their current footprint:"),
        _format_relationship_clause(renewal_values, "Keep renewal timing in view:"),
        _format_relationship_clause(stage_values, "The active motion is still"),
        _format_relationship_clause(touch_values, "The last seller touch was"),
    ):
        if clause:
            clauses.append(clause)

    sentence = ". ".join(clauses).strip()
    if not sentence.endswith("."):
        sentence += "."
    return sentence


def render_account_pulse_briefing_markdown(
    scan_bundle: dict[str, Any],
    *,
    total_accounts: int,
    segment: str,
) -> str:
    bundle = ScanBundle.model_validate(scan_bundle)
    full_date, generated_at = _format_generated_timestamp()
    segment_name = (segment or "UNKNOWN").strip().upper()
    signals = [signal.model_dump(mode="json") for signal in bundle.signals]

    bucket_counts = {"threats": 0, "changes": 0, "business": 0, "regulatory": 0}
    for signal in signals:
        bucket_counts[_classify_bucket(signal)] += 1

    lines = [
        "## Account Pulse",
        f"{full_date}  ·  generated at {generated_at}  ·  live",
        "",
        "| Threats | Changes | Business | Regulatory |",
        "|---------|---------|----------|------------|",
        f"| {bucket_counts['threats']} | {bucket_counts['changes']} | {bucket_counts['business']} | {bucket_counts['regulatory']} |",
        "",
    ]

    if not signals:
        lines.extend(
            [
                "**Your accounts are quiet today.**",
                "*No significant signals in the last 48 hours. A good day to focus on pipeline and outreach.*",
            ]
        )
        return "\n".join(lines)

    if segment_name == "VEL":
        lines.extend(["---", ""])
        for index, signal in enumerate(signals[:5], start=1):
            lines.append(
                f"{index}. **{signal['account_name']}** — {signal['summary']} {_build_nudge(signal)} {_format_source_link(signal)}"
            )
        lines.extend(["", "---", f"*{len(bundle.quiet_accounts)} accounts had no new signals in the last 48 hours.*"])
        return "\n".join(lines)

    lines.extend(["---", ""])
    for signal in signals:
        parent_name = (signal.get("parent_name") or "").strip()
        account_name = (signal.get("account_name") or parent_name).strip()
        summary = (signal.get("summary") or "").strip()
        if parent_name and account_name != parent_name:
            summary = f"{summary} Parent: {parent_name}."
        lines.extend(
            [
                f"### {_TIER_LABELS.get(signal.get('tier', 3), 'Tier 3')} {account_name}",
                summary,
                _format_source_link(signal),
                f"> *{_build_nudge(signal)}*",
                "",
            ]
        )

    lines.extend(["---", f"*{len(bundle.quiet_accounts)} accounts had no new signals in the last 48 hours.*"])
    return "\n".join(lines)


def _tool_func(tool_obj: Any) -> Any:
    return getattr(tool_obj, "func", tool_obj)


def _normalize_string_list(values: Any) -> list[str]:
    if values in (None, ""):
        return []
    if isinstance(values, list):
        return [
            str(value).strip()
            for value in values
            if value not in (None, "") and str(value).strip().lower() != "none"
        ]
    value = str(values).strip()
    if not value or value.lower() == "none":
        return []
    return [value]


def _matches_requested_account(request: str, row: dict[str, Any]) -> bool:
    normalized_request = f" {request.lower()} "
    for candidate in (
        str(row.get("name", "")).strip().lower(),
        str(row.get("global_ultimate", "")).strip().lower(),
    ):
        if candidate and candidate in normalized_request:
            return True
    return False


def _relationship_context_for_rows(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "customer_or_prospect": sorted({value for row in rows for value in _normalize_string_list(row.get("customer_or_prospect"))}),
        "current_veeam_products": sorted({value for row in rows for value in _normalize_string_list(row.get("current_veeam_products"))}),
        "renewal_dates": sorted({value for row in rows for value in _normalize_string_list(row.get("renewal_date"))}),
        "opportunity_stages": sorted({value for row in rows for value in _normalize_string_list(row.get("opportunity_stage"))}),
        "last_seller_touch_dates": sorted({value for row in rows for value in _normalize_string_list(row.get("last_seller_touch_date"))}),
    }


def _build_parent_scan_targets(
    scoped_accounts_payload: dict[str, Any],
    *,
    request_text: str,
    top_opportunities_payload: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    accounts = list(scoped_accounts_payload.get("accounts", []) or [])
    if not accounts:
        return [], 0

    narrowed_accounts = [row for row in accounts if _matches_requested_account(request_text, row)]
    active_accounts = narrowed_accounts or accounts
    segment = str(scoped_accounts_payload.get("segment", "UNKNOWN")).strip() or "UNKNOWN"
    candidate_account_names = None
    candidate_account_ids = None

    if segment.upper() == "VEL" and top_opportunities_payload:
        candidate_rows = list(top_opportunities_payload.get("accounts", []) or [])
        candidate_account_names = {
            str(row.get("account_name", "")).strip().lower()
            for row in candidate_rows
            if str(row.get("account_name", "")).strip()
        }
        candidate_account_ids = {
            str(row.get("account_id", "")).strip()
            for row in candidate_rows
            if str(row.get("account_id", "")).strip()
        }
        active_accounts = [
            row
            for row in active_accounts
            if str(row.get("account_id", "")).strip() in candidate_account_ids
            or str(row.get("name", "")).strip().lower() in candidate_account_names
            or str(row.get("global_ultimate", "")).strip().lower() in candidate_account_names
        ] or active_accounts

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in active_accounts:
        parent_name = str(row.get("global_ultimate") or row.get("name") or "").strip()
        if not parent_name:
            continue
        groups.setdefault(parent_name, []).append(row)

    scan_mode = "velocity_candidate" if segment.upper() == "VEL" else "full"
    scan_targets: list[dict[str, Any]] = []
    for parent_name, rows in groups.items():
        scan_targets.append(
            {
                "parent_name": parent_name,
                "child_accounts": sorted(
                    {
                        str(row.get("name") or parent_name).strip()
                        for row in rows
                        if str(row.get("name") or parent_name).strip()
                    }
                ),
                "segment": segment,
                "relationship_context": _relationship_context_for_rows(rows),
                "scan_mode": scan_mode,
            }
        )

    scan_targets.sort(key=lambda row: row["parent_name"].lower())
    return scan_targets, len(active_accounts)

# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_account_pulse_agent(
    client: AzureOpenAIResponsesClient,
    *,
    scoped_accounts_tool: Any = get_scoped_accounts,
    top_opportunities_tool: Any = get_top_opportunities,
) -> Agent:
    """Create the Account Pulse agent with semantic Databricks tools and internal parent scanning."""
    scan_parents_parallel = build_scan_parents_parallel_tool(client)
    scoped_accounts_func = _tool_func(scoped_accounts_tool)
    top_opportunities_func = _tool_func(top_opportunities_tool)
    scan_parents_parallel_func = _tool_func(scan_parents_parallel)

    @tool(
        name="generate_account_pulse_briefing",
        description=(
            "Generate the full Account Pulse markdown briefing for the seller request. "
            "Handles scope loading, account narrowing, parent grouping, internal parallel scanning, "
            "and final formatting. Return the markdown exactly as the final answer."
        ),
    )
    async def generate_account_pulse_briefing(request: str) -> str:
        started = time.perf_counter()
        logger.info("Account Pulse briefing started.", extra={"request_text": request[:120]})
        scoped_accounts_payload = json.loads(await scoped_accounts_func())
        print(
            "[account-pulse] scope-payload "
            f"keys={sorted(scoped_accounts_payload.keys())} "
            f"error={str(scoped_accounts_payload.get('error') or '').strip()!r} "
            f"account_count={len(list(scoped_accounts_payload.get('accounts', []) or []))}"
        )
        scoped_accounts_error = str(scoped_accounts_payload.get("error") or "").strip()
        if scoped_accounts_error:
            logger.warning(
                "Account Pulse scope load returned a backend error.",
                extra={"scope_error": scoped_accounts_error, "request_text": request[:120]},
            )
            return (
                "I’m unable to generate your Account Pulse briefing right now because scoped account access failed. "
                f"Backend detail: {scoped_accounts_error}"
            )
        accounts = list(scoped_accounts_payload.get("accounts", []) or [])
        if not accounts:
            logger.warning("Account Pulse briefing aborted because no scoped accounts were returned.")
            return "The data source is temporarily unavailable. Please try again in a moment."
        narrowed_accounts = [row for row in accounts if _matches_requested_account(request, row)]

        top_opportunities_payload = None
        if str(scoped_accounts_payload.get("segment", "")).upper() == "VEL":
            top_opportunities_payload = json.loads(
                await top_opportunities_func(filter_mode="velocity_candidates")
            )

        scan_targets, selected_account_count = _build_parent_scan_targets(
            scoped_accounts_payload,
            request_text=request,
            top_opportunities_payload=top_opportunities_payload,
        )
        print(
            f"[account-pulse] scan-targets count={len(scan_targets)} selected_accounts={selected_account_count}"
        )
        if not scan_targets:
            logger.info("Account Pulse briefing found no matching scan targets.")
            return "I couldn't find a matching account in your current scope. Try the parent or account name shown in your briefing."

        scan_bundle = json.loads(await scan_parents_parallel_func(scan_targets=scan_targets))
        print(
            "[account-pulse] scan-bundle "
            f"completed={scan_bundle.get('scan_targets_completed', 0)} "
            f"failed={scan_bundle.get('scan_targets_failed', 0)} "
            f"signals={len(scan_bundle.get('signals', []) or [])}"
        )
        logger.info(
            "Account Pulse scan completed.",
            extra={
                "segment": str(scoped_accounts_payload.get("segment", "UNKNOWN")).upper(),
                "execution_mode": get_account_pulse_execution_mode(),
                "scan_target_count": len(scan_targets),
                "selected_account_count": selected_account_count,
                "scan_targets_completed": scan_bundle.get("scan_targets_completed", 0),
                "scan_targets_failed": scan_bundle.get("scan_targets_failed", 0),
                "signal_count": len(scan_bundle.get("signals", []) or []),
                "max_observed_concurrency": scan_bundle.get("max_observed_concurrency", 0),
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            },
        )
        if len(narrowed_accounts) == 1:
            requested_account_name = str(narrowed_accounts[0].get("name") or "").strip()
            requested_parent_name = str(
                narrowed_accounts[0].get("global_ultimate") or requested_account_name
            ).strip()
            for signal in scan_bundle.get("signals", []) or []:
                if str(signal.get("parent_name") or "").strip() == requested_parent_name:
                    signal["account_name"] = requested_account_name
                    signal["supporting_accounts"] = [requested_account_name]
        return render_account_pulse_briefing_markdown(
            scan_bundle,
            total_accounts=selected_account_count,
            segment=str(scoped_accounts_payload.get("segment", "UNKNOWN")),
        )

    return client.as_agent(
        name="AccountPulse",
        description=(
            "Specialist for morning briefings across seller accounts using Databricks, "
            "parallel parent scanning, and source-grounded signal aggregation."
        ),
        instructions=ACCOUNT_PULSE_INSTRUCTIONS,
        tools=[generate_account_pulse_briefing],
    )
