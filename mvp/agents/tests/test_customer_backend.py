"""Tests for the customer-mode backend adapters."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import customer_backend
from auth_context import TokenClaims


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeAsyncHttpClient:
    def __init__(self, response_payload: dict) -> None:
        self.response_payload = response_payload
        self.calls: list[dict] = []

    async def post(self, url: str, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self.response_payload)

    async def get(self, url: str):
        self.calls.append({"url": url, "method": "GET"})
        return _FakeResponse({"status": "OK"})

    async def aclose(self) -> None:
        return None


class _FakeDatabricksClient:
    def __init__(self, rows_by_statement: list[list[dict]]) -> None:
        self.rows_by_statement = list(rows_by_statement)
        self.statements: list[str] = []

    async def query_sql(self, statement: str) -> list[dict]:
        self.statements.append(statement)
        return self.rows_by_statement.pop(0)


class _CapturingDatabricksSqlClient:
    instances: list["_CapturingDatabricksSqlClient"] = []

    def __init__(self, settings, access_token=None) -> None:
        self.settings = settings
        self.access_token = access_token
        self.statements: list[str] = []
        self.__class__.instances.append(self)

    async def query_sql(self, statement: str) -> list[dict]:
        self.statements.append(statement)
        return [{"account_id": "001"}]

    async def close(self) -> None:
        return None


def test_sales_team_resolver_uses_static_map(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "ENT-APAC-01"}),
    )

    resolver = customer_backend.SalesTeamResolver(query_client=_FakeDatabricksClient([]))

    assert asyncio.run(resolver.resolve()) == "ENT-APAC-01"


def test_customer_dap_client_uses_authorization_header_for_obo(monkeypatch) -> None:
    fake_http = _FakeAsyncHttpClient(
        {
            "sales_team": "ENT-APAC-01",
            "row_count": 1,
            "rows": [{"account_id": "001", "account_name": "Contoso", "need": 0.8, "intent": 0.7, "xf_score": 0.9}],
        }
    )
    monkeypatch.setattr(customer_backend, "acquire_downstream_access_token", lambda *args, **kwargs: "dap-token")

    client = customer_backend.CustomerDapClient(
        settings=customer_backend.CustomerDapSettings(
            base_url="https://dap.example",
            accounts_query_path="/api/v1/accounts/query",
            healthcheck_path="/api/v1/healthcheck",
            debug_headers_path="/api/v1/debug/headers",
            auth_mode="obo",
            token_header_mode="authorization",
            scope="api://dap/access_as_user",
            timeout_seconds=30.0,
        ),
        http_client=fake_http,
    )

    payload = asyncio.run(client.query_accounts(sales_team="ENT-APAC-01", row_limit=5))

    assert payload["row_count"] == 1
    assert fake_http.calls[0]["headers"]["Authorization"] == "Bearer dap-token"
    assert "X-Forwarded-Access-Token" not in fake_http.calls[0]["headers"]


def test_customer_dap_client_can_send_forwarded_access_token(monkeypatch) -> None:
    fake_http = _FakeAsyncHttpClient({"sales_team": "ENT-APAC-01", "row_count": 0, "rows": []})
    monkeypatch.setattr(customer_backend, "get_request_user_assertion", lambda: "planner-user-token")

    client = customer_backend.CustomerDapClient(
        settings=customer_backend.CustomerDapSettings(
            base_url="https://dap.example",
            accounts_query_path="/api/v1/accounts/query",
            healthcheck_path="/api/v1/healthcheck",
            debug_headers_path="/api/v1/debug/headers",
            auth_mode="forward_user_token",
            token_header_mode="x_forwarded_access_token",
            scope="",
            timeout_seconds=30.0,
        ),
        http_client=fake_http,
    )

    asyncio.run(client.query_accounts(sales_team="ENT-APAC-01", row_limit=5))

    assert fake_http.calls[0]["headers"]["X-Forwarded-Access-Token"] == "planner-user-token"
    assert "Authorization" not in fake_http.calls[0]["headers"]


def test_customer_databricks_query_client_allows_dynamic_warehouse_resolution(monkeypatch) -> None:
    _CapturingDatabricksSqlClient.instances.clear()
    monkeypatch.setattr(customer_backend, "acquire_downstream_access_token", lambda *args, **kwargs: "dbx-token")
    monkeypatch.setattr(customer_backend, "DatabricksSqlClient", _CapturingDatabricksSqlClient)

    client = customer_backend.CustomerDatabricksQueryClient(
        settings=customer_backend.CustomerDatabricksQuerySettings(
            host="https://example.databricks.net",
            scope="scope",
            warehouse_id=None,
            azure_resource_id=None,
            pat=None,
        )
    )

    rows = asyncio.run(client.query_sql("SELECT 1"))

    assert rows == [{"account_id": "001"}]
    assert _CapturingDatabricksSqlClient.instances[0].access_token == "dbx-token"
    assert _CapturingDatabricksSqlClient.instances[0].settings.warehouse_id is None
    assert _CapturingDatabricksSqlClient.instances[0].statements == ["SELECT 1"]


def test_tool_backend_router_uses_direct_databricks_top_opportunities_source(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_catalog", lambda: "prod_catalog")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_schema", lambda: "data_science_account_iq_gold")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_table", lambda: "account_iq_scores")
    databricks_client = _FakeDatabricksClient(
        [
            [
                    {
                        "account_id": "001GL0001",
                        "account_name": "Ford Motor Company",
                        "company_name": "Ford Motor Company",
                        "sales_team": "GreatLakes-ENT-Named-1",
                        "xf_score_previous_day": 0.9,
                        "intent": 0.84,
                        "need": 0.92,
                    }
                ]
            ]
        )

    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(router.get_top_opportunities_payload(limit=5, offset=0, filter_mode=None))

    assert payload["territory"] == "GreatLakes-ENT-Named-1"
    assert payload["accounts"][0]["xf_score_previous_day"] == 0.9
    assert payload["accounts"][0]["company_name"] == "Ford Motor Company"
    assert "FROM prod_catalog.data_science_account_iq_gold.account_iq_scores" in databricks_client.statements[0]


def test_sales_team_resolver_can_build_source_from_catalog_schema_table(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_static_map_json", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_mapping_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_mapping_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_mapping_catalog", lambda: "demo_catalog")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_mapping_schema", lambda: "ri")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_mapping_table", lambda: "seller_mapping")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_user_column", lambda: "user_upn")
    monkeypatch.setattr(customer_backend, "get_customer_sales_team_column", lambda: "sales_team")

    query_client = _FakeDatabricksClient([[{"sales_team": "ENT-APAC-01"}]])
    resolver = customer_backend.SalesTeamResolver(query_client=query_client)

    assert asyncio.run(resolver.resolve()) == "ENT-APAC-01"
    assert "FROM demo_catalog.ri.seller_mapping" in query_client.statements[0]


def test_rep_lookup_resolver_supports_exact_partial_and_ambiguous(monkeypatch) -> None:
    monkeypatch.setattr(
        customer_backend,
        "get_customer_rep_lookup_static_map_json",
        lambda: json.dumps(
            {
                "Scott Jackson": "Germany-ENT-Named-5",
                "Scott Jones": "GreatLakes-ENT-Named-1",
                "Jon Test": "NewYorkNewJersey-ENT-Named-2",
            }
        ),
    )

    resolver = customer_backend.RepLookupResolver()

    exact = resolver.resolve("Jon Test")
    assert exact["status"] == "ok"
    assert exact["match_type"] == "exact"
    assert exact["territory"] == "NewYorkNewJersey-ENT-Named-2"

    partial = resolver.resolve("Test")
    assert partial["status"] == "ok"
    assert partial["match_type"] == "partial"

    ambiguous = resolver.resolve("Scott")
    assert ambiguous["status"] == "ambiguous"
    assert len(ambiguous["matches"]) == 2


def test_rep_lookup_resolver_returns_available_reps_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        customer_backend,
        "get_customer_rep_lookup_static_map_json",
        lambda: json.dumps({"Jon Test": "NewYorkNewJersey-ENT-Named-2"}),
    )

    payload = customer_backend.RepLookupResolver().resolve("Nope")

    assert payload["status"] == "no_match"
    assert payload["available_reps"] == ["Jon Test"]


def test_scoped_accounts_can_build_source_from_catalog_schema_table(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_catalog", lambda: "demo_catalog")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_schema", lambda: "ri_secure")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_table", lambda: "accounts")

    databricks_client = _FakeDatabricksClient(
        [[{"account_id": "001", "name": "Contoso", "global_ultimate": "Contoso", "sales_team": "GreatLakes-ENT-Named-1"}]]
    )
    router = customer_backend.ToolBackendRouter(
        dap_client=customer_backend.CustomerDapClient(
            settings=customer_backend.CustomerDapSettings(
                base_url="https://dap.example",
                accounts_query_path="/api/v1/accounts/query",
                healthcheck_path="/api/v1/healthcheck",
                debug_headers_path="/api/v1/debug/headers",
                auth_mode="obo",
                token_header_mode="authorization",
                scope="api://dap/access_as_user",
                timeout_seconds=30.0,
            ),
            http_client=_FakeAsyncHttpClient({"sales_team": "GreatLakes-ENT-Named-1", "row_count": 0, "rows": []}),
        ),
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(router.get_scoped_accounts_payload())

    assert payload["total_accounts"] == 1
    assert "FROM demo_catalog.ri_secure.accounts" in databricks_client.statements[0]


def test_scoped_accounts_prefers_static_json_path(monkeypatch, tmp_path: Path) -> None:
    scoped_accounts_path = tmp_path / "scope_accounts.json"
    scoped_accounts_path.write_text(
        json.dumps(
            [
                {
                    "account_id": "0016000000M33UPAAZ",
                    "source_vpower_id": "001cx00000PLCLkAAP",
                    "legacy_id": "0016000000M33UPAAZ",
                    "name": "BLUE CROSS AND BLUE SHIELD OF NORTH CAROLINA SENIOR HEALTH",
                    "global_ultimate": "BLUE CROSS AND BLUE SHIELD OF NORTH CAROLINA SENIOR HEALTH",
                    "sales_team": "GreatLakes-ENT-Named-1",
                    "duns": "11001296",
                    "is_subsidiary": False,
                    "industry": None,
                    "sic_or_naics": None,
                    "hq_country": None,
                    "hq_region": None,
                    "customer_or_prospect": None,
                    "current_veeam_products": None,
                    "renewal_date": None,
                    "opportunity_stage": None,
                    "last_seller_touch_date": None,
                },
                {
                    "account_id": "other",
                    "name": "Other Account",
                    "global_ultimate": "Other Account",
                    "sales_team": "Germany-ENT-Named-5",
                },
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(
        customer_backend,
        "get_customer_scope_accounts_static_json_path",
        lambda: str(scoped_accounts_path),
    )
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_catalog", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_schema", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_scope_accounts_table", lambda: "")

    databricks_client = _FakeDatabricksClient([])
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(router.get_scoped_accounts_payload())

    assert payload["total_accounts"] == 1
    assert payload["accounts"][0]["account_id"] == "0016000000M33UPAAZ"
    assert payload["accounts"][0]["source_vpower_id"] == "001cx00000PLCLkAAP"
    assert payload["accounts"][0]["industry"] is None
    assert databricks_client.statements == []


def test_top_opportunities_prefers_explicit_territory_and_direct_databricks_source(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_catalog", lambda: "prod_catalog")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_schema", lambda: "data_science_account_iq_gold")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_table", lambda: "account_iq_scores")

    databricks_client = _FakeDatabricksClient(
        [[{"account_id": "001", "account_name": "Contoso", "company_name": "Contoso", "sales_team": "Germany-ENT-Named-5", "xf_score_previous_day": 90.0}]]
    )
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(
        router.get_top_opportunities_payload(limit=5, offset=0, filter_mode=None, territory="Germany-ENT-Named-5")
    )

    assert payload["territory"] == "Germany-ENT-Named-5"
    assert payload["accounts"][0]["xf_score_previous_day"] == 90.0
    assert "FROM prod_catalog.data_science_account_iq_gold.account_iq_scores" in databricks_client.statements[0]
    assert "sales_team = 'Germany-ENT-Named-5'" in databricks_client.statements[0]


def test_account_contacts_uses_domain_account_id(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_contacts_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_contacts_source", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_contacts_catalog", lambda: "prod_catalog")
    monkeypatch.setattr(customer_backend, "get_customer_contacts_schema", lambda: "account_iq_gold")
    monkeypatch.setattr(customer_backend, "get_customer_contacts_table", lambda: "aiq_contact")

    databricks_client = _FakeDatabricksClient([[{"domain_account_id": "001", "name": "Alice"}]])
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(router.get_account_contacts_payload("001"))

    assert payload["contacts"][0]["domain_account_id"] == "001"
    assert "WHERE domain_account_id = '001'" in databricks_client.statements[0]


def test_top_opportunities_supports_velocity_candidate_ordering(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_source", lambda: "demo_catalog.ri_secure.opportunities")

    databricks_client = _FakeDatabricksClient(
        [[{"account_id": "001", "account_name": "Contoso", "company_name": "Contoso", "sales_team": "GreatLakes-ENT-Named-1", "xf_score_previous_day": 90.0}]]
    )
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    payload = asyncio.run(
        router.get_top_opportunities_payload(limit=5, offset=0, filter_mode="velocity_candidates")
    )

    assert payload["filter_mode"] == "velocity_candidates"
    assert "coalesce(intent, 0) DESC" in databricks_client.statements[0]
    assert "coalesce(xf_score_diff_pct, 0) DESC" in databricks_client.statements[0]


def test_top_opportunities_supports_new_logo_only_filter(monkeypatch) -> None:
    monkeypatch.setattr(customer_backend, "get_request_user_upn", lambda: "seller@example.com")
    monkeypatch.setattr(
        customer_backend,
        "get_customer_sales_team_static_map_json",
        lambda: json.dumps({"seller@example.com": "GreatLakes-ENT-Named-1"}),
    )
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_query", lambda: "")
    monkeypatch.setattr(customer_backend, "get_customer_top_opportunities_source", lambda: "demo_catalog.ri_secure.opportunities")

    databricks_client = _FakeDatabricksClient(
        [[{"account_id": "001", "account_name": "Contoso", "company_name": "Contoso", "sales_team": "GreatLakes-ENT-Named-1", "xf_score_previous_day": 90.0}]]
    )
    router = customer_backend.ToolBackendRouter(
        databricks_client=databricks_client,
        sales_team_resolver=customer_backend.SalesTeamResolver(databricks_client),
    )

    asyncio.run(router.get_top_opportunities_payload(limit=5, offset=0, filter_mode="new_logo_only"))

    assert "coalesce(sales_play_sell_vdp, false)" in databricks_client.statements[0]
    assert "coalesce(sales_play_vmware_migration, false)" in databricks_client.statements[0]
