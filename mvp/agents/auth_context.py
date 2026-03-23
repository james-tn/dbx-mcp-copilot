"""
Authentication helpers for the stateful Daily Account Planner API.

This module validates inbound Entra bearer tokens for the planner API and
acquires delegated Databricks access tokens through OBO when available.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sys

import msal

try:
    from ..shared.entra_auth import (
        EntraTokenValidator,
        TokenClaims,
        acquire_obo_access_token,
        build_confidential_app,
        expand_expected_audiences,
        extract_bearer_token as extract_bearer_token_header,
    )
    from ..shared.runtime_env import ensure_runtime_env_loaded
except ImportError:
    _MVP_ROOT = Path(__file__).resolve().parent.parent
    if str(_MVP_ROOT) not in sys.path:
        sys.path.insert(0, str(_MVP_ROOT))
    from shared.entra_auth import (
        EntraTokenValidator,
        TokenClaims,
        acquire_obo_access_token,
        build_confidential_app,
        expand_expected_audiences,
        extract_bearer_token as extract_bearer_token_header,
    )
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()

_REQUEST_USER_ASSERTION: ContextVar[str | None] = ContextVar("request_user_assertion", default=None)
_REQUEST_CLAIMS: ContextVar["TokenClaims | None"] = ContextVar("request_claims", default=None)
_REQUEST_DATABRICKS_ACCESS_TOKEN: ContextVar[str | None] = ContextVar(
    "request_databricks_access_token",
    default=None,
)


class AuthenticationRequiredError(RuntimeError):
    """Raised when a request is missing or fails planner API authentication."""


class AuthConfigurationError(RuntimeError):
    """Raised when planner API auth or OBO settings are incomplete."""


class DatabricksOboError(RuntimeError):
    """Raised when delegated Databricks token acquisition fails."""


@dataclass(frozen=True)
class AuthSettings:
    azure_tenant_id: str
    planner_api_client_id: str
    planner_api_client_secret: str
    planner_api_expected_audience: str
    databricks_obo_scope: str

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}"

    @property
    def expected_audiences(self) -> list[str]:
        return expand_expected_audiences(self.planner_api_expected_audience)


@lru_cache(maxsize=1)
def load_auth_settings() -> AuthSettings:
    return AuthSettings(
        azure_tenant_id=os.environ.get("AZURE_TENANT_ID", "").strip(),
        planner_api_client_id=os.environ.get("PLANNER_API_CLIENT_ID", "").strip(),
        planner_api_client_secret=os.environ.get("PLANNER_API_CLIENT_SECRET", "").strip(),
        planner_api_expected_audience=os.environ.get("PLANNER_API_EXPECTED_AUDIENCE", "").strip(),
        databricks_obo_scope=(
            os.environ.get(
                "DATABRICKS_OBO_SCOPE",
                os.environ.get(
                    "DATABRICKS_TOKEN_SCOPE",
                    "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default",
                ),
            ).strip()
            or "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
        ),
    )


@lru_cache(maxsize=1)
def get_token_validator() -> EntraTokenValidator:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for planner API auth.")
    return EntraTokenValidator(settings.azure_tenant_id)


@lru_cache(maxsize=1)
def get_confidential_app() -> msal.ConfidentialClientApplication:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for Databricks OBO.")
    if not settings.planner_api_client_id or not settings.planner_api_client_secret:
        raise AuthConfigurationError(
            "PLANNER_API_CLIENT_ID and PLANNER_API_CLIENT_SECRET are required for Databricks OBO."
        )
    return build_confidential_app(
        client_id=settings.planner_api_client_id,
        client_credential=settings.planner_api_client_secret,
        authority=settings.authority,
    )


def extract_bearer_token(authorization: str | None) -> str:
    return extract_bearer_token_header(
        authorization,
        error_type=AuthenticationRequiredError,
    )


def validate_bearer_token(token: str) -> TokenClaims:
    settings = load_auth_settings()
    if not settings.planner_api_expected_audience:
        raise AuthConfigurationError(
            "PLANNER_API_EXPECTED_AUDIENCE is required for planner API token validation."
        )
    return get_token_validator().validate(
        token,
        settings.expected_audiences,
        error_type=AuthenticationRequiredError,
    )


def bind_request_identity(
    user_assertion: str,
    claims: TokenClaims,
) -> tuple[Token[str | None], Token[TokenClaims | None], Token[str | None]]:
    return (
        _REQUEST_USER_ASSERTION.set(user_assertion),
        _REQUEST_CLAIMS.set(claims),
        _REQUEST_DATABRICKS_ACCESS_TOKEN.set(None),
    )


def reset_request_identity(
    token_ref: Token[str | None],
    claims_ref: Token[TokenClaims | None],
    databricks_token_ref: Token[str | None],
) -> None:
    _REQUEST_USER_ASSERTION.reset(token_ref)
    _REQUEST_CLAIMS.reset(claims_ref)
    _REQUEST_DATABRICKS_ACCESS_TOKEN.reset(databricks_token_ref)


def get_request_user_assertion() -> str | None:
    return _REQUEST_USER_ASSERTION.get()


def get_request_claims() -> TokenClaims | None:
    return _REQUEST_CLAIMS.get()


def get_request_user_id() -> str | None:
    claims = get_request_claims()
    return claims.user_id if claims else None


def acquire_databricks_access_token(user_assertion: str | None = None) -> str | None:
    assertion = (user_assertion or get_request_user_assertion() or "").strip()
    if not assertion:
        return None
    if user_assertion is None:
        cached_access_token = _REQUEST_DATABRICKS_ACCESS_TOKEN.get()
        if cached_access_token:
            return cached_access_token

    settings = load_auth_settings()
    try:
        app = get_confidential_app()
    except AuthConfigurationError as exc:
        raise DatabricksOboError(str(exc)) from exc

    access_token = acquire_obo_access_token(
        app,
        user_assertion=assertion,
        scopes=[settings.databricks_obo_scope],
        error_type=DatabricksOboError,
        default_message="Databricks OBO token acquisition failed.",
    )
    if user_assertion is None:
        _REQUEST_DATABRICKS_ACCESS_TOKEN.set(access_token)
    return access_token
