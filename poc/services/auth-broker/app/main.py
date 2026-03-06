from __future__ import annotations

import time
from typing import Any

import msal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .security import TokenValidator

settings = Settings()
validator = TokenValidator(settings.azure_tenant_id)
app = FastAPI(title='RI Auth Broker', version='1.0.0')


class TokenIssueRequest(BaseModel):
    user_assertion: str = Field(..., min_length=10)
    operation_profile: str = Field(..., min_length=3)
    workspace: str | None = None
    warehouse_id: str | None = None


class TokenIssueResponse(BaseModel):
    access_token: str
    token_type: str = 'Bearer'
    expires_in: int
    issued_at_epoch: int


def validate_service_access(service_name: str | None, service_key: str | None) -> None:
    if not service_name:
        raise HTTPException(status_code=401, detail='Missing x-service-name header.')
    if service_name not in settings.allowed_service_names:
        raise HTTPException(status_code=403, detail='Service not allowed for broker access.')
    if not service_key or service_key != settings.broker_shared_service_key:
        raise HTTPException(status_code=401, detail='Invalid service key.')


def validate_operation_profile(profile: str) -> None:
    allowed_profiles = {'sql.read.revenue'}
    if profile not in allowed_profiles:
        raise HTTPException(status_code=403, detail=f'Operation profile is not allowed: {profile}')


@app.get('/healthz')
def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/broker/v1/databricks/token', response_model=TokenIssueResponse)
def issue_databricks_token(
    payload: TokenIssueRequest,
    x_service_name: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
) -> Any:
    validate_service_access(x_service_name, x_service_key)
    validate_operation_profile(payload.operation_profile)

    if settings.broker_allow_passthrough_for_dev:
        return TokenIssueResponse(
            access_token=payload.user_assertion,
            expires_in=900,
            issued_at_epoch=int(time.time()),
        )

    try:
        claims = validator.validate_user_assertion(
            token=payload.user_assertion,
            expected_audience=settings.expected_audiences,
            allowed_tenants=settings.allowed_tenants,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f'Invalid user assertion: {exc}') from exc

    confidential_app = msal.ConfidentialClientApplication(
        client_id=settings.broker_client_id,
        client_credential=settings.broker_client_secret,
        authority=settings.authority,
    )

    result = confidential_app.acquire_token_on_behalf_of(
        user_assertion=payload.user_assertion,
        scopes=[settings.broker_scope],
    )

    if 'access_token' not in result:
        raise HTTPException(status_code=401, detail=f"OBO exchange failed: {result.get('error_description', 'unknown error')}")

    return TokenIssueResponse(
        access_token=result['access_token'],
        expires_in=int(result.get('expires_in', 900)),
        issued_at_epoch=int(time.time()),
    )
