"""Tests for the simulated DAP app contract."""

from __future__ import annotations

import asyncio
import os
import sys

from httpx import ASGITransport, AsyncClient

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from mvp.databricks_apps.dap_simulator.app import app


def test_simulator_healthcheck() -> None:
    async def _run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/v1/healthcheck")

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert response.json()["status"] == "OK"


def test_simulator_accepts_forwarded_token_in_local_bypass(monkeypatch) -> None:
    monkeypatch.setenv("DAP_SIMULATOR_LOCAL_DEV_BYPASS_AUTH", "true")

    async def _run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/api/v1/accounts/query",
                json={"sales_team": "ENT-APAC-01", "row_limit": 2},
                headers={"X-Forwarded-Access-Token": "dev-token"},
            )

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert response.json()["row_count"] == 2


def test_simulator_debug_headers_reports_both_token_paths(monkeypatch) -> None:
    monkeypatch.setenv("DAP_SIMULATOR_LOCAL_DEV_BYPASS_AUTH", "true")

    async def _run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/api/v1/debug/headers",
                headers={
                    "Authorization": "Bearer planner-token",
                    "X-Forwarded-Access-Token": "forwarded-token",
                },
            )

    response = asyncio.run(_run())
    payload = response.json()

    assert response.status_code == 200
    assert payload["has_authorization"] is True
    assert payload["has_x_forwarded_access_token"] is True
