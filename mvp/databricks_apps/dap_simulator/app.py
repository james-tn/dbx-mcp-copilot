"""
Simulated Daily Account Planner (DAP) API.

This FastAPI app mirrors the customer-facing DAP contract closely enough to let
the planner team validate token handling and payload expectations before the
real customer deployment is available.

The simulator intentionally keeps its runtime dependencies lightweight so it can
run inside Databricks Apps without requiring compiled wheels. For authentication
experiments we only enforce the JWT audience claim and inspect token headers;
full signature validation belongs in the real customer DAP service.
"""

from __future__ import annotations

import logging
import os
import json
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger("dap_simulator")
app = FastAPI(title="Simulated Daily Account Planner API", version="1.0.0")

_SIMULATED_ROWS: dict[str, list[dict[str, Any]]] = {
    "ENT-APAC-01": [
        {
            "account_id": "0011a00000XYZ001",
            "account_name": "Contoso Ltd",
            "need": 0.94,
            "intent": 0.87,
            "xf_score": 0.91,
        },
        {
            "account_id": "0011a00000XYZ002",
            "account_name": "Fabrikam Inc",
            "need": 0.88,
            "intent": 0.79,
            "xf_score": 0.85,
        },
    ],
    "GreatLakes-ENT-Named-1": [
        {
            "account_id": "001GL0001",
            "account_name": "Ford Motor Company",
            "need": 0.92,
            "intent": 0.84,
            "xf_score": 0.90,
        },
        {
            "account_id": "001GL0003",
            "account_name": "Bridgestone Americas",
            "need": 0.86,
            "intent": 0.72,
            "xf_score": 0.80,
        },
    ],
    "Germany-ENT-Named-5": [
        {
            "account_id": "001GE0001",
            "account_name": "adidas AG",
            "need": 0.89,
            "intent": 0.82,
            "xf_score": 0.91,
        },
        {
            "account_id": "001GE0002",
            "account_name": "DATEV eG",
            "need": 0.83,
            "intent": 0.68,
            "xf_score": 0.86,
        },
        {
            "account_id": "001GE0003",
            "account_name": "Porsche Digital GmbH",
            "need": 0.81,
            "intent": 0.74,
            "xf_score": 0.84,
        },
    ],
}


class AccountsQueryRequest(BaseModel):
    sales_team: str = Field(..., min_length=1)
    row_limit: int = Field(default=20, ge=1, le=5000)


def _expand_expected_audiences(raw_values: str) -> list[str]:
    items = [item.strip() for item in raw_values.split(",") if item.strip()]
    audiences: list[str] = []
    for item in items:
        if item not in audiences:
            audiences.append(item)
        if item.startswith("api://"):
            plain = item[len("api://") :]
            if plain and plain not in audiences:
                audiences.append(plain)
        else:
            api_uri = f"api://{item}"
            if api_uri not in audiences:
                audiences.append(api_uri)
    return audiences


def _expected_audiences() -> list[str]:
    configured = (
        os.environ.get("DAP_SIMULATOR_EXPECTED_AUDIENCE", "").strip()
        or os.environ.get("DAP_API_EXPECTED_AUDIENCE", "").strip()
        or os.environ.get("TOP_OPPORTUNITIES_APP_EXPECTED_AUDIENCE", "").strip()
        or "api://dap-simulator"
    )
    return _expand_expected_audiences(configured)


def _allow_local_dev_bypass() -> bool:
    value = os.environ.get("DAP_SIMULATOR_LOCAL_DEV_BYPASS_AUTH", "false").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _extract_forwarded_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    forwarded = request.headers.get("x-forwarded-access-token", "").strip()
    return forwarded or None


def _decode_jwt_claims_unverified(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("JWT must contain exactly three segments.")
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Malformed bearer token: {exc}",
        ) from exc

    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed bearer token: JWT payload was not a JSON object.",
        )
    return claims


def _audience_matches(actual: Any, expected: list[str]) -> bool:
    if isinstance(actual, str):
        values = [actual]
    elif isinstance(actual, list):
        values = [item for item in actual if isinstance(item, str)]
    else:
        values = []
    return any(value in expected for value in values)


def _validate_request_token(request: Request) -> dict[str, Any]:
    token = _extract_forwarded_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    if _allow_local_dev_bypass():
        return {
            "auth_mode": "local_bypass",
            "aud": _expected_audiences()[0],
            "upn": "local-dev@example.com",
        }

    decoded = _decode_jwt_claims_unverified(token)
    actual_audience = decoded.get("aud")
    expected_audiences = _expected_audiences()
    if not _audience_matches(actual_audience, expected_audiences):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Token audience did not match the DAP simulator configuration. "
                f"Expected one of {expected_audiences}, got {actual_audience!r}."
            ),
        )

    return {
        "auth_mode": "audience_check_only",
        "aud": decoded.get("aud"),
        "upn": decoded.get("upn") or decoded.get("preferred_username"),
        "tid": decoded.get("tid"),
        "oid": decoded.get("oid"),
        "scp": decoded.get("scp"),
    }


@app.get("/api/v1/healthcheck")
async def healthcheck() -> dict[str, str]:
    return {
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/debug/headers")
async def debug_headers(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization")
    forwarded_token = request.headers.get("x-forwarded-access-token")
    claims_hint: dict[str, Any] | None = None
    token = _extract_forwarded_token(request)
    if token:
        try:
            decoded = _decode_jwt_claims_unverified(token)
            claims_hint = {
                "aud": decoded.get("aud"),
                "appid": decoded.get("appid") or decoded.get("azp"),
                "upn": decoded.get("upn") or decoded.get("preferred_username"),
                "scp": decoded.get("scp"),
            }
        except HTTPException:
            claims_hint = {"error": "malformed_token"}
    return {
        "has_authorization": bool(authorization),
        "authorization_prefix": authorization[:20] if authorization else None,
        "has_x_forwarded_access_token": bool(forwarded_token),
        "x_forwarded_access_token_prefix": forwarded_token[:20] if forwarded_token else None,
        "x_ms_client_principal": request.headers.get("x-ms-client-principal"),
        "token_claims_hint": claims_hint,
        "all_header_keys": sorted(request.headers.keys()),
    }


@app.post("/api/v1/accounts/query")
async def accounts_query(payload: AccountsQueryRequest, request: Request) -> dict[str, Any]:
    claims = _validate_request_token(request)
    logger.info(
        "DAP simulator request accepted.",
        extra={
            "sales_team": payload.sales_team,
            "row_limit": payload.row_limit,
            "auth_mode": claims.get("auth_mode"),
            "aud": claims.get("aud"),
            "upn": claims.get("upn"),
            "used_authorization_header": bool(request.headers.get("authorization")),
            "used_x_forwarded_access_token": bool(request.headers.get("x-forwarded-access-token")),
        },
    )
    rows = list(_SIMULATED_ROWS.get(payload.sales_team, []))[: payload.row_limit]
    return {
        "sales_team": payload.sales_team,
        "row_count": len(rows),
        "rows": rows,
    }
