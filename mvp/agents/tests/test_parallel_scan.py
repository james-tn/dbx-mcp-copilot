from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parallel_scan import (
    ScanBundle,
    WorkerScanResult,
    build_scan_parents_parallel_tool,
    load_replay_fixture_set,
    run_scan_targets,
)


def _aggregator_runner(signals):
    return [
        {
            "account_name": signal.account_name,
            "parent_name": signal.parent_name,
            "tier": signal.tier_hint or 3,
            "summary": signal.summary,
            "source_name": signal.source_name,
            "source_url": signal.source_url,
            "published_at": signal.published_at,
            "source_kind": signal.source_kind,
            "signal_type": signal.signal_type,
            "supporting_accounts": list(signal.supporting_accounts),
            "relationship_context": dict(signal.relationship_context),
        }
        for signal in signals
    ]


def test_load_replay_fixture_set_exposes_expected_scenarios() -> None:
    fixture_set = load_replay_fixture_set("small_parent_set")
    assert fixture_set["request"] == "Give me my morning briefing"
    assert len(fixture_set["scan_targets"]) == 2
    assert "Ford Motor Company" in fixture_set["sources"]


def test_run_scan_targets_legacy_mode_is_sequential() -> None:
    state = {"active": 0, "max": 0}

    async def worker_runner(target):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return {
            "parent_name": target.parent_name,
            "child_accounts": list(target.child_accounts),
            "candidate_signals": [
                {
                    "account_name": target.parent_name,
                    "parent_name": target.parent_name,
                    "summary": f"{target.parent_name} reported a quarterly update.",
                    "source_name": "Fixture News",
                    "source_url": f"https://example.test/{target.parent_name}",
                    "published_at": "2026-03-19",
                    "source_kind": "general_news",
                    "signal_type": "news",
                    "supporting_accounts": list(target.child_accounts),
                    "relationship_context": dict(target.relationship_context),
                    "tier_hint": 3,
                }
            ],
            "source_errors": [],
            "timing_ms": 10.0,
            "raw_sources_summary": {},
        }

    bundle = asyncio.run(
        run_scan_targets(
            client=None,
            scan_targets=[
                {"parent_name": "Parent A", "child_accounts": ["Account A"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
                {"parent_name": "Parent B", "child_accounts": ["Account B"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
                {"parent_name": "Parent C", "child_accounts": ["Account C"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
            ],
            execution_mode="legacy_sequential",
            max_concurrency=8,
            worker_runner=worker_runner,
            aggregator_runner=_aggregator_runner,
        )
    )

    assert state["max"] == 1
    assert bundle.max_observed_concurrency == 1
    assert bundle.scan_targets_completed == 3


def test_run_scan_targets_dynamic_parallel_respects_cap() -> None:
    state = {"active": 0, "max": 0}

    async def worker_runner(target):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return WorkerScanResult(
            parent_name=target.parent_name,
            child_accounts=list(target.child_accounts),
            candidate_signals=[],
            source_errors=[],
            timing_ms=10.0,
            raw_sources_summary={},
        )

    bundle = asyncio.run(
        run_scan_targets(
            client=None,
            scan_targets=[
                {"parent_name": f"Parent {i}", "child_accounts": [f"Account {i}"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"}
                for i in range(10)
            ],
            execution_mode="dynamic_parallel",
            max_concurrency=3,
            worker_runner=worker_runner,
            aggregator_runner=_aggregator_runner,
        )
    )

    assert state["max"] <= 3
    assert state["max"] >= 2
    assert bundle.max_observed_concurrency <= 3


def test_run_scan_targets_respects_model_concurrency_cap(monkeypatch) -> None:
    state = {"active": 0, "max": 0}

    async def fake_run_worker_agent(client, target, *, source_mode, replay_fixture_set):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return WorkerScanResult(
            parent_name=target.parent_name,
            child_accounts=list(target.child_accounts),
            candidate_signals=[],
            source_errors=[],
            timing_ms=10.0,
            raw_sources_summary={},
        )

    monkeypatch.setattr("parallel_scan._run_worker_agent", fake_run_worker_agent)
    monkeypatch.setattr("parallel_scan.get_account_pulse_model_concurrency", lambda: 2)

    bundle = asyncio.run(
        run_scan_targets(
            client=object(),
            scan_targets=[
                {"parent_name": f"Parent {i}", "child_accounts": [f"Account {i}"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"}
                for i in range(6)
            ],
            execution_mode="dynamic_parallel",
            max_concurrency=6,
            aggregator_runner=_aggregator_runner,
        )
    )

    assert state["max"] <= 2
    assert bundle.max_observed_concurrency <= 6


def test_run_scan_targets_dedupes_and_tracks_quiet_accounts() -> None:
    async def worker_runner(target):
        if target.parent_name == "Quiet Parent":
            return WorkerScanResult(
                parent_name=target.parent_name,
                child_accounts=list(target.child_accounts),
                candidate_signals=[],
                source_errors=[],
                timing_ms=5.0,
                raw_sources_summary={},
            )
        return {
            "parent_name": target.parent_name,
            "child_accounts": list(target.child_accounts),
            "candidate_signals": [
                {
                    "account_name": target.parent_name,
                    "parent_name": target.parent_name,
                    "summary": "Company reported cloud modernization plans.",
                    "source_name": "Fixture News",
                    "source_url": "https://example.test/shared-story",
                    "published_at": "2026-03-19",
                    "source_kind": "general_news",
                    "signal_type": "news",
                    "supporting_accounts": list(target.child_accounts),
                    "relationship_context": {},
                    "tier_hint": 2,
                },
                {
                    "account_name": target.parent_name,
                    "parent_name": target.parent_name,
                    "summary": "Company reported cloud modernization plans.",
                    "source_name": "Fixture News",
                    "source_url": "https://example.test/shared-story",
                    "published_at": "2026-03-19",
                    "source_kind": "general_news",
                    "signal_type": "news",
                    "supporting_accounts": list(target.child_accounts),
                    "relationship_context": {},
                    "tier_hint": 2,
                }
            ],
            "source_errors": [],
            "timing_ms": 10.0,
            "raw_sources_summary": {},
        }

    bundle = asyncio.run(
        run_scan_targets(
            client=None,
            scan_targets=[
                {"parent_name": "Noisy Parent", "child_accounts": ["Account A"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
                {"parent_name": "Another Parent", "child_accounts": ["Account B"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
                {"parent_name": "Quiet Parent", "child_accounts": ["Account C"], "segment": "ENT", "relationship_context": {}, "scan_mode": "full"},
            ],
            execution_mode="dynamic_parallel",
            max_concurrency=8,
            worker_runner=worker_runner,
            aggregator_runner=_aggregator_runner,
        )
    )

    assert bundle.duplicate_signal_count == 2
    assert bundle.quiet_accounts == ["Quiet Parent"]
    assert len(bundle.signals) == 2


def test_build_scan_parents_parallel_tool_serializes_bundle(monkeypatch) -> None:
    async def fake_run_scan_targets(*args: Any, **kwargs: Any) -> ScanBundle:
        return ScanBundle(
            scan_targets_total=1,
            scan_targets_completed=1,
            scan_targets_failed=0,
            quiet_accounts=[],
            signals=[],
            worker_diagnostics=[],
        )

    monkeypatch.setattr("parallel_scan.run_scan_targets", fake_run_scan_targets)

    tool = build_scan_parents_parallel_tool(client=None)
    raw = asyncio.run(
        tool.func(
            scan_targets=[
                {
                    "parent_name": "Test Parent",
                    "child_accounts": ["Child"],
                    "segment": "ENT",
                    "relationship_context": {},
                    "scan_mode": "full",
                }
            ]
        )
    )
    payload = json.loads(raw)
    assert payload["scan_targets_total"] == 1
    assert payload["scan_targets_completed"] == 1
