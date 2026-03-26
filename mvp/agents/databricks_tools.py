"""
Semantic Databricks-backed tools for the Daily Account Planner.

These tools hide raw SQL and direct Databricks transport details from prompts. The
planner now defaults to user-scoped Databricks access via delegated OBO, while
still keeping a local-only demo scope fallback for development.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from typing import Annotated, Any

from agent_framework import tool
from pydantic import Field

try:
    from .config import (
        get_customer_backend_enabled,
        get_effective_ri_scope_mode,
        get_secure_deployment_enabled,
    )
    from .auth_context import acquire_databricks_access_token, get_request_user_assertion
    from .customer_backend import (
        CustomerBackendConfigurationError,
        CustomerDataAccessError,
        SalesTeamResolutionError,
        get_customer_tool_backend_router,
    )
    from .databricks_sql import DatabricksSqlAuthError, DatabricksSqlClient, DatabricksSqlError
except ImportError:
    from config import get_customer_backend_enabled, get_effective_ri_scope_mode, get_secure_deployment_enabled
    from auth_context import acquire_databricks_access_token, get_request_user_assertion
    from customer_backend import (
        CustomerBackendConfigurationError,
        CustomerDataAccessError,
        SalesTeamResolutionError,
        get_customer_tool_backend_router,
    )
    from databricks_sql import DatabricksSqlAuthError, DatabricksSqlClient, DatabricksSqlError

_DEFAULT_DEMO_TERRITORY = "GreatLakes-ENT-Named-1"
_DEFAULT_DATABRICKS_CATALOG = "veeam_demo"
_REQUEST_DATABRICKS_CLIENT: ContextVar[DatabricksSqlClient | None] = ContextVar(
    "request_databricks_client",
    default=None,
)
_REQUEST_DATABRICKS_CLIENT_TOKEN: ContextVar[str | None] = ContextVar(
    "request_databricks_client_token",
    default=None,
)


def _scope_mode() -> str:
    return get_effective_ri_scope_mode()


def _customer_backend_enabled() -> bool:
    return get_customer_backend_enabled()


def _demo_territory() -> str:
    return os.environ.get("RI_DEMO_TERRITORY", _DEFAULT_DEMO_TERRITORY).strip() or _DEFAULT_DEMO_TERRITORY


def _catalog_name() -> str:
    return os.environ.get("DATABRICKS_CATALOG", _DEFAULT_DATABRICKS_CATALOG).strip() or _DEFAULT_DATABRICKS_CATALOG


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _segment_from_territory(territory: str) -> str:
    upper = territory.upper()
    if "-ENT-" in upper:
        return "ENT"
    if "-COM-" in upper:
        return "COM"
    if "-VEL-" in upper:
        return "VEL"
    return "UNKNOWN"


def _request_is_user_scoped() -> bool:
    return bool(get_request_user_assertion())


def _resolve_territory(territory_override: str | None = None, *, allow_override: bool = False) -> str:
    override = (territory_override or "").strip()
    if get_secure_deployment_enabled():
        raise ValueError(
            "Territory override is disabled in secure deployment mode. "
            "The planner always uses the signed-in user's Databricks access."
        )
    if override:
        if _request_is_user_scoped():
            raise ValueError(
                "Territory override is disabled in authenticated planner sessions. "
                "The planner uses the signed-in user's Databricks access."
            )
        if allow_override:
            return override
    return _demo_territory()


def _build_databricks_client(access_token: str | None = None) -> DatabricksSqlClient:
    return DatabricksSqlClient(access_token=access_token)


async def _get_request_databricks_client(access_token: str | None) -> DatabricksSqlClient:
    client = _REQUEST_DATABRICKS_CLIENT.get()
    cached_token = _REQUEST_DATABRICKS_CLIENT_TOKEN.get()
    normalized_token = access_token or ""
    if client is not None and cached_token == normalized_token:
        return client

    if client is not None:
        await client.close()

    client = _build_databricks_client(access_token)
    _REQUEST_DATABRICKS_CLIENT.set(client)
    _REQUEST_DATABRICKS_CLIENT_TOKEN.set(normalized_token)
    return client


async def close_request_databricks_client() -> None:
    client = _REQUEST_DATABRICKS_CLIENT.get()
    _REQUEST_DATABRICKS_CLIENT.set(None)
    _REQUEST_DATABRICKS_CLIENT_TOKEN.set(None)
    if client is not None:
        await client.close()
    if _customer_backend_enabled():
        await get_customer_tool_backend_router().close()


async def _run_query(statement: str) -> list[dict[str, Any]]:
    access_token = acquire_databricks_access_token()
    use_request_client = _request_is_user_scoped()
    client = (
        await _get_request_databricks_client(access_token)
        if use_request_client
        else _build_databricks_client(access_token)
    )
    try:
        return await client.query_sql(statement)
    except DatabricksSqlAuthError as exc:
        raise RuntimeError(
            "The Databricks data source is temporarily unavailable because authentication failed."
        ) from exc
    except DatabricksSqlError as exc:
        raise RuntimeError("The Databricks data source is temporarily unavailable.") from exc
    finally:
        if not use_request_client:
            await client.close()


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _summarize_scope(rows: list[dict[str, Any]]) -> tuple[str | None, list[str], str]:
    territories = sorted(
        {
            str(row.get("sales_team", "")).strip()
            for row in rows
            if str(row.get("sales_team", "")).strip()
        }
    )
    if not territories:
        return None, [], "UNKNOWN"
    if len(territories) == 1:
        return territories[0], territories, _segment_from_territory(territories[0])
    return None, territories, "MIXED"


@tool(
    name="get_scoped_accounts",
    description=(
        "Load the signed-in seller's accessible account list from Databricks secure views. "
        "Returns the accessible territory summary, segment, total accounts, unique global "
        "ultimate parents, and enriched account rows."
    ),
)
async def get_scoped_accounts() -> str:
    if _customer_backend_enabled():
        try:
            payload = await get_customer_tool_backend_router().get_scoped_accounts_payload()
        except (CustomerBackendConfigurationError, CustomerDataAccessError, SalesTeamResolutionError) as exc:
            return _json_payload({"error": str(exc)})
        return _json_payload(payload)

    catalog = _catalog_name()
    statement = f"""
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
FROM {catalog}.ri_secure.accounts
""".strip()
    if _scope_mode() == "demo":
        territory = _resolve_territory()
        statement = f"{statement}\nWHERE sales_team = '{_escape_sql(territory)}'"
    statement = f"{statement}\nORDER BY sales_team, global_ultimate, name"
    rows = await _run_query(statement)
    territory, territories, segment = _summarize_scope(rows)
    unique_parents = sorted({str(row.get("global_ultimate", "")) for row in rows if row.get("global_ultimate")})
    payload = {
        "scope_mode": _scope_mode(),
        "territory": territory,
        "territories": territories,
        "segment": segment,
        "total_accounts": len(rows),
        "unique_global_ultimates": len(unique_parents),
        "accounts": rows,
    }
    return _json_payload(payload)


@tool(
    name="lookup_rep",
    description=(
        "Look up a seller territory by rep name. Supports exact or partial name matches."
    ),
)
async def lookup_rep(
    rep_name: Annotated[str, Field(description="Full or partial seller name")] = "",
) -> str:
    if _customer_backend_enabled():
        try:
            payload = get_customer_tool_backend_router().lookup_rep_payload(rep_name)
        except CustomerBackendConfigurationError as exc:
            return _json_payload({"error": str(exc)})
        return _json_payload(payload)

    name = rep_name.strip()
    if not name:
        return _json_payload({"error": "rep_name is required."})
    if _request_is_user_scoped():
        return _json_payload(
            {
                "error": (
                    "Rep lookup is disabled in authenticated planner sessions. "
                    "The planner uses the signed-in user's Databricks access instead."
                )
            }
        )

    statement = f"""
SELECT rep_key, rep_name, territory
FROM {_catalog_name()}.ri_secure.reps
WHERE LOWER(rep_name) LIKE '%{_escape_sql(name.lower())}%'
ORDER BY rep_name
""".strip()
    rows = await _run_query(statement)
    return _json_payload({"matches": rows})


def _top_opportunities_statement(
    territory: str | None,
    *,
    limit: int,
    offset: int,
    filter_mode: str | None,
) -> str:
    catalog = _catalog_name()
    filters: list[str] = []
    if territory:
        filters.append(f"o.sales_team = '{_escape_sql(territory)}'")
    mode = (filter_mode or "").strip().lower()

    if mode == "new_logo_only":
        filters.append(
            "("
            "coalesce(o.sales_play_sell_vdp, false) OR "
            "coalesce(o.sales_play_sell_kasten, false) OR "
            "coalesce(o.sales_play_sell_o365, false) OR "
            "coalesce(o.sales_play_sell_vbsf, false) OR "
            "coalesce(o.sales_play_sell_cloud, false) OR "
            "coalesce(o.sales_play_sell_vault, false) OR "
            "coalesce(o.sales_play_vmware_migration, false)"
            ")"
        )

    order_by = "o.xf_score_previous_day DESC, o.intent DESC, o.account_name"
    if mode == "velocity_candidates":
        order_by = (
            "coalesce(o.intent, 0) DESC, "
            "coalesce(o.xf_score_diff_pct, 0) DESC, "
            "coalesce(o.upsell, 0) DESC, "
            "coalesce(o.xf_score_previous_day, 0) DESC, "
            "o.account_name"
        )

    where_clause = " AND ".join(filters) if filters else "1 = 1"
    return f"""
SELECT
  o.account_id,
  o.account_name,
  o.company_name,
  o.sales_team,
  o.xf_score_previous_day,
  o.xf_score_diff_pct,
  o.intent,
  o.competitive,
  o.upsell,
  o.fit,
  o.need,
  o.vdp_why,
  o.kasten_why,
  o.o365_why,
  o.vbsf_why,
  o.cloud_why,
  o.sales_play_sell_vdp,
  o.sales_play_sell_kasten,
  o.sales_play_sell_o365,
  o.sales_play_sell_vbsf,
  o.sales_play_sell_cloud,
  o.sales_play_sell_vault,
  o.sales_play_vmware_migration,
  o.sales_play_upsell_vdp,
  o.sales_play_convert_to_vdc,
  a.customer_or_prospect,
  a.current_veeam_products,
  a.renewal_date,
  a.opportunity_stage,
  a.last_seller_touch_date,
  a.industry,
  a.sic_or_naics,
  a.hq_country,
  a.hq_region
FROM {catalog}.ri_secure.opportunities o
LEFT JOIN {catalog}.ri_secure.accounts a
  ON a.account_id = o.account_id
WHERE {where_clause}
ORDER BY {order_by}
LIMIT {limit}
OFFSET {offset}
""".strip()


@tool(
    name="get_top_opportunities",
    description=(
        "Load top accounts for the signed-in seller's accessible scope. Supports optional "
        "pagination, local-only territory override, and filter modes such as new_logo_only "
        "or velocity_candidates."
    ),
)
async def get_top_opportunities(
    limit: Annotated[int, Field(description="Number of accounts to return", ge=1, le=25)] = 5,
    offset: Annotated[int, Field(description="Offset for pagination", ge=0)] = 0,
    territory: Annotated[
        str | None,
        Field(description="Optional sales territory filter, such as Germany-ENT-Named-5, or a comma-separated list"),
    ] = None,
    filter_mode: Annotated[
        str | None,
        Field(description="Optional filter mode: velocity_candidates or new_logo_only"),
    ] = None,
    territory_override: Annotated[
        str | None,
        Field(description="Deprecated local compatibility alias for territory"),
    ] = None,
) -> str:
    if _customer_backend_enabled():
        try:
            payload = await get_customer_tool_backend_router().get_top_opportunities_payload(
                limit=max(1, min(_coerce_int(limit, 5), 25)),
                offset=max(0, _coerce_int(offset, 0)),
                filter_mode=filter_mode,
                territory=territory or territory_override,
            )
        except (CustomerBackendConfigurationError, CustomerDataAccessError, SalesTeamResolutionError) as exc:
            return _json_payload({"error": str(exc)})
        return _json_payload(payload)

    territory: str | None = None
    if _scope_mode() == "demo" or territory_override:
        territory = _resolve_territory(territory_override, allow_override=True)
    safe_limit = max(1, min(_coerce_int(limit, 5), 25))
    safe_offset = max(0, _coerce_int(offset, 0))
    statement = _top_opportunities_statement(
        territory,
        limit=safe_limit,
        offset=safe_offset,
        filter_mode=filter_mode,
    )
    rows = await _run_query(statement)
    filtered_rows = [
        row
        for row in rows
        if row.get("xf_score_previous_day") not in (None, 0, 0.0, "0", "0.0")
    ]
    inferred_territory, territories, segment = _summarize_scope(filtered_rows)
    payload = {
        "scope_mode": _scope_mode(),
        "territory": territory or inferred_territory,
        "territories": territories,
        "segment": segment,
        "filter_mode": filter_mode,
        "limit": safe_limit,
        "offset": safe_offset,
        "accounts": filtered_rows,
    }
    return _json_payload(payload)


@tool(
    name="get_account_contacts",
    description=(
        "Load contacts for a given account_id from the Databricks secure contact view."
    ),
)
async def get_account_contacts(
    account_id: Annotated[str, Field(description="Account ID from get_top_opportunities")],
) -> str:
    if _customer_backend_enabled():
        try:
            payload = await get_customer_tool_backend_router().get_account_contacts_payload(account_id)
        except (CustomerBackendConfigurationError, CustomerDataAccessError, SalesTeamResolutionError) as exc:
            return _json_payload({"error": str(exc)})
        return _json_payload(payload)

    value = account_id.strip()
    if not value:
        return _json_payload({"error": "account_id is required."})
    statement = f"""
SELECT
  account_id,
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
FROM {_catalog_name()}.ri_secure.contacts
WHERE account_id = '{_escape_sql(value)}'
ORDER BY name
""".strip()
    rows = await _run_query(statement)
    return _json_payload({"account_id": value, "contacts": rows})
