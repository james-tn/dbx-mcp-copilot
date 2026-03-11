from __future__ import annotations

import databricks.sql as sql


def run_query(
    server_hostname: str,
    http_path: str,
    access_token: str,
    query: str,
    max_rows: int,
) -> list[dict[str, object]]:
    with sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchmany(max_rows)
            columns = [col[0] for col in (cursor.description or [])]

    result: list[dict[str, object]] = []
    for row in rows:
        item = {columns[index]: value for index, value in enumerate(row)}
        result.append(item)
    return result