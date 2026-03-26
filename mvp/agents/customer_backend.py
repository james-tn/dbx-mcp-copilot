"""
Customer-backend adapters for the Daily Account Planner.

The active hosted customer path now uses direct Databricks for ranked account
retrieval and contacts, while Account Pulse can also source scoped accounts
from a checked-in static JSON file. DAP helpers remain in-repo for future use,
but they are not required by the active secure runtime path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

try:
    from .auth_context import (
        acquire_downstream_access_token,
        get_request_user_assertion,
        get_request_user_upn,
    )
    from .config import (
        get_customer_contacts_query,
        get_customer_contacts_source,
        get_customer_contacts_catalog,
        get_customer_contacts_schema,
        get_customer_contacts_table,
        get_customer_databricks_host,
        get_customer_databricks_pat,
        get_customer_databricks_resource_id,
        get_customer_databricks_scope,
        get_customer_rep_lookup_static_map_json,
        get_customer_databricks_warehouse_id,
        get_customer_sales_team_column,
        get_customer_sales_team_mapping_catalog,
        get_customer_sales_team_mapping_query,
        get_customer_sales_team_mapping_schema,
        get_customer_sales_team_mapping_source,
        get_customer_sales_team_mapping_table,
        get_customer_sales_team_static_map_json,
        get_customer_sales_team_user_column,
        get_customer_scope_accounts_catalog,
        get_customer_scope_accounts_query,
        get_customer_scope_accounts_schema,
        get_customer_scope_accounts_static_json_path,
        get_customer_scope_accounts_source,
        get_customer_scope_accounts_table,
        get_customer_top_opportunities_catalog,
        get_customer_top_opportunities_query,
        get_customer_top_opportunities_schema,
        get_customer_top_opportunities_source,
        get_customer_top_opportunities_table,
        get_dap_accounts_query_path,
        get_dap_api_auth_mode,
        get_dap_api_base_url,
        get_dap_api_scope,
        get_dap_api_token_header_mode,
        get_dap_debug_headers_path,
        get_dap_healthcheck_path,
        get_openai_timeout_seconds,
    )
    from .databricks_sql import (
        DatabricksSqlAuthError,
        DatabricksSqlClient,
        DatabricksSqlError,
        DatabricksSqlSettings,
    )
except ImportError:
    from auth_context import acquire_downstream_access_token, get_request_user_assertion, get_request_user_upn
    from config import (
        get_customer_contacts_query,
        get_customer_contacts_source,
        get_customer_contacts_catalog,
        get_customer_contacts_schema,
        get_customer_contacts_table,
        get_customer_databricks_host,
        get_customer_databricks_pat,
        get_customer_databricks_resource_id,
        get_customer_databricks_scope,
        get_customer_rep_lookup_static_map_json,
        get_customer_databricks_warehouse_id,
        get_customer_sales_team_column,
        get_customer_sales_team_mapping_catalog,
        get_customer_sales_team_mapping_query,
        get_customer_sales_team_mapping_schema,
        get_customer_sales_team_mapping_source,
        get_customer_sales_team_mapping_table,
        get_customer_sales_team_static_map_json,
        get_customer_sales_team_user_column,
        get_customer_scope_accounts_catalog,
        get_customer_scope_accounts_query,
        get_customer_scope_accounts_schema,
        get_customer_scope_accounts_static_json_path,
        get_customer_scope_accounts_source,
        get_customer_scope_accounts_table,
        get_customer_top_opportunities_catalog,
        get_customer_top_opportunities_query,
        get_customer_top_opportunities_schema,
        get_customer_top_opportunities_source,
        get_customer_top_opportunities_table,
        get_dap_accounts_query_path,
        get_dap_api_auth_mode,
        get_dap_api_base_url,
        get_dap_api_scope,
        get_dap_api_token_header_mode,
        get_dap_debug_headers_path,
        get_dap_healthcheck_path,
        get_openai_timeout_seconds,
    )
    from databricks_sql import (
        DatabricksSqlAuthError,
        DatabricksSqlClient,
        DatabricksSqlError,
        DatabricksSqlSettings,
    )


class CustomerBackendConfigurationError(RuntimeError):
    """Raised when customer-backend configuration is incomplete."""


class CustomerDataAccessError(RuntimeError):
    """Raised when the customer data sources cannot be reached."""


class SalesTeamResolutionError(RuntimeError):
    """Raised when the planner cannot resolve the signed-in user's sales team."""


class RepLookupConfigurationError(RuntimeError):
    """Raised when hosted rep lookup configuration is invalid."""


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _normalize_string(value: Any) -> str:
    return str(value or "").strip()


def _normalize_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [_normalize_string(item) for item in value if _normalize_string(item)]
    normalized = _normalize_string(value)
    return [normalized] if normalized else []


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _nullable_string(value: Any) -> str | None:
    normalized = _normalize_string(value)
    return normalized or None


def _emit_backend_log(message: str) -> None:
    print(message, flush=True)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _normalize_string(value).lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _segment_from_territory(territory: str) -> str:
    upper = territory.upper()
    if "-ENT-" in upper:
        return "ENT"
    if "-COM-" in upper:
        return "COM"
    if "-VEL-" in upper:
        return "VEL"
    return "UNKNOWN"


def _render_sql_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", _escape_sql(value))
    return rendered


def _join_source_parts(*parts: str) -> str:
    normalized = [part.strip() for part in parts if part and part.strip()]
    return ".".join(normalized)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mvp_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_local_data_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    for root in (Path.cwd(), _mvp_root()):
        candidate = (root / path).resolve()
        if candidate.exists():
            return candidate
    return (_mvp_root() / path).resolve()


def _normalize_scoped_account_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": _normalize_string(row.get("account_id") or row.get("legacy_id")),
        "source_vpower_id": _nullable_string(row.get("source_vpower_id")),
        "legacy_id": _nullable_string(row.get("legacy_id") or row.get("account_id")),
        "name": _normalize_string(row.get("name")),
        "global_ultimate": _nullable_string(row.get("global_ultimate")) or _normalize_string(row.get("name")),
        "sales_team": _normalize_string(row.get("sales_team")),
        "duns": _nullable_string(row.get("duns")),
        "is_subsidiary": _coerce_bool(row.get("is_subsidiary")),
        "industry": _nullable_string(row.get("industry")),
        "sic_or_naics": _nullable_string(row.get("sic_or_naics")),
        "hq_country": _nullable_string(row.get("hq_country")),
        "hq_region": _nullable_string(row.get("hq_region")),
        "customer_or_prospect": _nullable_string(row.get("customer_or_prospect")),
        "current_veeam_products": _nullable_string(row.get("current_veeam_products")),
        "renewal_date": _nullable_string(row.get("renewal_date")),
        "opportunity_stage": _nullable_string(row.get("opportunity_stage")),
        "last_seller_touch_date": _nullable_string(row.get("last_seller_touch_date")),
    }


def _load_static_scoped_accounts(path_value: str, *, sales_team: str) -> list[dict[str, Any]]:
    resolved_path = _resolve_local_data_path(path_value)
    if not resolved_path.exists():
        raise CustomerBackendConfigurationError(
            f"CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH does not exist: {resolved_path}"
        )

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CustomerBackendConfigurationError(
            f"CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH is not valid JSON: {resolved_path}"
        ) from exc

    if isinstance(payload, dict):
        rows = payload.get("accounts")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise CustomerBackendConfigurationError(
            "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH must contain a JSON array or an object with an 'accounts' array."
        )

    normalized_rows = [
        _normalize_scoped_account_row(row)
        for row in rows
        if isinstance(row, dict)
    ]
    filtered_rows = [
        row for row in normalized_rows if row["account_id"] and row["name"] and row["sales_team"] == sales_team
    ]
    return sorted(
        filtered_rows,
        key=lambda row: (
            row["sales_team"].lower(),
            (row.get("global_ultimate") or "").lower(),
            row["name"].lower(),
        ),
    )


@dataclass(frozen=True)
class CustomerDapSettings:
    base_url: str
    accounts_query_path: str
    healthcheck_path: str
    debug_headers_path: str
    auth_mode: str
    token_header_mode: str
    scope: str
    timeout_seconds: float


@dataclass(frozen=True)
class CustomerDatabricksQuerySettings:
    host: str
    scope: str
    warehouse_id: str | None
    azure_resource_id: str | None
    pat: str | None


def load_customer_dap_settings() -> CustomerDapSettings:
    return CustomerDapSettings(
        base_url=get_dap_api_base_url(),
        accounts_query_path=get_dap_accounts_query_path(),
        healthcheck_path=get_dap_healthcheck_path(),
        debug_headers_path=get_dap_debug_headers_path(),
        auth_mode=get_dap_api_auth_mode(),
        token_header_mode=get_dap_api_token_header_mode(),
        scope=get_dap_api_scope(),
        timeout_seconds=get_openai_timeout_seconds(),
    )


def load_customer_databricks_query_settings() -> CustomerDatabricksQuerySettings:
    return CustomerDatabricksQuerySettings(
        host=get_customer_databricks_host(),
        scope=get_customer_databricks_scope(),
        warehouse_id=get_customer_databricks_warehouse_id(),
        azure_resource_id=get_customer_databricks_resource_id() or None,
        pat=get_customer_databricks_pat() or None,
    )


class CustomerDapClient:
    def __init__(
        self,
        settings: CustomerDapSettings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or load_customer_dap_settings()
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(timeout=self.settings.timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self.http_client.aclose()

    def _build_auth_headers(self, access_token: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.token_header_mode in {"authorization", "both"}:
            headers["Authorization"] = f"Bearer {access_token}"
        if self.settings.token_header_mode in {"x_forwarded_access_token", "both"}:
            headers["X-Forwarded-Access-Token"] = access_token
        return headers

    def _resolve_access_token(self) -> str:
        if self.settings.auth_mode == "forward_user_token":
            access_token = _normalize_string(get_request_user_assertion())
            if not access_token:
                raise CustomerDataAccessError(
                    "DAP token forwarding is enabled, but no signed-in user token is available."
                )
            return access_token

        scope = _normalize_string(self.settings.scope)
        if not scope:
            raise CustomerBackendConfigurationError(
                "DAP_API_SCOPE is required when DAP_API_AUTH_MODE=obo."
            )
        access_token = acquire_downstream_access_token(
            scope,
            default_message="DAP API OBO token acquisition failed.",
        )
        if not access_token:
            raise CustomerDataAccessError(
                "The DAP API is temporarily unavailable because the planner could not acquire a user token."
            )
        return access_token

    async def healthcheck(self) -> dict[str, Any]:
        if not self.settings.base_url:
            raise CustomerBackendConfigurationError("DAP_API_BASE_URL is required for customer mode.")
        response = await self.http_client.get(f"{self.settings.base_url}{self.settings.healthcheck_path}")
        response.raise_for_status()
        return response.json()

    async def query_accounts(self, *, sales_team: str, row_limit: int) -> dict[str, Any]:
        if not self.settings.base_url:
            raise CustomerBackendConfigurationError("DAP_API_BASE_URL is required for customer mode.")

        access_token = self._resolve_access_token()
        response = await self.http_client.post(
            f"{self.settings.base_url}{self.settings.accounts_query_path}",
            headers=self._build_auth_headers(access_token),
            json={"sales_team": sales_team, "row_limit": row_limit},
        )
        if response.status_code in {401, 403}:
            raise CustomerDataAccessError(
                "The DAP API rejected the signed-in user's access."
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CustomerDataAccessError("The DAP API returned an unexpected response payload.")
        return payload


class CustomerDatabricksQueryClient:
    def __init__(self, settings: CustomerDatabricksQuerySettings | None = None) -> None:
        self.settings = settings or load_customer_databricks_query_settings()

    async def query_sql(self, statement: str, *, query_name: str = "unnamed") -> list[dict[str, Any]]:
        if not self.settings.host:
            raise CustomerBackendConfigurationError(
                "CUSTOMER_DATABRICKS_HOST or DATABRICKS_HOST is required for customer mode."
            )

        access_token = acquire_downstream_access_token(
            self.settings.scope,
            default_message="Customer Databricks OBO token acquisition failed.",
        )
        if not access_token and not self.settings.pat:
            _emit_backend_log(
                f"[customer-databricks] query={query_name} token_status=missing auth_mode=obo"
            )
            raise CustomerDataAccessError(
                "The customer Databricks source is temporarily unavailable because the planner could not acquire a user token."
            )

        auth_mode = "pat" if self.settings.pat else "obo"
        host_label = self.settings.host.replace("https://", "").replace("http://", "").rstrip("/")
        warehouse_label = self.settings.warehouse_id or "<auto>"
        _emit_backend_log(
            f"[customer-databricks] query={query_name} status=start auth_mode={auth_mode} "
            f"host={host_label} warehouse_id={warehouse_label}"
        )

        client = DatabricksSqlClient(
            settings=DatabricksSqlSettings(
                host=self.settings.host,
                token_scope=self.settings.scope,
                azure_management_scope="https://management.core.windows.net//.default",
                azure_workspace_resource_id=self.settings.azure_resource_id,
                warehouse_id=self.settings.warehouse_id,
                timeout_seconds=30.0,
                retry_count=1,
                poll_attempts=6,
                poll_interval_seconds=1.0,
                pat=self.settings.pat,
            ),
            access_token=access_token,
        )
        try:
            rows = await client.query_sql(statement)
            _emit_backend_log(
                f"[customer-databricks] query={query_name} status=success row_count={len(rows)}"
            )
            return rows
        except DatabricksSqlAuthError as exc:
            _emit_backend_log(
                f"[customer-databricks] query={query_name} status=auth_error detail={str(exc)[:300]}"
            )
            raise CustomerDataAccessError(
                "The customer Databricks source rejected the signed-in user's access."
            ) from exc
        except DatabricksSqlError as exc:
            _emit_backend_log(
                f"[customer-databricks] query={query_name} status=error detail={str(exc)[:300]}"
            )
            raise CustomerDataAccessError(
                "The customer Databricks source is temporarily unavailable."
            ) from exc
        except Exception as exc:
            _emit_backend_log(
                f"[customer-databricks] query={query_name} status=unexpected_error detail={type(exc).__name__}: {str(exc)[:300]}"
            )
            raise
        finally:
            await client.close()


class SalesTeamResolver:
    def __init__(self, query_client: CustomerDatabricksQueryClient | None = None) -> None:
        self.query_client = query_client or CustomerDatabricksQueryClient()

    async def resolve(self) -> str:
        user_upn = _normalize_string(get_request_user_upn())
        if not user_upn:
            raise SalesTeamResolutionError(
                "The signed-in user identity is missing an email/UPN claim, so sales-team mapping cannot be resolved."
            )

        sales_teams = self._resolve_from_static_map(user_upn)
        if sales_teams is None:
            sales_teams = await self._resolve_from_databricks(user_upn)

        normalized = sorted({item for item in (sales_teams or []) if item})
        if not normalized:
            raise SalesTeamResolutionError(
                f"No sales-team mapping is configured for signed-in user '{user_upn}'."
            )
        if len(normalized) > 1:
            raise SalesTeamResolutionError(
                f"Signed-in user '{user_upn}' maps to multiple sales teams ({', '.join(normalized)}). "
                "The current DAP integration requires a single resolved sales team."
            )
        return normalized[0]

    def _resolve_from_static_map(self, user_upn: str) -> list[str] | None:
        raw_json = get_customer_sales_team_static_map_json()
        if not raw_json:
            return None
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise CustomerBackendConfigurationError(
                "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON is not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise CustomerBackendConfigurationError(
                "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON must be a JSON object keyed by user UPN."
            )
        entry = payload.get(user_upn) or payload.get(user_upn.lower())
        if entry is None:
            return []
        return _normalize_string_list(entry)

    async def _resolve_from_databricks(self, user_upn: str) -> list[str]:
        configured_query = get_customer_sales_team_mapping_query()
        if configured_query:
            _emit_backend_log(
                f"[customer-backend] sales-team-mapping mode=custom_query user_upn={user_upn}"
            )
            statement = _render_sql_template(configured_query, {"user_upn": user_upn})
        else:
            source = get_customer_sales_team_mapping_source() or _join_source_parts(
                get_customer_sales_team_mapping_catalog(),
                get_customer_sales_team_mapping_schema(),
                get_customer_sales_team_mapping_table(),
            )
            if not source:
                raise CustomerBackendConfigurationError(
                    "Configure CUSTOMER_SALES_TEAM_MAPPING_QUERY, CUSTOMER_SALES_TEAM_MAPPING_SOURCE, "
                    "or CUSTOMER_SALES_TEAM_STATIC_MAP_JSON for customer mode."
                )
            statement = (
                "SELECT "
                f"{get_customer_sales_team_column()} AS sales_team "
                f"FROM {source} "
                f"WHERE LOWER({get_customer_sales_team_user_column()}) = LOWER('{{{{user_upn}}}}') "
                "ORDER BY sales_team"
            )
            statement = _render_sql_template(statement, {"user_upn": user_upn})
            _emit_backend_log(
                f"[customer-backend] sales-team-mapping mode=source user_upn={user_upn} source={source}"
            )

        rows = await self.query_client.query_sql(statement, query_name="sales_team_mapping")
        return [
            _normalize_string(row.get("sales_team"))
            for row in rows
            if _normalize_string(row.get("sales_team"))
        ]


class RepLookupResolver:
    def resolve(self, rep_name: str) -> dict[str, Any]:
        name_query = _normalize_string(rep_name)
        if not name_query:
            return {"status": "error", "message": "rep_name is required."}

        raw_json = get_customer_rep_lookup_static_map_json()
        if not raw_json:
            return {
                "status": "error",
                "message": "Rep lookup is not configured. Set CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON.",
            }
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise RepLookupConfigurationError(
                "CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON is not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RepLookupConfigurationError(
                "CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON must be a JSON object keyed by rep name."
            )

        records: list[tuple[str, str]] = []
        for rep, territory in payload.items():
            rep_value = _normalize_string(rep)
            territory_value = _normalize_string(territory)
            if rep_value and territory_value:
                records.append((rep_value, territory_value))

        lowered = name_query.lower()
        exact = [(rep, territory) for rep, territory in records if rep.lower() == lowered]
        if len(exact) == 1:
            rep, territory = exact[0]
            return {"status": "ok", "rep": rep, "territory": territory, "match_type": "exact"}

        partial = [(rep, territory) for rep, territory in records if lowered in rep.lower()]
        if len(partial) == 1:
            rep, territory = partial[0]
            return {"status": "ok", "rep": rep, "territory": territory, "match_type": "partial"}
        if len(partial) > 1:
            return {
                "status": "ambiguous",
                "query": rep_name,
                "matches": [
                    {"rep": rep, "territory": territory}
                    for rep, territory in sorted(partial, key=lambda item: item[0].lower())
                ],
            }

        return {
            "status": "no_match",
            "query": rep_name,
            "available_reps": sorted([rep for rep, _territory in records], key=str.lower),
        }


class ToolBackendRouter:
    def __init__(
        self,
        *,
        dap_client: CustomerDapClient | None = None,
        databricks_client: CustomerDatabricksQueryClient | None = None,
        sales_team_resolver: SalesTeamResolver | None = None,
        rep_lookup_resolver: RepLookupResolver | None = None,
    ) -> None:
        self.dap_client = dap_client or CustomerDapClient()
        self.databricks_client = databricks_client or CustomerDatabricksQueryClient()
        self.sales_team_resolver = sales_team_resolver or SalesTeamResolver(self.databricks_client)
        self.rep_lookup_resolver = rep_lookup_resolver or RepLookupResolver()

    async def get_scoped_accounts_payload(self) -> dict[str, Any]:
        sales_team = await self.sales_team_resolver.resolve()
        _emit_backend_log(f"[customer-backend] scoped-accounts sales_team={sales_team}")
        static_json_path = get_customer_scope_accounts_static_json_path()
        if static_json_path:
            _emit_backend_log(
                f"[customer-backend] scoped-accounts source=static_json path={static_json_path}"
            )
            rows = _load_static_scoped_accounts(static_json_path, sales_team=sales_team)
        else:
            query = get_customer_scope_accounts_query()
            if not query:
                source = get_customer_scope_accounts_source() or _join_source_parts(
                    get_customer_scope_accounts_catalog(),
                    get_customer_scope_accounts_schema(),
                    get_customer_scope_accounts_table(),
                )
                if not source:
                    raise CustomerBackendConfigurationError(
                        "Configure CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH, CUSTOMER_SCOPE_ACCOUNTS_QUERY, "
                        "or CUSTOMER_SCOPE_ACCOUNTS_SOURCE for customer mode."
                    )
                query = f"""
SELECT
  account_id,
  name,
  global_ultimate,
  sales_team,
  duns,
  is_subsidiary,
  industry,
  sic_or_naics,
  hq_country,
  hq_region,
  customer_or_prospect,
  current_veeam_products,
  renewal_date,
  opportunity_stage,
  last_seller_touch_date
FROM {source}
WHERE sales_team = '{{{{sales_team}}}}'
ORDER BY sales_team, global_ultimate, name
""".strip()
                _emit_backend_log(
                    f"[customer-backend] scoped-accounts source=databricks_source table_or_view={source}"
                )
            else:
                _emit_backend_log("[customer-backend] scoped-accounts source=custom_query")
            rows = await self.databricks_client.query_sql(
                _render_sql_template(query, {"sales_team": sales_team})
                ,
                query_name="scoped_accounts",
            )
        _emit_backend_log(
            f"[customer-backend] scoped-accounts row_count={len(rows)} sales_team={sales_team}"
        )
        unique_parents = sorted(
            {
                _normalize_string(row.get("global_ultimate"))
                for row in rows
                if _normalize_string(row.get("global_ultimate"))
            }
        )
        return {
            "scope_mode": "customer_existing_databricks",
            "territory": sales_team,
            "territories": [sales_team],
            "segment": _segment_from_territory(sales_team),
            "total_accounts": len(rows),
            "unique_global_ultimates": len(unique_parents),
            "accounts": rows,
        }

    async def get_account_contacts_payload(self, account_id: str) -> dict[str, Any]:
        normalized_account_id = _normalize_string(account_id)
        if not normalized_account_id:
            return {"error": "account_id is required."}
        _emit_backend_log(
            f"[customer-backend] contacts account_id={normalized_account_id}"
        )
        query = get_customer_contacts_query()
        if not query:
            source = get_customer_contacts_source() or _join_source_parts(
                get_customer_contacts_catalog(),
                get_customer_contacts_schema(),
                get_customer_contacts_table(),
            )
            if not source:
                raise CustomerBackendConfigurationError(
                    "Configure CUSTOMER_CONTACTS_QUERY or CUSTOMER_CONTACTS_SOURCE for customer mode."
                )
            query = f"""
SELECT
  domain_account_id,
  first_name,
  last_name,
  name,
  title,
  job_position,
  email,
  phone,
  engagement_level,
  contact_stage,
  last_activity_date,
  do_not_call
FROM {source}
WHERE domain_account_id = '{{{{account_id}}}}'
ORDER BY engagement_level, title
""".strip()
            _emit_backend_log(
                f"[customer-backend] contacts source=databricks_source table_or_view={source}"
            )
        else:
            _emit_backend_log("[customer-backend] contacts source=custom_query")
        rows = await self.databricks_client.query_sql(
            _render_sql_template(
                query,
                {
                    "account_id": normalized_account_id,
                },
            )
            ,
            query_name="account_contacts",
        )
        _emit_backend_log(
            f"[customer-backend] contacts row_count={len(rows)} account_id={normalized_account_id}"
        )
        return {"account_id": normalized_account_id, "contacts": rows}

    async def get_top_opportunities_payload(
        self,
        *,
        limit: int,
        offset: int,
        filter_mode: str | None,
        territory: str | None = None,
    ) -> dict[str, Any]:
        sales_team = _normalize_string(territory)
        if not sales_team:
            sales_team = await self.sales_team_resolver.resolve()
        _emit_backend_log(
            f"[customer-backend] top-opps sales_team={sales_team} limit={limit} offset={offset} filter_mode={filter_mode}"
        )
        safe_limit = max(1, min(int(limit), 25))
        safe_offset = max(0, int(offset))
        mode = _normalize_string(filter_mode).lower()
        query = get_customer_top_opportunities_query()
        if not query:
            source = get_customer_top_opportunities_source() or _join_source_parts(
                get_customer_top_opportunities_catalog(),
                get_customer_top_opportunities_schema(),
                get_customer_top_opportunities_table(),
            )
            if not source:
                raise CustomerBackendConfigurationError(
                    "Configure CUSTOMER_TOP_OPPORTUNITIES_QUERY or CUSTOMER_TOP_OPPORTUNITIES_SOURCE for customer mode."
                )
            filters = ["sales_team = '{{sales_team}}'"]
            if mode == "new_logo_only":
                filters.append(
                    "("
                    "coalesce(sales_play_sell_vdp, false) OR "
                    "coalesce(sales_play_sell_kasten, false) OR "
                    "coalesce(sales_play_sell_o365, false) OR "
                    "coalesce(sales_play_sell_vbsf, false) OR "
                    "coalesce(sales_play_sell_cloud, false) OR "
                    "coalesce(sales_play_sell_vault, false) OR "
                    "coalesce(sales_play_vmware_migration, false)"
                    ")"
                )
            order_by = "xf_score_previous_day DESC"
            if mode == "velocity_candidates":
                order_by = (
                    "coalesce(intent, 0) DESC, "
                    "coalesce(xf_score_diff_pct, 0) DESC, "
                    "coalesce(upsell, 0) DESC, "
                    "coalesce(xf_score_previous_day, 0) DESC, "
                    "account_name"
                )
            query = f"""
SELECT
  account_id,
  account_name,
  company_name,
  sales_team,
  xf_score_previous_day,
  xf_score_diff_pct,
  intent,
  competitive,
  upsell,
  fit,
  need,
  vdp_why,
  kasten_why,
  o365_why,
  vbsf_why,
  cloud_why,
  sales_play_sell_vdp,
  sales_play_sell_kasten,
  sales_play_sell_o365,
  sales_play_sell_vbsf,
  sales_play_sell_cloud,
  sales_play_sell_vault,
  sales_play_vmware_migration,
  sales_play_upsell_vdp,
  sales_play_convert_to_vdc
FROM {source}
WHERE {" AND ".join(filters)}
ORDER BY {order_by}
LIMIT {{{{limit}}}}
OFFSET {{{{offset}}}}
""".strip()
            _emit_backend_log(
                f"[customer-backend] top-opps source=databricks_source table_or_view={source} order_by={order_by}"
            )
        else:
            _emit_backend_log("[customer-backend] top-opps source=custom_query")
        rows = await self.databricks_client.query_sql(
            _render_sql_template(
                query,
                {
                    "sales_team": sales_team,
                    "limit": str(safe_limit),
                    "offset": str(safe_offset),
                },
            )
            ,
            query_name="top_opportunities",
        )
        accounts = [
            row
            for row in rows
            if row.get("xf_score_previous_day") not in (None, 0, 0.0, "0", "0.0")
        ]
        _emit_backend_log(
            f"[customer-backend] top-opps row_count={len(accounts)} sales_team={sales_team}"
        )

        return {
            "scope_mode": "customer_existing_databricks",
            "territory": sales_team,
            "territories": [sales_team],
            "segment": _segment_from_territory(sales_team),
            "filter_mode": filter_mode,
            "limit": safe_limit,
            "offset": safe_offset,
            "accounts": accounts,
        }

    def lookup_rep_payload(self, rep_name: str) -> dict[str, Any]:
        return self.rep_lookup_resolver.resolve(rep_name)

    async def close(self) -> None:
        await self.dap_client.close()


_ROUTER: ToolBackendRouter | None = None


def get_customer_tool_backend_router() -> ToolBackendRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = ToolBackendRouter()
    return _ROUTER


def build_backend_investigation_matrix() -> list[dict[str, Any]]:
    return [
        {
            "tool": "get_top_opportunities",
            "backend_source": "direct customer Databricks",
            "auth_model": "planner-side Databricks OBO",
            "required_inputs": ["sales_team", "limit", "offset"],
            "required_fields": [
                "account_id",
                "account_name",
                "company_name",
                "sales_team",
                "xf_score_previous_day",
                "intent",
                "competitive",
                "upsell",
                "fit",
                "need",
            ],
            "fallback_behavior": "optional fallback to signed-in user sales-team mapping when no territory is provided",
            "config_keys": [
                "CUSTOMER_TOP_OPPORTUNITIES_QUERY",
                "CUSTOMER_TOP_OPPORTUNITIES_SOURCE",
                "CUSTOMER_TOP_OPPORTUNITIES_CATALOG",
                "CUSTOMER_TOP_OPPORTUNITIES_SCHEMA",
                "CUSTOMER_TOP_OPPORTUNITIES_TABLE",
                "CUSTOMER_DATABRICKS_HOST",
                "CUSTOMER_DATABRICKS_WAREHOUSE_ID",
            ],
            "open_questions": [
                "What customer table or view should define the top opportunities source?",
            ],
        },
        {
            "tool": "get_scoped_accounts",
            "backend_source": "direct customer Databricks",
            "auth_model": "planner-side Databricks OBO",
            "required_inputs": ["sales_team"],
            "required_fields": [
                "account_id",
                "name",
                "global_ultimate",
                "sales_team",
                "customer_or_prospect",
                "current_veeam_products",
                "renewal_date",
                "opportunity_stage",
                "last_seller_touch_date",
            ],
            "fallback_behavior": "prefer static JSON path, then direct customer Databricks",
            "config_keys": [
                "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH",
                "CUSTOMER_SCOPE_ACCOUNTS_QUERY",
                "CUSTOMER_SCOPE_ACCOUNTS_SOURCE",
                "CUSTOMER_SCOPE_ACCOUNTS_CATALOG",
                "CUSTOMER_SCOPE_ACCOUNTS_SCHEMA",
                "CUSTOMER_SCOPE_ACCOUNTS_TABLE",
                "CUSTOMER_DATABRICKS_HOST",
                "CUSTOMER_DATABRICKS_WAREHOUSE_ID",
            ],
            "open_questions": [
                "Should the static scope JSON remain the long-term Account Pulse source, or move to Databricks later?",
            ],
        },
        {
            "tool": "get_account_contacts",
            "backend_source": "direct customer Databricks",
            "auth_model": "planner-side Databricks OBO",
            "required_inputs": ["account_id"],
            "required_fields": [
                "domain_account_id",
                "name",
                "title",
                "job_position",
                "email",
                "phone",
                "engagement_level",
                "contact_stage",
                "last_activity_date",
                "do_not_call",
            ],
            "fallback_behavior": "none",
            "config_keys": [
                "CUSTOMER_CONTACTS_QUERY",
                "CUSTOMER_CONTACTS_SOURCE",
                "CUSTOMER_CONTACTS_CATALOG",
                "CUSTOMER_CONTACTS_SCHEMA",
                "CUSTOMER_CONTACTS_TABLE",
                "CUSTOMER_DATABRICKS_HOST",
                "CUSTOMER_DATABRICKS_WAREHOUSE_ID",
            ],
            "open_questions": [
                "What customer table or view should define the contact list?",
            ],
        },
        {
            "tool": "lookup_rep",
            "backend_source": "static configured rep mapping",
            "auth_model": "none",
            "required_inputs": ["rep_name"],
            "required_fields": ["matches"],
            "fallback_behavior": "returns no_match with available reps",
            "config_keys": ["CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON"],
            "open_questions": ["Should hosted rep lookup move to a dedicated customer source later?"],
        },
    ]


def dumps_backend_investigation_matrix() -> str:
    return _json_payload({"tools": build_backend_investigation_matrix()})
