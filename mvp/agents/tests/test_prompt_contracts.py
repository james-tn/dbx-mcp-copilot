"""Prompt/runtime contract tests for the planner API Databricks path."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from account_pulse import (
    ACCOUNT_PULSE_INSTRUCTIONS,
    _build_parent_scan_targets,
    render_account_pulse_briefing_markdown,
)
from databricks_tools import get_account_contacts, get_scoped_accounts, get_top_opportunities, lookup_rep
from next_move import NEXT_MOVE_INSTRUCTIONS


def test_config_no_longer_exposes_direct_execute_sql() -> None:
    assert not hasattr(config, "execute_sql_tool")


def test_account_pulse_prompt_uses_semantic_tools() -> None:
    assert "SELECT name, global_ultimate" not in ACCOUNT_PULSE_INSTRUCTIONS
    assert "generate_account_pulse_briefing" in ACCOUNT_PULSE_INSTRUCTIONS
    assert "signed-in user's permitted enterprise data access" in ACCOUNT_PULSE_INSTRUCTIONS


def test_next_move_prompt_uses_semantic_tools() -> None:
    assert "SELECT * FROM veeam_demo.ri_secure.opportunities" not in NEXT_MOVE_INSTRUCTIONS
    assert "get_top_opportunities" in NEXT_MOVE_INSTRUCTIONS
    assert "get_account_contacts" in NEXT_MOVE_INSTRUCTIONS
    assert "What territory should I use?" in NEXT_MOVE_INSTRUCTIONS
    assert "territory string" in NEXT_MOVE_INSTRUCTIONS


def test_semantic_tool_names_are_stable() -> None:
    assert get_scoped_accounts.name == "get_scoped_accounts"
    assert lookup_rep.name == "lookup_rep"
    assert get_top_opportunities.name == "get_top_opportunities"
    assert get_account_contacts.name == "get_account_contacts"


def test_account_pulse_formatter_produces_required_sections() -> None:
    markdown = render_account_pulse_briefing_markdown(
        {
            "scan_targets_total": 1,
            "scan_targets_completed": 1,
            "scan_targets_failed": 0,
            "quiet_accounts": [],
            "signals": [
                {
                    "account_name": "Ford Motor Company",
                    "parent_name": "Ford Motor Company",
                    "tier": 1,
                    "summary": "Ford disclosed a supplier cyber incident that triggered a review of connected data-sharing controls.",
                    "source_name": "CyberDaily",
                    "source_url": "https://example.test/ford-cyber",
                    "published_at": "2026-03-19",
                    "source_kind": "cyber_news",
                    "signal_type": "cybersecurity",
                    "supporting_accounts": ["Ford Pro"],
                    "relationship_context": {
                        "customer_or_prospect": ["Customer"],
                        "current_veeam_products": ["VBR"],
                        "renewal_dates": ["2026-06-30"],
                        "opportunity_stages": ["Upsell"],
                        "last_seller_touch_dates": ["2026-03-01"],
                    },
                }
            ],
            "worker_diagnostics": [],
        },
        total_accounts=3,
        segment="ENT",
    )

    assert "## Account Pulse" in markdown
    assert "| Threats | Changes | Business | Regulatory |" in markdown
    assert "[[CyberDaily - 2026-03-19]](https://example.test/ford-cyber)" in markdown
    assert "accounts had no new signals" in markdown


def test_account_pulse_scan_target_builder_can_narrow_to_named_account() -> None:
    scan_targets, selected_account_count = _build_parent_scan_targets(
        {
            "segment": "ENT",
            "accounts": [
                {
                    "name": "Latitude AI LLC",
                    "global_ultimate": "Ford Motor Company",
                    "customer_or_prospect": "Customer",
                    "current_veeam_products": ["VBR"],
                    "renewal_date": "2026-06-30",
                    "opportunity_stage": "Upsell",
                    "last_seller_touch_date": "2026-03-01",
                },
                {
                    "name": "Ford Pro",
                    "global_ultimate": "Ford Motor Company",
                    "customer_or_prospect": "Customer",
                    "current_veeam_products": ["VBR"],
                    "renewal_date": "2026-06-30",
                    "opportunity_stage": "Upsell",
                    "last_seller_touch_date": "2026-03-01",
                },
                {
                    "name": "adidas North America",
                    "global_ultimate": "adidas AG",
                    "customer_or_prospect": "Prospect",
                },
            ],
        },
        request_text="can you provide full briefing on account Ford Pro",
    )

    assert selected_account_count == 1
    assert len(scan_targets) == 1
    assert scan_targets[0]["parent_name"] == "Ford Motor Company"
    assert scan_targets[0]["child_accounts"] == ["Ford Pro"]
