"""
Wrapper-side Entra validation and planner OBO helpers for debug chat.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import msal
import sys

try:
    from ..shared.entra_auth import (
        EntraTokenValidator,
        TokenClaims as DebugTokenClaims,
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
        TokenClaims as DebugTokenClaims,
        acquire_obo_access_token,
        build_confidential_app,
        expand_expected_audiences,
        extract_bearer_token as extract_bearer_token_header,
    )
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()


class DebugAuthError(RuntimeError):
    """Base error for wrapper debug-chat authentication failures."""


class DebugAuthConfigurationError(DebugAuthError):
    """Raised when wrapper debug-chat auth settings are incomplete."""


class DebugAuthValidationError(DebugAuthError):
    """Raised when the caller bearer is missing or invalid."""


class DebugAuthOboError(DebugAuthError):
    """Raised when the wrapper cannot exchange a debug token for a planner token."""


@dataclass(frozen=True)
class DebugAuthSettings:
    tenant_id: str
    wrapper_client_id: str
    wrapper_client_secret: str
    expected_audience: str
    planner_scope: str

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def expected_audiences(self) -> list[str]:
        return expand_expected_audiences(
            self.expected_audience,
            include_client_id=self.wrapper_client_id,
            include_bot_id_aliases=True,
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DebugAuthConfigurationError(f"{name} is required for wrapper debug chat.")
    return value


@lru_cache(maxsize=1)
def load_debug_auth_settings(expected_audience: str | None = None) -> DebugAuthSettings:
    audience = (
        expected_audience.strip()
        if expected_audience is not None
        else os.environ.get("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "").strip()
    )
    if not audience:
        raise DebugAuthConfigurationError(
            "WRAPPER_DEBUG_EXPECTED_AUDIENCE is required for wrapper debug chat."
        )
    return DebugAuthSettings(
        tenant_id=_required("AZURE_TENANT_ID"),
        wrapper_client_id=_required("BOT_APP_ID"),
        wrapper_client_secret=_required("BOT_APP_PASSWORD"),
        expected_audience=audience,
        planner_scope=_required("PLANNER_API_SCOPE"),
    )


@lru_cache(maxsize=1)
def get_debug_token_validator(expected_audience: str | None = None) -> EntraTokenValidator:
    settings = load_debug_auth_settings(expected_audience)
    return EntraTokenValidator(settings.tenant_id)


@lru_cache(maxsize=1)
def get_debug_confidential_app(expected_audience: str | None = None) -> msal.ConfidentialClientApplication:
    settings = load_debug_auth_settings(expected_audience)
    return build_confidential_app(
        client_id=settings.wrapper_client_id,
        client_credential=settings.wrapper_client_secret,
        authority=settings.authority,
    )


def extract_bearer_token(authorization: str | None) -> str:
    return extract_bearer_token_header(
        authorization,
        error_type=DebugAuthValidationError,
    )


def validate_debug_token(token: str, *, expected_audience: str) -> DebugTokenClaims:
    settings = load_debug_auth_settings(expected_audience)
    return get_debug_token_validator(expected_audience).validate(
        token,
        settings.expected_audiences,
        error_type=DebugAuthValidationError,
    )


def acquire_planner_token_on_behalf_of(
    *,
    user_assertion: str,
    expected_audience: str,
) -> str:
    settings = load_debug_auth_settings(expected_audience)
    return acquire_obo_access_token(
        get_debug_confidential_app(expected_audience),
        user_assertion=user_assertion,
        scopes=[settings.planner_scope],
        error_type=DebugAuthOboError,
        default_message="Wrapper planner OBO failed.",
    )
