"""
Parallel parent-scanning runtime for Account Pulse.

This module keeps Account Pulse as a single seller-facing agent while moving
the expensive per-parent scan into an internal tool with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Sequence

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from pydantic import BaseModel, Field, ValidationError

try:
    from .config import (
        get_account_pulse_execution_mode,
        get_account_pulse_internal_aggregator_enabled,
        get_account_pulse_max_concurrency,
        get_account_pulse_model_concurrency,
        get_account_pulse_replay_fixture_set,
        get_account_pulse_source_mode,
    )
    from .resilience import run_with_rate_limit_retry
except ImportError:
    from config import (
        get_account_pulse_execution_mode,
        get_account_pulse_internal_aggregator_enabled,
        get_account_pulse_max_concurrency,
        get_account_pulse_model_concurrency,
        get_account_pulse_replay_fixture_set,
        get_account_pulse_source_mode,
    )
    from resilience import run_with_rate_limit_retry

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from edgar_lookup import edgar_lookup as _edgar_lookup

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "account_pulse_replay.json"
logger = logging.getLogger(__name__)

_TIER1_KEYWORDS = (
    "ransomware",
    "data breach",
    "cyberattack",
    "cyber attack",
    "security incident",
    "malware",
    "outage",
    "extortion",
    "phishing",
    "compliance penalty",
)
_TIER2_KEYWORDS = (
    "new cto",
    "new cio",
    "new ciso",
    "new ceo",
    "new cfo",
    "appointed",
    "acquisition",
    "merger",
    "restructuring",
    "layoffs",
    "cloud migration",
)
_TIER3_KEYWORDS = (
    "earnings",
    "quarterly",
    "annual report",
    "10-k",
    "10-q",
    "8-k",
    "funding",
    "expansion",
    "contract",
    "revenue",
)
_TIER4_KEYWORDS = (
    "gdpr",
    "hipaa",
    "dora",
    "nis2",
    "regulatory",
    "compliance",
    "audit",
    "investigation",
)


class ScanTarget(BaseModel):
    parent_name: str = Field(..., min_length=1)
    child_accounts: list[str] = Field(default_factory=list)
    segment: str = "UNKNOWN"
    relationship_context: dict[str, Any] = Field(default_factory=dict)
    scan_mode: str = "full"


class CandidateSignal(BaseModel):
    account_name: str = Field(..., min_length=1)
    parent_name: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    source_name: str = Field(..., min_length=1)
    source_url: str = Field(..., min_length=1)
    published_at: str | None = None
    source_kind: str = Field(..., min_length=1)
    signal_type: str = Field(..., min_length=1)
    supporting_accounts: list[str] = Field(default_factory=list)
    relationship_context: dict[str, Any] = Field(default_factory=dict)
    tier_hint: int | None = Field(default=None, ge=1, le=4)


class WorkerScanResult(BaseModel):
    parent_name: str = Field(..., min_length=1)
    child_accounts: list[str] = Field(default_factory=list)
    candidate_signals: list[CandidateSignal] = Field(default_factory=list)
    source_errors: list[str] = Field(default_factory=list)
    timing_ms: float = 0.0
    raw_sources_summary: dict[str, Any] = Field(default_factory=dict)


class WorkerDiagnostic(BaseModel):
    parent_name: str
    status: str
    signal_count: int = 0
    timing_ms: float = 0.0
    source_errors: list[str] = Field(default_factory=list)


class AggregatedSignal(BaseModel):
    account_name: str
    parent_name: str
    tier: int = Field(..., ge=1, le=4)
    summary: str
    source_name: str
    source_url: str
    published_at: str | None = None
    source_kind: str
    signal_type: str
    supporting_accounts: list[str] = Field(default_factory=list)
    relationship_context: dict[str, Any] = Field(default_factory=dict)


class ScanBundle(BaseModel):
    scan_targets_total: int
    scan_targets_completed: int
    scan_targets_failed: int
    quiet_accounts: list[str] = Field(default_factory=list)
    signals: list[AggregatedSignal] = Field(default_factory=list)
    worker_diagnostics: list[WorkerDiagnostic] = Field(default_factory=list)
    max_observed_concurrency: int = 0
    duplicate_signal_count: int = 0
    malformed_worker_count: int = 0


class _ParallelRuntimeState(BaseModel):
    current_concurrency: int = 0
    max_concurrency: int = 0


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _clean_json_text(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _loads_json(text: str) -> Any:
    cleaned = _clean_json_text(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def _tier_from_text(text: str) -> int:
    normalized = text.lower()
    if any(keyword in normalized for keyword in _TIER1_KEYWORDS):
        return 1
    if any(keyword in normalized for keyword in _TIER2_KEYWORDS):
        return 2
    if any(keyword in normalized for keyword in _TIER4_KEYWORDS):
        return 4
    if any(keyword in normalized for keyword in _TIER3_KEYWORDS):
        return 3
    return 3


def _fingerprint_signal(signal: CandidateSignal) -> str:
    summary = re.sub(r"\s+", " ", signal.summary.strip().lower())
    return "|".join(
        (
            signal.parent_name.strip().lower(),
            signal.source_url.strip().lower(),
            signal.source_kind.strip().lower(),
            summary,
        )
    )


def _load_replay_fixtures() -> dict[str, Any]:
    if not _FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Replay fixture file not found: {_FIXTURE_PATH}")
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def load_replay_fixture_set(name: str) -> dict[str, Any]:
    fixtures = _load_replay_fixtures()
    try:
        return fixtures["fixture_sets"][name]
    except KeyError as exc:
        available = ", ".join(sorted(fixtures.get("fixture_sets", {}).keys()))
        raise KeyError(f"Unknown replay fixture set '{name}'. Available: {available}") from exc


def _fixture_for_parent(fixture_set: dict[str, Any], parent_name: str) -> dict[str, Any]:
    sources = fixture_set.get("sources", {})
    if parent_name not in sources:
        raise KeyError(f"Replay fixture set is missing source data for parent '{parent_name}'.")
    return sources[parent_name]


def _build_live_edgar_tool():
    @tool(
        name="edgar_lookup",
        description=(
            "Look up the assigned parent company in SEC EDGAR and return JSON with "
            "public status plus recent 10-K, 10-Q, and 8-K filings."
        ),
    )
    async def edgar_lookup(
        company_name: Annotated[str, Field(description="Parent company name to search in EDGAR")],
    ) -> str:
        result = await asyncio.to_thread(_edgar_lookup, company_name)
        return _json_dumps(result)

    return edgar_lookup


def _build_replay_tools(fixture_set: dict[str, Any], parent_name: str):
    source_payload = _fixture_for_parent(fixture_set, parent_name)

    @tool(
        name="general_news_search",
        description="Return replayed general-news search results for the assigned parent as JSON.",
    )
    async def general_news_search() -> str:
        return _json_dumps({"results": source_payload.get("general_news", [])})

    @tool(
        name="cybersecurity_news_search",
        description="Return replayed cybersecurity search results for the assigned parent as JSON.",
    )
    async def cybersecurity_news_search() -> str:
        if source_payload.get("cyber_error"):
            return _json_dumps({"error": source_payload["cyber_error"], "results": []})
        return _json_dumps({"results": source_payload.get("cyber_news", [])})

    @tool(
        name="edgar_lookup",
        description="Return replayed EDGAR lookup results for the assigned parent as JSON.",
    )
    async def edgar_lookup() -> str:
        if source_payload.get("edgar_error"):
            return _json_dumps({"error": source_payload["edgar_error"], "public": False, "filings": []})
        return _json_dumps(source_payload.get("edgar", {"public": False, "filings": []}))

    return [general_news_search, cybersecurity_news_search, edgar_lookup]


def _worker_instructions(target: ScanTarget, *, source_mode: str) -> str:
    child_accounts = target.child_accounts or [target.parent_name]
    if source_mode == "replay":
        search_section = """
1. Call `general_news_search` once.
2. Call `cybersecurity_news_search` once.
3. Call `edgar_lookup` once.
""".strip()
    else:
        search_section = """
1. Use `web_search` once for: "[Parent Name]" news (last 48 hours).
2. Use `web_search` once for: "[Parent Name]" ransomware OR "data breach" OR "cybersecurity incident" (last 48 hours).
3. Call `edgar_lookup` once.
""".strip()

    return f"""You are an internal Account Pulse scan worker.

You only scan this one parent entity:
- Parent: {target.parent_name}
- Child accounts: {", ".join(child_accounts)}
- Segment: {target.segment}
- Scan mode: {target.scan_mode}

Relationship context JSON:
{_json_dumps(target.relationship_context)}

Task:
{search_section}

Return ONLY valid JSON with this exact top-level object shape:
{{
  "parent_name": "{target.parent_name}",
  "child_accounts": ["..."],
  "candidate_signals": [
    {{
      "account_name": "{target.parent_name}",
      "parent_name": "{target.parent_name}",
      "summary": "1-3 factual sentences grounded only in returned sources.",
      "source_name": "Publication or SEC",
      "source_url": "https://...",
      "published_at": "YYYY-MM-DD or null",
      "source_kind": "general_news|cyber_news|edgar",
      "signal_type": "news|cybersecurity|sec_filing",
      "supporting_accounts": ["..."],
      "relationship_context": {{}},
      "tier_hint": 1
    }}
  ],
  "source_errors": ["..."],
  "raw_sources_summary": {{
    "general_news_count": 0,
    "cyber_news_count": 0,
    "edgar_public": false,
    "edgar_filing_count": 0
  }}
}}

Rules:
- Use only facts visible in the tool outputs.
- Do not invent sources or URLs.
- If there are no meaningful signals, return an empty candidate_signals array.
- If a source call fails, record it in source_errors and continue.
- Keep summaries factual. Do not add Veeam guidance here.
- For 10-K / 10-Q / 8-K, you may emit a business signal with the filing type and date.
"""


def _aggregator_instructions() -> str:
    return """You are an internal Account Pulse aggregation agent.

You will receive normalized candidate signals from per-parent scan workers.

Return ONLY valid JSON:
{
  "signals": [
    {
      "account_name": "Company name for seller-facing heading",
      "parent_name": "Global ultimate parent",
      "tier": 1,
      "summary": "Factual sourced summary.",
      "source_name": "Publication or SEC",
      "source_url": "https://...",
      "published_at": "YYYY-MM-DD or null",
      "source_kind": "general_news|cyber_news|edgar",
      "signal_type": "news|cybersecurity|sec_filing",
      "supporting_accounts": ["..."],
      "relationship_context": {}
    }
  ]
}

Rules:
- Preserve source grounding exactly. Never invent or change URLs.
- Assign final tiers 1-4 using the summary and signal type.
- Keep one output signal per candidate input unless a candidate is clearly malformed.
- Summaries must remain factual, concise, and free of seller guidance.
"""


async def _run_worker_agent(
    client: AzureOpenAIResponsesClient,
    target: ScanTarget,
    *,
    source_mode: str,
    replay_fixture_set: dict[str, Any] | None,
) -> WorkerScanResult:
    if source_mode == "replay":
        tools = _build_replay_tools(replay_fixture_set or {}, target.parent_name)
    else:
        tools = [client.get_web_search_tool(), _build_live_edgar_tool()]

    worker = client.as_agent(
        name=f"AccountPulseWorker-{re.sub(r'[^A-Za-z0-9]+', '-', target.parent_name)[:48] or 'parent'}",
        description="Internal one-parent scan worker for Account Pulse.",
        instructions=_worker_instructions(target, source_mode=source_mode),
        tools=tools,
    )
    started_at = time.perf_counter()
    response = await run_with_rate_limit_retry(
        f"account-pulse-worker:{target.parent_name}",
        lambda: worker.run(f"Scan the assigned parent now: {target.parent_name}"),
    )
    timing_ms = (time.perf_counter() - started_at) * 1000.0
    payload = _loads_json(response.text)
    model = WorkerScanResult.model_validate(payload)
    model.timing_ms = timing_ms
    if not model.child_accounts:
        model.child_accounts = list(target.child_accounts)
    for signal in model.candidate_signals:
        if not signal.supporting_accounts:
            signal.supporting_accounts = list(target.child_accounts or [target.parent_name])
        if not signal.relationship_context:
            signal.relationship_context = dict(target.relationship_context)
    return model


async def _aggregate_signals_with_agent(
    client: AzureOpenAIResponsesClient,
    signals: Sequence[CandidateSignal],
) -> list[AggregatedSignal]:
    if not signals:
        return []
    aggregator = client.as_agent(
        name="AccountPulseSignalAggregator",
        description="Internal signal aggregator for Account Pulse.",
        instructions=_aggregator_instructions(),
        tools=[],
    )
    payload = {"signals": [signal.model_dump(mode="json") for signal in signals]}
    response = await run_with_rate_limit_retry(
        "account-pulse-aggregator",
        lambda: aggregator.run(_json_dumps(payload)),
    )
    parsed = _loads_json(response.text)
    signal_rows = parsed.get("signals", []) if isinstance(parsed, dict) else []
    return [AggregatedSignal.model_validate(row) for row in signal_rows]


def _fallback_aggregate(signals: Sequence[CandidateSignal]) -> list[AggregatedSignal]:
    aggregated: list[AggregatedSignal] = []
    for signal in signals:
        aggregated.append(
            AggregatedSignal(
                account_name=signal.account_name,
                parent_name=signal.parent_name,
                tier=signal.tier_hint or _tier_from_text(signal.summary),
                summary=signal.summary,
                source_name=signal.source_name,
                source_url=signal.source_url,
                published_at=signal.published_at,
                source_kind=signal.source_kind,
                signal_type=signal.signal_type,
                supporting_accounts=list(signal.supporting_accounts),
                relationship_context=dict(signal.relationship_context),
            )
        )
    return aggregated


async def run_scan_targets(
    client: AzureOpenAIResponsesClient,
    scan_targets: Sequence[dict[str, Any] | ScanTarget],
    *,
    execution_mode: str | None = None,
    source_mode: str | None = None,
    replay_fixture_set_name: str | None = None,
    max_concurrency: int | None = None,
    worker_runner: Callable[[ScanTarget], Any] | None = None,
    aggregator_runner: Callable[[Sequence[CandidateSignal]], Any] | None = None,
) -> ScanBundle:
    started = time.perf_counter()
    validated_targets = [
        target if isinstance(target, ScanTarget) else ScanTarget.model_validate(target)
        for target in scan_targets
    ]
    if not validated_targets:
        return ScanBundle(scan_targets_total=0, scan_targets_completed=0, scan_targets_failed=0)

    active_execution_mode = execution_mode or get_account_pulse_execution_mode()
    active_source_mode = source_mode or get_account_pulse_source_mode()
    active_max_concurrency = max_concurrency or get_account_pulse_max_concurrency()
    effective_concurrency = 1 if active_execution_mode == "legacy_sequential" else max(1, active_max_concurrency)
    active_model_concurrency = min(
        effective_concurrency,
        get_account_pulse_model_concurrency(),
    )
    active_model_concurrency = max(1, min(effective_concurrency, active_model_concurrency))
    fixture_name = replay_fixture_set_name or get_account_pulse_replay_fixture_set()
    replay_fixture_set = load_replay_fixture_set(fixture_name) if active_source_mode == "replay" else None

    semaphore = asyncio.Semaphore(effective_concurrency)
    model_semaphore = asyncio.Semaphore(active_model_concurrency)
    state = _ParallelRuntimeState()
    state_lock = asyncio.Lock()
    worker_results: list[WorkerScanResult] = []
    diagnostics: list[WorkerDiagnostic] = []
    malformed_worker_count = 0

    async def invoke_worker(target: ScanTarget) -> None:
        nonlocal malformed_worker_count
        async with semaphore:
            async with state_lock:
                state.current_concurrency += 1
                state.max_concurrency = max(state.max_concurrency, state.current_concurrency)
            try:
                if worker_runner is not None:
                    maybe_result = worker_runner(target)
                    result = await maybe_result if asyncio.iscoroutine(maybe_result) else maybe_result
                else:
                    async with model_semaphore:
                        result = await _run_worker_agent(
                            client,
                            target,
                            source_mode=active_source_mode,
                            replay_fixture_set=replay_fixture_set,
                        )
                model = result if isinstance(result, WorkerScanResult) else WorkerScanResult.model_validate(result)
                worker_results.append(model)
                diagnostics.append(
                    WorkerDiagnostic(
                        parent_name=target.parent_name,
                        status="completed",
                        signal_count=len(model.candidate_signals),
                        timing_ms=model.timing_ms,
                        source_errors=list(model.source_errors),
                    )
                )
            except ValidationError as exc:
                malformed_worker_count += 1
                diagnostics.append(
                    WorkerDiagnostic(
                        parent_name=target.parent_name,
                        status="malformed",
                        source_errors=[str(exc)],
                    )
                )
            except Exception as exc:
                diagnostics.append(
                    WorkerDiagnostic(
                        parent_name=target.parent_name,
                        status="failed",
                        source_errors=[str(exc)],
                    )
                )
            finally:
                async with state_lock:
                    state.current_concurrency -= 1

    await asyncio.gather(*(invoke_worker(target) for target in validated_targets))

    deduped_signals: list[CandidateSignal] = []
    seen_fingerprints: set[str] = set()
    duplicate_signal_count = 0
    quiet_accounts: list[str] = []
    completed_parents = {result.parent_name for result in worker_results}

    for result in worker_results:
        if not result.candidate_signals:
            quiet_accounts.append(result.parent_name)
        for signal in result.candidate_signals:
            fingerprint = _fingerprint_signal(signal)
            if fingerprint in seen_fingerprints:
                duplicate_signal_count += 1
                continue
            seen_fingerprints.add(fingerprint)
            deduped_signals.append(signal)

    deduped_signals.sort(
        key=lambda signal: (
            signal.published_at or "",
            signal.parent_name.lower(),
            signal.source_name.lower(),
            signal.source_url.lower(),
        ),
        reverse=True,
    )

    if get_account_pulse_internal_aggregator_enabled():
        try:
            if aggregator_runner is not None:
                maybe_result = aggregator_runner(deduped_signals)
                aggregated_result = await maybe_result if asyncio.iscoroutine(maybe_result) else maybe_result
                aggregated_signals = [
                    signal if isinstance(signal, AggregatedSignal) else AggregatedSignal.model_validate(signal)
                    for signal in aggregated_result
                ]
            else:
                aggregated_signals = await _aggregate_signals_with_agent(client, deduped_signals)
        except Exception:
            aggregated_signals = _fallback_aggregate(deduped_signals)
    else:
        aggregated_signals = _fallback_aggregate(deduped_signals)

    aggregated_signals.sort(
        key=lambda signal: (
            signal.tier,
            signal.published_at or "",
            signal.account_name.lower(),
        )
    )

    bundle = ScanBundle(
        scan_targets_total=len(validated_targets),
        scan_targets_completed=len(completed_parents),
        scan_targets_failed=len(validated_targets) - len(completed_parents),
        quiet_accounts=sorted(set(quiet_accounts)),
        signals=aggregated_signals,
        worker_diagnostics=diagnostics,
        max_observed_concurrency=state.max_concurrency,
        duplicate_signal_count=duplicate_signal_count,
        malformed_worker_count=malformed_worker_count,
    )
    logger.info(
        "Account Pulse parallel scan bundle created.",
        extra={
            "execution_mode": active_execution_mode,
            "source_mode": active_source_mode,
            "scan_targets_total": bundle.scan_targets_total,
            "scan_targets_completed": bundle.scan_targets_completed,
            "scan_targets_failed": bundle.scan_targets_failed,
            "signal_count": len(bundle.signals),
            "quiet_account_count": len(bundle.quiet_accounts),
            "max_observed_concurrency": bundle.max_observed_concurrency,
            "model_concurrency_limit": active_model_concurrency,
            "duplicate_signal_count": bundle.duplicate_signal_count,
            "malformed_worker_count": bundle.malformed_worker_count,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        },
    )
    return bundle


def build_scan_parents_parallel_tool(
    client: AzureOpenAIResponsesClient,
    *,
    execution_mode: str | None = None,
    source_mode: str | None = None,
    replay_fixture_set_name: str | None = None,
    max_concurrency: int | None = None,
):
    @tool(
        name="scan_parents_parallel",
        description=(
            "Run the internal Account Pulse parent scan for a typed array of scan targets. "
            "Each target must include parent_name, child_accounts, segment, relationship_context, "
            "and scan_mode. Returns one canonical structured scan bundle."
        ),
    )
    async def scan_parents_parallel(scan_targets: list[dict[str, Any]]) -> str:
        bundle = await run_scan_targets(
            client,
            scan_targets,
            execution_mode=execution_mode,
            source_mode=source_mode,
            replay_fixture_set_name=replay_fixture_set_name,
            max_concurrency=max_concurrency,
        )
        return _json_dumps(bundle.model_dump(mode="json"))

    return scan_parents_parallel
