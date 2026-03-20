"""
Replay-backed local benchmark harness for Account Pulse execution modes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import tool

try:
    from .account_pulse import create_account_pulse_agent
    from .config import get_client
    from .parallel_scan import ScanBundle, load_replay_fixture_set, run_scan_targets
except ImportError:
    from account_pulse import create_account_pulse_agent
    from config import get_client
    from parallel_scan import ScanBundle, load_replay_fixture_set, run_scan_targets


@dataclass
class BenchmarkModeResult:
    execution_mode: str
    elapsed_ms: float
    scan_bundle: dict[str, Any]
    final_briefing: str
    metrics: dict[str, Any]


def _validate_briefing_format(text: str) -> dict[str, bool]:
    return {
        "has_header": "## Account Pulse" in text,
        "has_table": "| Threats | Changes | Business | Regulatory |" in text,
        "has_quiet_footer": "accounts had no new signals" in text or "Your accounts are quiet today." in text,
        "has_source_links": "(http" in text,
    }


def _measure_bundle_quality(bundle: dict[str, Any], fixture_set: dict[str, Any]) -> dict[str, Any]:
    signals = bundle.get("signals", [])
    diagnostics = bundle.get("worker_diagnostics", [])
    grounded = [
        signal
        for signal in signals
        if signal.get("source_name") and signal.get("source_url") and signal.get("summary")
    ]
    expected_min_signals = int(fixture_set.get("expected", {}).get("min_signals", 0))
    return {
        "worker_count": bundle.get("scan_targets_total", 0),
        "max_observed_concurrency": bundle.get("max_observed_concurrency", 0),
        "malformed_worker_count": bundle.get("malformed_worker_count", 0),
        "duplicate_signal_count": bundle.get("duplicate_signal_count", 0),
        "signal_count": len(signals),
        "grounded_signal_count": len(grounded),
        "source_grounding_compliance": len(grounded) == len(signals),
        "expected_signal_coverage": len(signals) >= expected_min_signals,
        "quiet_account_count": len(bundle.get("quiet_accounts", [])),
        "worker_failure_count": len([diag for diag in diagnostics if diag.get("status") != "completed"]),
    }


def _build_fixture_tools(fixture_set: dict[str, Any]):
    scope_payload = fixture_set["scope_payload"]
    top_opportunities_payload = fixture_set.get(
        "top_opportunities_payload",
        {
            "scope_mode": scope_payload.get("scope_mode", "demo"),
            "territory": scope_payload.get("territory"),
            "territories": scope_payload.get("territories", []),
            "segment": scope_payload.get("segment", "UNKNOWN"),
            "filter_mode": "velocity_candidates",
            "limit": 5,
            "offset": 0,
            "accounts": [],
        },
    )

    @tool(name="get_scoped_accounts", description="Replay fixture replacement for scoped account loading.")
    async def get_scoped_accounts_fixture() -> str:
        return json.dumps(scope_payload, ensure_ascii=False)

    @tool(name="get_top_opportunities", description="Replay fixture replacement for top opportunities.")
    async def get_top_opportunities_fixture(
        limit: int = 5,
        offset: int = 0,
        territory_override: str | None = None,
        filter_mode: str | None = None,
    ) -> str:
        payload = dict(top_opportunities_payload)
        payload["limit"] = limit
        payload["offset"] = offset
        payload["filter_mode"] = filter_mode or payload.get("filter_mode")
        payload["territory"] = territory_override or payload.get("territory")
        return json.dumps(payload, ensure_ascii=False)

    return get_scoped_accounts_fixture, get_top_opportunities_fixture


def _build_markdown_report(
    fixture_name: str,
    sequential: BenchmarkModeResult,
    parallel: BenchmarkModeResult,
) -> str:
    speedup_pct = 0.0
    if sequential.elapsed_ms > 0:
        speedup_pct = ((sequential.elapsed_ms - parallel.elapsed_ms) / sequential.elapsed_ms) * 100.0
    lines = [
        f"# Account Pulse Benchmark: {fixture_name}",
        "",
        "| Mode | Elapsed ms | Signals | Max concurrency | Worker failures | Format OK |",
        "|------|------------|---------|-----------------|-----------------|-----------|",
        (
            f"| {sequential.execution_mode} | {sequential.elapsed_ms:.1f} | "
            f"{sequential.metrics['signal_count']} | {sequential.metrics['max_observed_concurrency']} | "
            f"{sequential.metrics['worker_failure_count']} | "
            f"{all(sequential.metrics['output_format_compliance'].values())} |"
        ),
        (
            f"| {parallel.execution_mode} | {parallel.elapsed_ms:.1f} | "
            f"{parallel.metrics['signal_count']} | {parallel.metrics['max_observed_concurrency']} | "
            f"{parallel.metrics['worker_failure_count']} | "
            f"{all(parallel.metrics['output_format_compliance'].values())} |"
        ),
        "",
        f"Sequential to parallel speedup: {speedup_pct:.1f}%",
    ]
    return "\n".join(lines)


async def _run_mode(
    *,
    fixture_name: str,
    fixture_set: dict[str, Any],
    execution_mode: str,
) -> BenchmarkModeResult:
    client = get_client()
    scan_targets = fixture_set["scan_targets"]
    scoped_tool, top_opps_tool = _build_fixture_tools(fixture_set)

    started = time.perf_counter()
    bundle = await run_scan_targets(
        client,
        scan_targets,
        execution_mode=execution_mode,
        source_mode="replay",
        replay_fixture_set_name=fixture_name,
        max_concurrency=8,
    )
    scan_elapsed_ms = (time.perf_counter() - started) * 1000.0

    os.environ["ACCOUNT_PULSE_EXECUTION_MODE"] = execution_mode
    os.environ["ACCOUNT_PULSE_SOURCE_MODE"] = "replay"
    os.environ["ACCOUNT_PULSE_REPLAY_FIXTURE_SET"] = fixture_name

    agent = create_account_pulse_agent(
        client,
        scoped_accounts_tool=scoped_tool,
        top_opportunities_tool=top_opps_tool,
    )
    render_started = time.perf_counter()
    response = await agent.run(fixture_set.get("request", "Give me my morning briefing"))
    render_elapsed_ms = (time.perf_counter() - render_started) * 1000.0
    final_briefing = response.text

    bundle_payload = bundle.model_dump(mode="json")
    metrics = _measure_bundle_quality(bundle_payload, fixture_set)
    metrics["scan_elapsed_ms"] = scan_elapsed_ms
    metrics["render_elapsed_ms"] = render_elapsed_ms
    metrics["output_format_compliance"] = _validate_briefing_format(final_briefing)

    return BenchmarkModeResult(
        execution_mode=execution_mode,
        elapsed_ms=scan_elapsed_ms + render_elapsed_ms,
        scan_bundle=bundle_payload,
        final_briefing=final_briefing,
        metrics=metrics,
    )


async def _run_benchmark(fixture_name: str, output_dir: Path) -> dict[str, Any]:
    fixture_set = load_replay_fixture_set(fixture_name)
    sequential = await _run_mode(
        fixture_name=fixture_name,
        fixture_set=fixture_set,
        execution_mode="legacy_sequential",
    )
    parallel = await _run_mode(
        fixture_name=fixture_name,
        fixture_set=fixture_set,
        execution_mode="dynamic_parallel",
    )
    report = {
        "fixture_name": fixture_name,
        "sequential": {
            "execution_mode": sequential.execution_mode,
            "elapsed_ms": sequential.elapsed_ms,
            "metrics": sequential.metrics,
            "scan_bundle": sequential.scan_bundle,
            "final_briefing": sequential.final_briefing,
        },
        "parallel": {
            "execution_mode": parallel.execution_mode,
            "elapsed_ms": parallel.elapsed_ms,
            "metrics": parallel.metrics,
            "scan_bundle": parallel.scan_bundle,
            "final_briefing": parallel.final_briefing,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", fixture_name)
    (output_dir / f"{safe_name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / f"{safe_name}.md").write_text(
        _build_markdown_report(fixture_name, sequential, parallel),
        encoding="utf-8",
    )
    (output_dir / f"{safe_name}.sequential.txt").write_text(sequential.final_briefing, encoding="utf-8")
    (output_dir / f"{safe_name}.parallel.txt").write_text(parallel.final_briefing, encoding="utf-8")
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay-backed Account Pulse benchmark harness.")
    parser.add_argument(
        "--fixture-set",
        action="append",
        dest="fixture_sets",
        default=[],
        help="Fixture set name to run. Can be provided multiple times.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "benchmark-output"),
        help="Directory for JSON and markdown benchmark reports.",
    )
    args = parser.parse_args()

    fixture_sets = args.fixture_sets or [
        "small_parent_set",
        "medium_mixed_hierarchy",
        "large_gt_8_parents",
        "velocity_prefilter",
        "partial_failure",
    ]
    output_dir = Path(args.output_dir)
    summary: dict[str, Any] = {}
    for fixture_name in fixture_sets:
        print(f"Running replay benchmark for {fixture_name}...", file=sys.stderr)
        summary[fixture_name] = await _run_benchmark(fixture_name, output_dir)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "fixture_sets": fixture_sets}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
