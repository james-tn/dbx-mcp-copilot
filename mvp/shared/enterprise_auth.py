"""
Shared request authentication helpers for enterprise middle-tier services.

This module owns Entra bearer validation plus downstream Databricks OBO token
exchange for services such as the MCP server and Databricks-backed app tools.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache

from azure.identity import ManagedIdentityCredential, OnBehalfOfCredential
import msal

from .entra_auth import (
    EntraTokenValidator,
    TokenClaims,
    acquire_obo_access_token,
    build_confidential_app,
    expand_expected_audiences,
    extract_bearer_token as extract_bearer_token_header,
)
from .runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()

_REQUEST_USER_ASSERTION: ContextVar[str | None] = ContextVar("mcp_request_user_assertion", default=None)
_REQUEST_CLAIMS: ContextVar[TokenClaims | None] = ContextVar("mcp_request_claims", default=None)
_REQUEST_DATABRICKS_ACCESS_TOKEN: ContextVar[str | None] = ContextVar(
    "mcp_request_databricks_access_token",
    default=None,
)

_DEFAULT_DATABRICKS_SCOPE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"
_DEFAULT_ASSERTION_SCOPE = "api://AzureADTokenExchange/.default"


class AuthenticationRequiredError(RuntimeError):
    """Raised when an enterprise service request is missing or fails authentication."""


class AuthConfigurationError(RuntimeError):
    """Raised when enterprise-service auth settings are incomplete."""


class DatabricksOboError(RuntimeError):
    """Raised when delegated Databricks token acquisition fails."""


@dataclass(frozen=True)
class McpAuthSettings:
    azure_tenant_id: str
    mcp_client_id: str
    mcp_client_secret: str
    mcp_expected_audience: str
    databricks_obo_scope: str
    managed_identity_client_id: str
    client_assertion_scope: str

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}"

    @property
    def expected_audiences(self) -> list[str]:
        return expand_expected_audiences(
            self.mcp_expected_audience,
            include_client_id=self.mcp_client_id or None,
        )


@lru_cache(maxsize=1)
def load_auth_settings() -> McpAuthSettings:
    return McpAuthSettings(
        azure_tenant_id=os.environ.get("AZURE_TENANT_ID", "").strip(),
        mcp_client_id=os.environ.get("MCP_CLIENT_ID", "").strip(),
        mcp_client_secret=os.environ.get("MCP_CLIENT_SECRET", "").strip(),
        mcp_expected_audience=os.environ.get("MCP_EXPECTED_AUDIENCE", "").strip(),
        databricks_obo_scope=(
            os.environ.get(
                "DATABRICKS_OBO_SCOPE",
                os.environ.get("DATABRICKS_TOKEN_SCOPE", _DEFAULT_DATABRICKS_SCOPE),
            ).strip()
            or _DEFAULT_DATABRICKS_SCOPE
        ),
        managed_identity_client_id=(
            os.environ.get("MCP_MANAGED_IDENTITY_CLIENT_ID", "").strip()
            or os.environ.get("AZURE_CLIENT_ID", "").strip()
        ),
        client_assertion_scope=(
            os.environ.get("MCP_CLIENT_ASSERTION_SCOPE", _DEFAULT_ASSERTION_SCOPE).strip()
            or _DEFAULT_ASSERTION_SCOPE
        ),
    )


@lru_cache(maxsize=1)
def get_token_validator() -> EntraTokenValidator:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for MCP auth.")
    return EntraTokenValidator(settings.azure_tenant_id)


@lru_cache(maxsize=1)
def get_confidential_app() -> msal.ConfidentialClientApplication:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for Databricks OBO.")
    if not settings.mcp_client_id or not settings.mcp_client_secret:
        raise AuthConfigurationError(
            "MCP_CLIENT_ID and MCP_CLIENT_SECRET are required for secret-based Databricks OBO."
        )
    return build_confidential_app(
        client_id=settings.mcp_client_id,
        client_credential=settings.mcp_client_secret,
        authority=settings.authority,
    )


@lru_cache(maxsize=1)
def get_managed_identity_credential() -> ManagedIdentityCredential:
    settings = load_auth_settings()
    if settings.managed_identity_client_id:
        return ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    return ManagedIdentityCredential()


def build_client_assertion() -> str:
    settings = load_auth_settings()
    try:
        return get_managed_identity_credential().get_token(settings.client_assertion_scope).token
    except Exception as exc:  # pragma: no cover - platform-dependent
        raise DatabricksOboError("Failed to acquire managed-identity client assertion for MCP OBO.") from exc


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
        raise AuthConfigurationError("MCP_EXPECTED_AUDIENCE is required for MCP token validation.")
    return get_token_validator().validate(
        token,
        expand_expected_audiences(value, include_client_id=include_client_id),
        error_type=AuthenticationRequiredError,
    )


def validate_bearer_token(token: str) -> TokenClaims:
    settings = load_auth_settings()
    return validate_bearer_token_for_audience(
        token,
        settings.mcp_expected_audience,
        include_client_id=settings.mcp_client_id or None,
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
    if settings.mcp_client_secret:
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
    else:
        if not settings.azure_tenant_id or not settings.mcp_client_id:
            raise DatabricksOboError(
                "AZURE_TENANT_ID and MCP_CLIENT_ID are required for managed-identity Databricks OBO."
            )
        try:
            credential = OnBehalfOfCredential(
                tenant_id=settings.azure_tenant_id,
                client_id=settings.mcp_client_id,
                client_assertion_func=build_client_assertion,
                user_assertion=assertion,
            )
            access_token = credential.get_token(settings.databricks_obo_scope).token
        except Exception as exc:  # pragma: no cover - platform-dependent
            raise DatabricksOboError("Managed-identity Databricks OBO token acquisition failed.") from exc

    if user_assertion is None:
        _REQUEST_DATABRICKS_ACCESS_TOKEN.set(access_token)
    return access_token
