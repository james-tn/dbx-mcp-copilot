from __future__ import annotations

from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient


@dataclass
class TokenClaims:
    oid: str | None
    tid: str | None
    upn: str | None
    aud: str
    scp: str | None


class TokenValidator:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._metadata = self._load_openid_config()
        self._jwks_client = PyJWKClient(self._metadata['jwks_uri'])
        self._allowed_issuers = {
            self._metadata['issuer'].rstrip('/'),
            f'https://sts.windows.net/{self.tenant_id}'.rstrip('/'),
        }

    def _load_openid_config(self) -> dict:
        url = f'https://login.microsoftonline.com/{self.tenant_id}/v2.0/.well-known/openid-configuration'
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
        return response.json()

    def validate_user_assertion(self, token: str, expected_audience: str | list[str], allowed_tenants: list[str]) -> TokenClaims:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            audience=expected_audience,
            options={'verify_iss': False, 'require': ['exp', 'iat', 'iss', 'aud']},
        )

        issuer = str(decoded.get('iss', '')).rstrip('/')
        if issuer not in self._allowed_issuers:
            raise ValueError('Invalid issuer')

        tid = decoded.get('tid')
        if allowed_tenants and tid not in allowed_tenants:
            raise ValueError('Token tenant is not allowed.')

        return TokenClaims(
            oid=decoded.get('oid'),
            tid=tid,
            upn=decoded.get('upn') or decoded.get('preferred_username'),
            aud=decoded['aud'],
            scp=decoded.get('scp'),
        )
