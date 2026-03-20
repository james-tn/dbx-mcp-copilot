#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
SQL_FILE="${SQL_FILE:-$ROOT_DIR/../poc/scripts/seed-databricks-ri.sql}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' "$ENV_FILE")
  set +a
fi

if [[ ! -f "$SQL_FILE" ]]; then
  echo "Seed SQL file not found: $SQL_FILE" >&2
  exit 1
fi

if [[ -z "${DATABRICKS_HOST:-}" ]]; then
  echo "DATABRICKS_HOST is required." >&2
  exit 1
fi

if [[ -n "${DATABRICKS_PAT:-}" ]]; then
  DBX_TOKEN="$DATABRICKS_PAT"
else
  DBX_TOKEN="$(az account get-access-token --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query accessToken -o tsv)"
fi

if [[ -z "${DATABRICKS_WAREHOUSE_ID:-}" ]]; then
  DATABRICKS_WAREHOUSE_ID="$(python - <<'PY'
import json
import os
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
request = urllib.request.Request(
    f"{host}/api/2.0/sql/warehouses",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)

warehouses = payload.get("warehouses", [])
preferred = None
for warehouse in warehouses:
    state = str(warehouse.get("state", "")).upper()
    if state in {"RUNNING", "STARTING", "STARTED"}:
        preferred = warehouse
        break
if preferred is None and warehouses:
    preferred = warehouses[0]
if preferred is None:
    raise SystemExit("No Databricks SQL warehouse was found.")
print(preferred["id"])
PY
)"
fi

export DBX_TOKEN DATABRICKS_WAREHOUSE_ID SQL_FILE

python - <<'PY'
import json
import os
import time
import urllib.request

host = os.environ["DATABRICKS_HOST"].rstrip("/")
token = os.environ["DBX_TOKEN"]
warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
sql_file = os.environ["SQL_FILE"]
script = open(sql_file, encoding="utf-8").read()

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{host}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

def split_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False

    for char in sql_text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double

        if char == ";" and not in_single and not in_double:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            continue

        buffer.append(char)

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements

def run_statement(statement: str) -> dict:
    create = request(
        "POST",
        "/api/2.0/sql/statements",
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "0s",
            "disposition": "INLINE",
        },
    )

    statement_id = create["statement_id"]
    state = str(create.get("status", {}).get("state", "")).upper()

    while state in {"PENDING", "RUNNING", "QUEUED"}:
        time.sleep(2)
        poll = request("GET", f"/api/2.0/sql/statements/{statement_id}")
        state = str(poll.get("status", {}).get("state", "")).upper()
        create = poll

    if state != "SUCCEEDED":
        raise SystemExit(
            f"Seed statement failed with state: {state}\nStatement:\n{statement}\n\n{json.dumps(create, indent=2)}"
        )
    return create

statements = split_statements(script)
last_result = None
for index, statement in enumerate(statements, start=1):
    print(f"Running statement {index}/{len(statements)}...")
    last_result = run_statement(statement)

manifest = (last_result or {}).get("manifest", {})
row_count = manifest.get("total_row_count")
print(f"Seed completed successfully using warehouse {warehouse_id}.")
if row_count is not None:
    print(f"Result row count: {row_count}")
PY
