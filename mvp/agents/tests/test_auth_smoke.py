"""Tests for the direct Databricks auth smoke helper."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth_smoke import AUTH_SMOKE_INSTRUCTIONS, CURRENT_USER_SQL, DatabricksAuthSmokeAgent


class _FakeClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[str] = []

    def query_sql(self, statement: str):
        self.calls.append(statement)
        if self.error:
            raise self.error
        return self.rows


def test_auth_smoke_instructions_point_at_current_user_query() -> None:
    assert CURRENT_USER_SQL in AUTH_SMOKE_INSTRUCTIONS
    assert "current environment" in AUTH_SMOKE_INSTRUCTIONS


def test_probe_returns_current_user_payload() -> None:
    client = _FakeClient(rows=[{"current_user": "seller@example.com"}])

    payload = DatabricksAuthSmokeAgent(client=client).probe()

    assert payload == {
        "ok": True,
        "current_user": "seller@example.com",
        "row_count": 1,
    }
    assert client.calls == [CURRENT_USER_SQL]


def test_probe_returns_structured_error_payload() -> None:
    client = _FakeClient(error=RuntimeError("boom"))

    payload = DatabricksAuthSmokeAgent(client=client).probe()

    assert payload["ok"] is False
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "boom"


def test_run_prints_json_payload(capsys) -> None:
    client = _FakeClient(rows=[{"current_user": "demo-user"}])

    DatabricksAuthSmokeAgent(client=client).run()

    output = capsys.readouterr().out.strip()
    assert json.loads(output)["current_user"] == "demo-user"
