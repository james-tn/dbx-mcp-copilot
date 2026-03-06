from __future__ import annotations

import httpx


def exchange_user_assertion_for_databricks_token(
    broker_base_url: str,
    broker_shared_key: str,
    user_assertion: str,
    operation_profile: str = 'sql.read.revenue',
) -> str:
    payload = {
        'user_assertion': user_assertion,
        'operation_profile': operation_profile,
        'workspace': 'ri-dbx-workspace',
    }

    response = httpx.post(
        f"{broker_base_url.rstrip('/')}/broker/v1/databricks/token",
        json=payload,
        headers={
            'x-service-name': 'revenue-mcp',
            'x-service-key': broker_shared_key,
        },
        timeout=20.0,
    )

    response.raise_for_status()
    data = response.json()
    return data['access_token']
