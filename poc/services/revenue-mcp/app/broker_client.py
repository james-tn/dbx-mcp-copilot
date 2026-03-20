from __future__ import annotations

import httpx


class BrokerUnauthorizedError(RuntimeError):
    pass


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    return str(data.get('detail') or data)


def _post_to_broker(
    broker_base_url: str,
    broker_shared_key: str,
    path: str,
    payload: dict,
    timeout_seconds: float = 60.0,
) -> dict:
    response = httpx.post(
        f"{broker_base_url.rstrip('/')}{path}",
        json=payload,
        headers={
            'x-service-name': 'revenue-mcp',
            'x-service-key': broker_shared_key,
        },
        timeout=timeout_seconds,
    )

    if response.status_code == 401:
        raise BrokerUnauthorizedError(_extract_error_detail(response))

    response.raise_for_status()
    return response.json()


def exchange_user_assertion_for_databricks_token(
    broker_base_url: str,
    broker_shared_key: str,
    user_assertion: str,
    operation_profile: str = 'sql.read.revenue',
    timeout_seconds: float = 60.0,
) -> str:
    payload = {
        'user_assertion': user_assertion,
        'operation_profile': operation_profile,
        'workspace': 'ri-dbx-workspace',
    }

    data = _post_to_broker(
        broker_base_url=broker_base_url,
        broker_shared_key=broker_shared_key,
        path='/broker/v1/databricks/token',
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return data['access_token']


def execute_sql_query_via_broker(
    broker_base_url: str,
    broker_shared_key: str,
    user_assertion: str,
    query: str,
    operation_profile: str = 'sql.read.revenue',
    max_rows: int | None = None,
    timeout_seconds: float = 60.0,
) -> list[dict]:
    payload = {
        'user_assertion': user_assertion,
        'operation_profile': operation_profile,
        'query': query,
    }
    if max_rows is not None:
        payload['max_rows'] = max_rows

    data = _post_to_broker(
        broker_base_url=broker_base_url,
        broker_shared_key=broker_shared_key,
        path='/api/sql/query',
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    return list(data['rows'])
