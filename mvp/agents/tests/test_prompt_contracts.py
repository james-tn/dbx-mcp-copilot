"""Prompt/runtime contract tests for the planner API Databricks path."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from account_pulse import ACCOUNT_PULSE_INSTRUCTIONS
from databricks_tools import get_account_contacts, get_scoped_accounts, get_top_opportunities, lookup_rep
from next_move import NEXT_MOVE_INSTRUCTIONS


def test_config_no_longer_exposes_direct_execute_sql() -> None:
    assert not hasattr(config, "execute_sql_tool")


def test_account_pulse_prompt_uses_semantic_tools() -> None:
    assert "SELECT name, global_ultimate" not in ACCOUNT_PULSE_INSTRUCTIONS
    assert "get_scoped_accounts" in ACCOUNT_PULSE_INSTRUCTIONS
    assert "get_top_opportunities" in ACCOUNT_PULSE_INSTRUCTIONS
    assert "signed-in user's Databricks access" in ACCOUNT_PULSE_INSTRUCTIONS


def test_next_move_prompt_uses_semantic_tools() -> None:
    assert "SELECT * FROM veeam_demo.ri_secure.opportunities" not in NEXT_MOVE_INSTRUCTIONS
    assert "get_top_opportunities" in NEXT_MOVE_INSTRUCTIONS
    assert "get_account_contacts" in NEXT_MOVE_INSTRUCTIONS
    assert "signed-in user's access to Databricks secure views" in NEXT_MOVE_INSTRUCTIONS


def test_semantic_tool_names_are_stable() -> None:
    assert get_scoped_accounts.name == "get_scoped_accounts"
    assert lookup_rep.name == "lookup_rep"
    assert get_top_opportunities.name == "get_top_opportunities"
    assert get_account_contacts.name == "get_account_contacts"
