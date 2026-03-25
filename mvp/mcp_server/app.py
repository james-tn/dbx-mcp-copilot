"""FastMCP application mounted inside FastAPI."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

_MODULE_PATH = Path(__file__).resolve()
_ROOT = _MODULE_PATH.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.enterprise_auth import (  # noqa: E402
    AuthenticationRequiredError,
    AuthConfigurationError,
    bind_request_identity,
    extract_bearer_token,
    get_request_user_assertion,
    load_auth_settings,
    reset_request_identity,
    validate_bearer_token_for_audience,
)
from mcp_server.edgar_lookup import edgar_lookup as lookup_edgar_company  # noqa: E402
from shared.enterprise_tool_backend import (  # noqa: E402
    close_request_databricks_client,
    get_account_contacts_payload,
    get_scoped_accounts_payload,
    get_top_opportunities_payload,
    lookup_rep_payload,
)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


def get_mcp_expected_audience() -> str:
    return load_auth_settings().mcp_expected_audience


def get_top_opportunities_app_base_url() -> str | None:
    value = os.environ.get("TOP_OPPORTUNITIES_APP_BASE_URL", "").strip()
    return value.rstrip("/") if value else None


async def _call_top_opportunities_app(
    *,
    user_assertion: str,
    limit: int,
    offset: int,
    territory_override: str | None,
    filter_mode: str | None,
) -> dict[str, Any]:
    if httpx is None:  # pragma: no cover
        raise RuntimeError("httpx is required for the top opportunities app backend.")
    base_url = get_top_opportunities_app_base_url()
    if not base_url:
        raise RuntimeError("TOP_OPPORTUNITIES_APP_BASE_URL is required for the app-backed top opportunities tool.")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/api/top-opportunities",
            headers={"Authorization": f"Bearer {user_assertion}"},
            json={
                "limit": limit,
                "offset": offset,
                "territory_override": territory_override,
                "filter_mode": filter_mode,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Top opportunities app returned an unexpected payload.")
        return payload


mcp = FastMCP(
    name="Daily Account Planner MCP",
    instructions=(
        "Semantic tools for the Daily Account Planner and its internal agents. "
        "The server hides backend-specific auth, transport, routing, and source logic so "
        "planner developers do not need source-specific code."
    ),
)


@mcp.tool(
    name="get_scoped_accounts",
    description="Load the signed-in seller's accessible account list from Databricks secure views.",
)
async def get_scoped_accounts() -> dict[str, Any]:
    return await get_scoped_accounts_payload()


@mcp.tool(
    name="lookup_rep",
    description="Look up a seller territory by rep name for local/demo scenarios.",
)
async def lookup_rep(rep_name: str = "") -> dict[str, Any]:
    return await lookup_rep_payload(rep_name=rep_name)


@mcp.tool(
    name="get_top_opportunities",
    description="Load top accounts for the signed-in seller's accessible scope.",
)
async def get_top_opportunities(
    limit: int = 5,
    offset: int = 0,
    territory_override: str | None = None,
    filter_mode: str | None = None,
) -> dict[str, Any]:
    user_assertion = (get_request_user_assertion() or "").strip()
    return await _call_top_opportunities_app(
        user_assertion=user_assertion,
        limit=limit,
        offset=offset,
        territory_override=territory_override,
        filter_mode=filter_mode,
    )


@mcp.tool(
    name="get_account_contacts",
    description="Load contacts for a given account from the Databricks secure contact view.",
)
async def get_account_contacts(account_id: str) -> dict[str, Any]:
    return await get_account_contacts_payload(account_id=account_id)


@mcp.tool(
    name="edgar_lookup",
    description=(
        "Look up a company in SEC EDGAR and return public-company status plus recent "
        "10-K, 10-Q, and 8-K filings."
    ),
)
async def edgar_lookup(company_name: str) -> dict[str, Any]:
    return await asyncio.to_thread(lookup_edgar_company, company_name)


fastapi_app = FastAPI(title="Daily Account Planner MCP", version="1.0.0")
fastapi_app.mount("/mcp", mcp.http_app(path="/", transport="streamable-http"))


@fastapi_app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@fastapi_app.middleware("http")
async def attach_request_identity(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)

    authorization = request.headers.get("authorization")
    expected_audience = get_mcp_expected_audience()
    try:
        user_assertion = extract_bearer_token(authorization)
        claims = validate_bearer_token_for_audience(user_assertion, expected_audience)
    except AuthenticationRequiredError as exc:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})
    except AuthConfigurationError as exc:
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})

    token_ref = claims_ref = databricks_token_ref = None
    try:
        token_ref, claims_ref, databricks_token_ref = bind_request_identity(user_assertion, claims)
        return await call_next(request)
    finally:
        if token_ref is not None and claims_ref is not None and databricks_token_ref is not None:
            reset_request_identity(token_ref, claims_ref, databricks_token_ref)
        await close_request_databricks_client()
