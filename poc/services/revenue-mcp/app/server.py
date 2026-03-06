from __future__ import annotations

from contextvars import ContextVar
import re
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from openai import AzureOpenAI
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .broker_client import exchange_user_assertion_for_databricks_token
from .databricks_client import run_query
from .sql_guardrails import validate_sql


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    mcp_broker_base_url: str
    mcp_broker_shared_key: str

    azure_openai_endpoint: str
    azure_openai_deployment: str

    databricks_server_hostname: str
    databricks_http_path: str

    mcp_allowed_schema: str = 'ri_poc.revenue'
    mcp_max_rows: int = 5000
    mcp_query_timeout_seconds: int = 30
    azure_tenant_id: str | None = None
    broker_client_id: str | None = None
    mcp_resource_identifier: str | None = None
    mcp_scope: str | None = None
    user_assertion_token: str | None = None


settings = Settings()
mcp = FastMCP(
    name='Revenue Intelligence Expert MCP',
    instructions=(
        'Use the domain expert tools to answer revenue analytics questions. '
        'Each tool returns generated SQL, row count, and tabular rows from the approved schema.'
    ),
)
mcp_http_app = mcp.http_app(path='/', transport='streamable-http', stateless_http=True)
app = FastAPI(
    title='Revenue Intelligence MCP Service',
    version='1.0.0',
    lifespan=mcp_http_app.lifespan,
)
request_user_assertion_token: ContextVar[str | None] = ContextVar('request_user_assertion_token', default=None)


class AskRevenueRequest(BaseModel):
    question: str = Field(..., min_length=5)


class AskRevenueResponse(BaseModel):
    generated_sql: str
    row_count: int
    rows: list[dict[str, Any]]


class ExpertToolResponse(AskRevenueResponse):
    expert: str


def _build_mcp_resource_identifier(request: Request) -> str:
    if settings.mcp_resource_identifier and settings.mcp_resource_identifier.strip():
        return settings.mcp_resource_identifier.strip().rstrip('/')
    forwarded_proto = request.headers.get('x-forwarded-proto', '').split(',')[0].strip().lower()
    scheme = forwarded_proto or request.url.scheme
    host = request.headers.get('host', request.url.netloc)
    if scheme != 'https' and not host.startswith('localhost') and not host.startswith('127.0.0.1'):
        scheme = 'https'
    base = f'{scheme}://{host}'.rstrip('/')
    return f'{base}/mcp'


def _build_resource_metadata_url(request: Request) -> str:
    resource_identifier = _build_mcp_resource_identifier(request)
    match = re.match(r'^(https://[^/]+)(/.*)?$', resource_identifier)
    if not match:
        return f"{str(request.base_url).rstrip('/')}/.well-known/oauth-protected-resource"
    origin = match.group(1)
    return f'{origin}/.well-known/oauth-protected-resource'


def _build_www_authenticate_header(request: Request, detail: str) -> str:
    resource_metadata_url = _build_resource_metadata_url(request)
    escaped_detail = detail.replace('"', "'")
    return (
        'Bearer '
        f'error="invalid_token", error_description="{escaped_detail}", '
        f'resource_metadata="{resource_metadata_url}"'
    )


def _build_rfc9728_metadata(request: Request, resource_identifier: str | None = None) -> dict[str, Any]:
    resource = (resource_identifier or _build_mcp_resource_identifier(request)).rstrip('/')
    metadata: dict[str, Any] = {
        'resource': resource,
        'bearer_methods_supported': ['header'],
        'resource_name': 'Revenue Intelligence Expert MCP',
    }

    if settings.azure_tenant_id:
        metadata['authorization_servers'] = [
            f'https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0'
        ]

    scope = settings.mcp_scope
    if not scope and settings.broker_client_id:
        scope = f'api://{settings.broker_client_id}/user_impersonation'
    if scope:
        metadata['scopes_supported'] = [scope]

    return metadata


@app.middleware('http')
async def enforce_mcp_bearer_auth(request: Request, call_next):
    if request.url.path == '/mcp':
        request.scope['path'] = '/mcp/'
        request.scope['raw_path'] = b'/mcp/'

    if not request.url.path.startswith('/mcp'):
        return await call_next(request)

    authorization = request.headers.get('authorization')
    if not authorization or not authorization.lower().startswith('bearer '):
        detail = 'Missing bearer token.'
        return JSONResponse(
            status_code=401,
            content={'detail': detail},
            headers={'WWW-Authenticate': _build_www_authenticate_header(request, detail)},
        )

    user_token = authorization.split(' ', 1)[1].strip()
    if not user_token:
        detail = 'Missing bearer token.'
        return JSONResponse(
            status_code=401,
            content={'detail': detail},
            headers={'WWW-Authenticate': _build_www_authenticate_header(request, detail)},
        )

    token_ref = request_user_assertion_token.set(user_token)
    try:
        return await call_next(request)
    finally:
        request_user_assertion_token.reset(token_ref)


def _extract_user_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(status_code=401, detail='Missing bearer token.')
    return authorization.split(' ', 1)[1].strip()


def _resolve_user_assertion_token(user_assertion_token: str | None) -> str:
    if user_assertion_token and user_assertion_token.strip():
        return user_assertion_token.strip()
    request_token = request_user_assertion_token.get()
    if request_token and request_token.strip():
        return request_token.strip()
    if settings.user_assertion_token and settings.user_assertion_token.strip():
        return settings.user_assertion_token.strip()
    raise ValueError('Missing user assertion token. Provide user_assertion_token or set USER_ASSERTION_TOKEN.')


def _default_sql(question: str) -> str:
    schema = settings.mcp_allowed_schema
    q = question.lower()
    if 'attainment' in q:
        return (
            "SELECT d.fiscal_quarter, r.region_code, "
            "SUM(f.net_amount) AS net_revenue, SUM(q.quota_amount) AS quota_amount, "
            "SUM(f.net_amount) / NULLIF(SUM(q.quota_amount),0) AS attainment_pct "
            f"FROM {schema}.v_fact_revenue_secure f "
            f"JOIN {schema}.dim_region r ON f.region_id = r.region_id "
            f"JOIN {schema}.dim_date d ON f.date_key = d.date_key "
            f"JOIN {schema}.fact_quota q ON q.date_key = f.date_key AND q.region_id = f.region_id AND q.product_id = f.product_id "
            "GROUP BY d.fiscal_quarter, r.region_code "
            "ORDER BY d.fiscal_quarter, r.region_code"
        )
    if 'arr' in q:
        return (
            "SELECT d.fiscal_quarter, r.region_code, SUM(f.arr_amount) AS arr "
            f"FROM {schema}.v_fact_revenue_secure f "
            f"JOIN {schema}.dim_region r ON f.region_id = r.region_id "
            f"JOIN {schema}.dim_date d ON f.date_key = d.date_key "
            "GROUP BY d.fiscal_quarter, r.region_code "
            "ORDER BY d.fiscal_quarter, r.region_code"
        )
    return (
        "SELECT d.fiscal_quarter, r.region_code, SUM(f.net_amount) AS net_revenue "
        f"FROM {schema}.v_fact_revenue_secure f "
        f"JOIN {schema}.dim_region r ON f.region_id = r.region_id "
        f"JOIN {schema}.dim_date d ON f.date_key = d.date_key "
        "GROUP BY d.fiscal_quarter, r.region_code "
        "ORDER BY d.fiscal_quarter, r.region_code"
    )


def _generate_sql_with_openai(question: str) -> str:
    client = AzureOpenAI(
        api_version='2024-10-21',
        azure_endpoint=settings.azure_openai_endpoint,
        timeout=15.0,
    )

    prompt = f"""
You generate a single read-only Databricks SQL query.
Use ONLY schema {settings.mcp_allowed_schema} and prefer view {settings.mcp_allowed_schema}.v_fact_revenue_secure.
Do not use DDL/DML. Return only SQL.
Question: {question}
"""

    response = client.responses.create(
        model=settings.azure_openai_deployment,
        input=prompt,
        temperature=0,
    )

    text = response.output_text.strip()
    text = re.sub(r'^```sql\\s*|```$', '', text, flags=re.IGNORECASE | re.MULTILINE).strip()
    return text


def _execute_expert_query(
    *,
    expert: str,
    expert_lens: str,
    question: str,
    user_assertion_token: str | None,
) -> ExpertToolResponse:
    assertion_token = _resolve_user_assertion_token(user_assertion_token)
    augmented_question = f'{question}\nFocus lens: {expert_lens}'

    try:
        sql_query = _generate_sql_with_openai(augmented_question)
    except Exception:
        sql_query = _default_sql(question)

    validate_sql(sql_query, settings.mcp_allowed_schema)

    databricks_token = exchange_user_assertion_for_databricks_token(
        broker_base_url=settings.mcp_broker_base_url,
        broker_shared_key=settings.mcp_broker_shared_key,
        user_assertion=assertion_token,
    )

    rows = run_query(
        server_hostname=settings.databricks_server_hostname,
        http_path=settings.databricks_http_path,
        access_token=databricks_token,
        query=sql_query,
        max_rows=settings.mcp_max_rows,
    )

    return ExpertToolResponse(
        expert=expert,
        generated_sql=sql_query,
        row_count=len(rows),
        rows=rows,
    )


@app.get('/healthz')
def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/.well-known/oauth-protected-resource')
def oauth_protected_resource_root(request: Request) -> dict[str, Any]:
    return _build_rfc9728_metadata(request)


@app.get('/.well-known/oauth-protected-resource/mcp')
def oauth_protected_resource_mcp(request: Request) -> dict[str, Any]:
    return _build_rfc9728_metadata(request, resource_identifier=_build_mcp_resource_identifier(request))


@mcp.tool(
    name='revenue_performance_expert',
    description='Analyzes revenue growth, ARR trajectory, and regional performance trends.',
)
def revenue_performance_expert(question: str) -> dict[str, Any]:
    response = _execute_expert_query(
        expert='revenue_performance_expert',
        expert_lens='Revenue growth trends, ARR movement, and region-level trajectory.',
        question=question,
        user_assertion_token=None,
    )
    return response.model_dump()


@mcp.tool(
    name='quota_attainment_expert',
    description='Analyzes quota attainment, pipeline conversion, and coverage efficiency.',
)
def quota_attainment_expert(question: str) -> dict[str, Any]:
    response = _execute_expert_query(
        expert='quota_attainment_expert',
        expert_lens='Quota attainment by quarter, conversion patterns, and pipeline coverage.',
        question=question,
        user_assertion_token=None,
    )
    return response.model_dump()


@mcp.tool(
    name='retention_margin_expert',
    description='Analyzes retention quality, discount impact, and profitability signals.',
)
def retention_margin_expert(question: str) -> dict[str, Any]:
    response = _execute_expert_query(
        expert='retention_margin_expert',
        expert_lens='Retention and discount behavior with margin/profitability implications.',
        question=question,
        user_assertion_token=None,
    )
    return response.model_dump()


app.mount('/mcp', mcp_http_app)


@app.post('/mcp/tools/ask_revenue_intelligence', response_model=AskRevenueResponse)
def ask_revenue_intelligence(
    request: AskRevenueRequest,
    authorization: str | None = Header(default=None),
) -> AskRevenueResponse:
    try:
        response = _execute_expert_query(
            expert='revenue_performance_expert',
            expert_lens='Revenue growth trends, ARR movement, and region-level trajectory.',
            question=request.question,
            user_assertion_token=_extract_user_token(authorization),
        )
        return AskRevenueResponse(
            generated_sql=response.generated_sql,
            row_count=response.row_count,
            rows=response.rows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        message = str(exc)
        if 'Query must target allowed schema' in message or 'Disallowed SQL operation' in message:
            raise HTTPException(status_code=400, detail=f'SQL guardrail violation: {message}') from exc
        raise
