"""
Authentication helpers for the planner API boundary.

This module is intentionally planner-only: it validates inbound Entra bearer
tokens and keeps request identity available to the planner runtime. Any
downstream enterprise data-source authentication now lives behind the MCP
server boundary.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Generator, AsyncGenerator
from pathlib import Path
import sys
import httpx

try:
    from ..shared.entra_auth import (
        EntraTokenValidator,
        TokenClaims,
        expand_expected_audiences,
        extract_bearer_token as extract_bearer_token_header,
    )
    from ..shared.runtime_env import ensure_runtime_env_loaded
except ImportError:
    _MODULE_PATH = Path(__file__).resolve()
    for _candidate_root in (_MODULE_PATH.parent.parent, _MODULE_PATH.parent):
        if (_candidate_root / "shared").exists():
            if str(_candidate_root) not in sys.path:
                sys.path.insert(0, str(_candidate_root))
            break
    from shared.entra_auth import (
        EntraTokenValidator,
        TokenClaims,
        expand_expected_audiences,
        extract_bearer_token as extract_bearer_token_header,
    )
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()

_REQUEST_USER_ASSERTION: ContextVar[str | None] = ContextVar("request_user_assertion", default=None)
_REQUEST_CLAIMS: ContextVar["TokenClaims | None"] = ContextVar("request_claims", default=None)


class AuthenticationRequiredError(RuntimeError):
    """Raised when a request is missing or fails planner API authentication."""


class AuthConfigurationError(RuntimeError):
    """Raised when planner API auth settings are incomplete."""


class PlannerMcpBearerAuth(httpx.Auth):
    """Attach the current planner request bearer token to outbound MCP requests."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = (get_request_user_assertion() or os.environ.get("MCP_ACCESS_TOKEN", "")).strip()
        if not token:
            raise AuthConfigurationError("An authenticated planner bearer token is required for MCP access.")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = (get_request_user_assertion() or os.environ.get("MCP_ACCESS_TOKEN", "")).strip()
        if not token:
            raise AuthConfigurationError("An authenticated planner bearer token is required for MCP access.")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


@dataclass(frozen=True)
class AuthSettings:
    azure_tenant_id: str
    planner_api_client_id: str
    planner_api_expected_audience: str

    @property
    def expected_audiences(self) -> list[str]:
        return expand_expected_audiences(
            self.planner_api_expected_audience,
            include_client_id=self.planner_api_client_id or None,
        )


@lru_cache(maxsize=1)
def load_auth_settings() -> AuthSettings:
    return AuthSettings(
        azure_tenant_id=os.environ.get("AZURE_TENANT_ID", "").strip(),
        planner_api_client_id=os.environ.get("PLANNER_API_CLIENT_ID", "").strip(),
        planner_api_expected_audience=os.environ.get("PLANNER_API_EXPECTED_AUDIENCE", "").strip(),
    )


@lru_cache(maxsize=1)
def get_token_validator() -> EntraTokenValidator:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for planner API auth.")
    return EntraTokenValidator(settings.azure_tenant_id)


def extract_bearer_token(authorization: str | None) -> str:
    return extract_bearer_token_header(
        authorization,
        error_type=AuthenticationRequiredError,
    )


def validate_bearer_token_for_audience(
    token: str,
    expected_audience: str,
    *,
    include_client_id: str | None = None,
) -> TokenClaims:
    value = expected_audience.strip()
    if not value:
        raise AuthConfigurationError(
            "PLANNER_API_EXPECTED_AUDIENCE is required for planner API token validation."
        )
    return get_token_validator().validate(
        token,
        expand_expected_audiences(value, include_client_id=include_client_id),
        error_type=AuthenticationRequiredError,
    )


def validate_bearer_token(token: str) -> TokenClaims:
    settings = load_auth_settings()
    return validate_bearer_token_for_audience(
        token,
        settings.planner_api_expected_audience,
        include_client_id=settings.planner_api_client_id or None,
    )


def bind_request_identity(
    user_assertion: str,
    claims: TokenClaims,
) -> tuple[Token[str | None], Token[TokenClaims | None]]:
    return (
        _REQUEST_USER_ASSERTION.set(user_assertion),
        _REQUEST_CLAIMS.set(claims),
    )


def reset_request_identity(
    token_ref: Token[str | None],
    claims_ref: Token[TokenClaims | None],
) -> None:
    _REQUEST_USER_ASSERTION.reset(token_ref)
    _REQUEST_CLAIMS.reset(claims_ref)


def get_request_user_assertion() -> str | None:
    return _REQUEST_USER_ASSERTION.get()


def get_request_claims() -> TokenClaims | None:
    return _REQUEST_CLAIMS.get()


def get_request_user_id() -> str | None:
    claims = get_request_claims()
    return claims.user_id if claims else None
