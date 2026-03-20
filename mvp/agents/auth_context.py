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
from typing import Any

import httpx
import jwt
import msal
from jwt import PyJWKClient

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)

_REQUEST_USER_ASSERTION: ContextVar[str | None] = ContextVar("request_user_assertion", default=None)
_REQUEST_CLAIMS: ContextVar["TokenClaims | None"] = ContextVar("request_claims", default=None)


class AuthenticationRequiredError(RuntimeError):
    """Raised when a request is missing or fails planner API authentication."""


class AuthConfigurationError(RuntimeError):
    """Raised when planner API auth or OBO settings are incomplete."""


class DatabricksOboError(RuntimeError):
    """Raised when delegated Databricks token acquisition fails."""


@dataclass(frozen=True)
class TokenClaims:
    oid: str | None
    tid: str | None
    upn: str | None
    aud: str
    scp: str | None

    @property
    def user_id(self) -> str:
        return self.oid or self.upn or self.aud


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
        raw_items = [
            item.strip()
            for item in self.planner_api_expected_audience.split(",")
            if item.strip()
        ]
        audiences: list[str] = []
        for item in raw_items:
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


class TokenValidator:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._metadata = self._load_openid_config()
        self._jwks_client = PyJWKClient(self._metadata["jwks_uri"])
        self._allowed_issuers = {
            self._metadata["issuer"].rstrip("/"),
            f"https://sts.windows.net/{self.tenant_id}".rstrip("/"),
        }

    def _load_openid_config(self) -> dict[str, Any]:
        url = (
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0/"
            ".well-known/openid-configuration"
        )
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
        return response.json()

    def validate(self, token: str, expected_audience: list[str]) -> TokenClaims:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=expected_audience,
            options={"verify_iss": False, "require": ["exp", "iat", "iss", "aud"]},
        )

        issuer = str(decoded.get("iss", "")).rstrip("/")
        if issuer not in self._allowed_issuers:
            raise ValueError("Invalid issuer.")

        return TokenClaims(
            oid=decoded.get("oid"),
            tid=decoded.get("tid"),
            upn=decoded.get("upn") or decoded.get("preferred_username"),
            aud=decoded["aud"],
            scp=decoded.get("scp"),
        )


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
def get_token_validator() -> TokenValidator:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for planner API auth.")
    return TokenValidator(settings.azure_tenant_id)


@lru_cache(maxsize=1)
def get_confidential_app() -> msal.ConfidentialClientApplication:
    settings = load_auth_settings()
    if not settings.azure_tenant_id:
        raise AuthConfigurationError("AZURE_TENANT_ID is required for Databricks OBO.")
    if not settings.planner_api_client_id or not settings.planner_api_client_secret:
        raise AuthConfigurationError(
            "PLANNER_API_CLIENT_ID and PLANNER_API_CLIENT_SECRET are required for Databricks OBO."
        )
    return msal.ConfidentialClientApplication(
        client_id=settings.planner_api_client_id,
        client_credential=settings.planner_api_client_secret,
        authority=settings.authority,
    )


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationRequiredError("Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AuthenticationRequiredError("Missing bearer token.")
    return token


def validate_bearer_token(token: str) -> TokenClaims:
    settings = load_auth_settings()
    if not settings.planner_api_expected_audience:
        raise AuthConfigurationError(
            "PLANNER_API_EXPECTED_AUDIENCE is required for planner API token validation."
        )
    return get_token_validator().validate(token, settings.expected_audiences)


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


def acquire_databricks_access_token(user_assertion: str | None = None) -> str | None:
    assertion = (user_assertion or get_request_user_assertion() or "").strip()
    if not assertion:
        return None

    settings = load_auth_settings()
    try:
        app = get_confidential_app()
    except AuthConfigurationError as exc:
        raise DatabricksOboError(str(exc)) from exc

    result = app.acquire_token_on_behalf_of(
        user_assertion=assertion,
        scopes=[settings.databricks_obo_scope],
    )
    access_token = result.get("access_token")
    if not access_token:
        raise DatabricksOboError(
            result.get("error_description", "Databricks OBO token acquisition failed.")
        )
    return str(access_token)
