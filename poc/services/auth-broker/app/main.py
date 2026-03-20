from __future__ import annotations

import time
from typing import Any

import msal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .databricks_client import run_query
from .security import TokenValidator
from .sql_guardrails import validate_sql

settings = Settings()
validator = TokenValidator(settings.azure_tenant_id)
confidential_app = msal.ConfidentialClientApplication(
    client_id=settings.broker_client_id,
    client_credential=settings.broker_client_secret,
    authority=settings.authority,
)
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


class SqlQueryRequest(BaseModel):
    user_assertion: str = Field(..., min_length=10)
    operation_profile: str = Field(..., min_length=3)
    query: str = Field(..., min_length=10)
    max_rows: int | None = Field(default=None, ge=1)


class SqlQueryResponse(BaseModel):
    row_count: int
    rows: list[dict[str, Any]]


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


def _build_confidential_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.broker_client_id,
        client_credential=settings.broker_client_secret,
        authority=settings.authority,
    )


def _acquire_databricks_token(user_assertion: str, *, prefer_cached_app: bool = True) -> TokenIssueResponse:
    if settings.broker_allow_passthrough_for_dev:
        return TokenIssueResponse(
            access_token=user_assertion,
            expires_in=900,
            issued_at_epoch=int(time.time()),
        )

    app_client = confidential_app if prefer_cached_app else _build_confidential_app()
    result = app_client.acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=[settings.broker_scope],
    )

    if 'access_token' not in result:
        raise HTTPException(status_code=401, detail=f"OBO exchange failed: {result.get('error_description', 'unknown error')}")

    return TokenIssueResponse(
        access_token=result['access_token'],
        expires_in=int(result.get('expires_in', 900)),
        issued_at_epoch=int(time.time()),
    )


def _is_unauthorized_databricks_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return '401' in message or 'unauthorized' in message or 'invalid access token' in message


def _execute_sql_query(payload: SqlQueryRequest) -> SqlQueryResponse:
    validate_sql(payload.query, settings.broker_allowed_schema)
    effective_max_rows = min(payload.max_rows or settings.broker_max_rows, settings.broker_max_rows)

    token_response = _acquire_databricks_token(payload.user_assertion, prefer_cached_app=True)
    try:
        rows = run_query(
            server_hostname=settings.databricks_server_hostname,
            http_path=settings.databricks_http_path,
            access_token=token_response.access_token,
            query=payload.query,
            max_rows=effective_max_rows,
        )
    except Exception as exc:
        if not _is_unauthorized_databricks_error(exc):
            raise

        refreshed_token = _acquire_databricks_token(payload.user_assertion, prefer_cached_app=False)
        try:
            rows = run_query(
                server_hostname=settings.databricks_server_hostname,
                http_path=settings.databricks_http_path,
                access_token=refreshed_token.access_token,
                query=payload.query,
                max_rows=effective_max_rows,
            )
        except Exception as retry_exc:
            if _is_unauthorized_databricks_error(retry_exc):
                raise HTTPException(status_code=401, detail='Downstream Databricks authorization failed after token refresh.') from retry_exc
            raise

    return SqlQueryResponse(row_count=len(rows), rows=rows)


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

    try:
        validator.validate_user_assertion(
            token=payload.user_assertion,
            expected_audience=settings.expected_audiences,
            allowed_tenants=settings.allowed_tenants,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f'Invalid user assertion: {exc}') from exc

    return _acquire_databricks_token(payload.user_assertion, prefer_cached_app=True)


@app.post('/api/sql/query', response_model=SqlQueryResponse)
def execute_sql_query(
    payload: SqlQueryRequest,
    x_service_name: str | None = Header(default=None),
    x_service_key: str | None = Header(default=None),
) -> SqlQueryResponse:
    validate_service_access(x_service_name, x_service_key)
    validate_operation_profile(payload.operation_profile)

    try:
        validator.validate_user_assertion(
            token=payload.user_assertion,
            expected_audience=settings.expected_audiences,
            allowed_tenants=settings.allowed_tenants,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f'Invalid user assertion: {exc}') from exc

    try:
        return _execute_sql_query(payload)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f'SQL guardrail violation: {exc}') from exc
