"""Shared Entra token validation and OBO helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import httpx
import jwt
import msal
from jwt import PyJWKClient


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


def expand_expected_audiences(
    raw_values: str | Sequence[str],
    *,
    include_client_id: str | None = None,
    include_bot_id_aliases: bool = False,
) -> list[str]:
    if isinstance(raw_values, str):
        items = [item.strip() for item in raw_values.split(",") if item.strip()]
    else:
        items = [str(item).strip() for item in raw_values if str(item).strip()]

    audiences: list[str] = []
    for item in items:
        if item not in audiences:
            audiences.append(item)
        if item.startswith("api://"):
            plain = item[len("api://") :]
            if plain and plain not in audiences:
                audiences.append(plain)
            if include_bot_id_aliases and plain.startswith("botid-"):
                app_id = plain[len("botid-") :]
                if app_id and app_id not in audiences:
                    audiences.append(app_id)
        else:
            api_uri = f"api://{item}"
            if api_uri not in audiences:
                audiences.append(api_uri)

    if include_client_id:
        client_id = include_client_id.strip()
        if client_id and client_id not in audiences:
            audiences.append(client_id)
    return audiences


class EntraTokenValidator:
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

    def validate(
        self,
        token: str,
        expected_audience: Sequence[str],
        *,
        error_type: type[Exception] = ValueError,
        invalid_issuer_message: str = "Invalid issuer.",
    ) -> TokenClaims:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=list(expected_audience),
            options={"verify_iss": False, "require": ["exp", "iat", "iss", "aud"]},
        )
        issuer = str(decoded.get("iss", "")).rstrip("/")
        if issuer not in self._allowed_issuers:
            raise error_type(invalid_issuer_message)
        return TokenClaims(
            oid=decoded.get("oid"),
            tid=decoded.get("tid"),
            upn=decoded.get("upn") or decoded.get("preferred_username"),
            aud=str(decoded["aud"]),
            scp=decoded.get("scp"),
        )


def build_confidential_app(
    *,
    client_id: str,
    client_credential: str,
    authority: str,
) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_credential,
        authority=authority,
    )


def acquire_obo_access_token(
    app: msal.ConfidentialClientApplication,
    *,
    user_assertion: str,
    scopes: Sequence[str],
    error_type: type[Exception],
    default_message: str,
) -> str:
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=list(scopes),
    )
    access_token = result.get("access_token")
    if access_token:
        return str(access_token)
    raise error_type(str(result.get("error_description") or default_message))


def extract_bearer_token(
    authorization: str | None,
    *,
    error_type: type[Exception],
    missing_message: str = "Missing bearer token.",
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise error_type(missing_message)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise error_type(missing_message)
    return token
