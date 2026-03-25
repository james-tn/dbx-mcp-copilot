"""Demo FastAPI app for the app-backed get_top_opportunities tool."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

_MODULE_PATH = Path(__file__).resolve()
_ROOT = _MODULE_PATH.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.enterprise_auth import (  # noqa: E402
    AuthenticationRequiredError,
    AuthConfigurationError,
    bind_request_identity,
    extract_bearer_token,
    reset_request_identity,
    validate_bearer_token_for_audience,
)
from shared.enterprise_tool_backend import close_request_databricks_client, get_top_opportunities_payload  # noqa: E402


class TopOpportunitiesRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=25)
    offset: int = Field(default=0, ge=0)
    territory_override: str | None = None
    filter_mode: str | None = None


app = FastAPI(title="Top Opportunities App", version="1.0.0")


def get_expected_audience() -> str:
    return (
        os.environ.get("TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE", "").strip()
        or os.environ.get("MCP_EXPECTED_AUDIENCE", "").strip()
        or os.environ.get("PLANNER_API_EXPECTED_AUDIENCE", "").strip()
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/top-opportunities")
async def top_opportunities(payload: TopOpportunitiesRequest) -> dict[str, object]:
    return await get_top_opportunities_payload(
        limit=payload.limit,
        offset=payload.offset,
        territory_override=payload.territory_override,
        filter_mode=payload.filter_mode,
    )


@app.middleware("http")
async def attach_request_identity(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)

    authorization = request.headers.get("authorization")
    try:
        user_assertion = extract_bearer_token(authorization)
        claims = validate_bearer_token_for_audience(user_assertion, get_expected_audience())
    except AuthenticationRequiredError as exc:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})
    except AuthConfigurationError as exc:
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})

    token_ref, claims_ref, databricks_token_ref = bind_request_identity(user_assertion, claims)
    try:
        return await call_next(request)
    finally:
        await close_request_databricks_client()
        reset_request_identity(token_ref, claims_ref, databricks_token_ref)
