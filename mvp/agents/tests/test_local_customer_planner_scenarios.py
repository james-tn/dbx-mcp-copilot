"""Local simulated customer-planner scenarios without real login or Databricks access."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import account_pulse
import customer_backend
import next_move
from auth_context import TokenClaims, bind_request_identity, reset_request_identity


class _FakeResolver:
    def __init__(self, territories: list[str] | None = None, *, error: Exception | None = None) -> None:
        self.territories = territories or []
        self.error = error

    async def resolve(self) -> list[str]:
        if self.error is not None:
            raise self.error
        return list(self.territories)


class _FakeRouter:
    def __init__(self, territories: list[str] | None = None, *, error: Exception | None = None) -> None:
        self.sales_team_resolver = _FakeResolver(territories, error=error)


class _FakeDatabricksClient:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def query_sql(self, statement: str, *, query_name: str = "unnamed") -> list[dict[str, object]]:
        self.statements.append(statement)
        return list(self.rows)


def _bind_identity(upn: str = "seller@example.com"):
    return bind_request_identity(
        "planner-user-token",
        TokenClaims(
            oid="user-123",
            tid="tenant-123",
            upn=upn,
            aud="api://planner-api",
            scp="access_as_user",
        ),
    )


def test_local_simulated_next_move_instructions_use_detected_territories(monkeypatch) -> None:
    token_refs = _bind_identity()
    try:
        monkeypatch.setattr(next_move, "get_customer_backend_enabled", lambda: True)
        monkeypatch.setattr(
            next_move,
            "get_customer_tool_backend_router",
            lambda: _FakeRouter(["Germany-ENT-Named-5", "UK-COM-Named-3"]),
        )

        instructions = asyncio.run(next_move.build_next_move_instructions_for_request())

        assert "currently resolves to these territories" in instructions
        assert "`Germany-ENT-Named-5`" in instructions
        assert "`UK-COM-Named-3`" in instructions
        assert "call `get_top_opportunities` with no `territory` argument" in instructions
        assert "comma-separated list of territories" in instructions
    finally:
        reset_request_identity(*token_refs)


def test_local_simulated_next_move_instructions_require_territory_when_none_detected(monkeypatch) -> None:
    token_refs = _bind_identity()
    try:
        monkeypatch.setattr(next_move, "get_customer_backend_enabled", lambda: True)
        monkeypatch.setattr(
            next_move,
            "get_customer_tool_backend_router",
            lambda: _FakeRouter(error=customer_backend.SalesTeamResolutionError("no mapping")),
        )

        instructions = asyncio.run(next_move.build_next_move_instructions_for_request())

        assert "No territories are currently resolved" in instructions
        assert "territory is mandatory" in instructions
    finally:
        reset_request_identity(*token_refs)


def test_local_simulated_top_opps_defaults_to_signed_in_scope(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_top_opportunities_source",
        lambda: "prod_catalog.data_science_account_iq_gold.account_iq_scores",
    )

    databricks_client = _FakeDatabricksClient(
        [
            {
                "account_id": "001DE0001",
                "account_name": "ERG S A",
                "company_name": "ERG S A",
                "sales_team": "Germany-ENT-Named-5",
                "xf_score_previous_day": 0.9,
            },
            {
                "account_id": "001UK0001",
                "account_name": "adidas AG",
                "company_name": "adidas AG",
                "sales_team": "UK-COM-Named-3",
                "xf_score_previous_day": 0.8,
            },
        ]
    )
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=_FakeResolver(["Germany-ENT-Named-5", "UK-COM-Named-3"]),
    )

    payload = asyncio.run(router.get_top_opportunities_payload(limit=5, offset=0, filter_mode=None))

    assert payload["territory"] is None
    assert payload["territories"] == ["Germany-ENT-Named-5", "UK-COM-Named-3"]
    assert payload["segment"] == "MIXED"
    assert "sales_team IN ('Germany-ENT-Named-5', 'UK-COM-Named-3')" in databricks_client.statements[0]


def test_local_simulated_top_opps_accepts_comma_separated_override(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_top_opportunities_source",
        lambda: "prod_catalog.data_science_account_iq_gold.account_iq_scores",
    )

    databricks_client = _FakeDatabricksClient(
        [
            {
                "account_id": "001DE0001",
                "account_name": "ERG S A",
                "company_name": "ERG S A",
                "sales_team": "Germany-ENT-Named-5",
                "xf_score_previous_day": 0.9,
            }
        ]
    )
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=_FakeResolver(["Ignored-ENT-1"]),
    )

    payload = asyncio.run(
        router.get_top_opportunities_payload(
            limit=5,
            offset=0,
            filter_mode=None,
            territory="Germany-ENT-Named-5, UK-COM-Named-3",
        )
    )

    assert payload["territories"] == ["Germany-ENT-Named-5", "UK-COM-Named-3"]
    assert "sales_team IN ('Germany-ENT-Named-5', 'UK-COM-Named-3')" in databricks_client.statements[0]


def test_local_simulated_account_pulse_empty_scope_message_is_specific() -> None:
    message = account_pulse._build_no_scoped_accounts_message(
        {
            "territory": "Germany-ENT-Named-5",
            "territories": ["Germany-ENT-Named-5"],
            "accounts": [],
        }
    )

    assert "no accounts in your current scope" in message
    assert "Territory scope: Germany-ENT-Named-5." in message
    assert "scoped account access and territory mapping" in message
