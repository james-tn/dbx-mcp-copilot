"""Tests for the stateful planner API endpoints."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api
from auth_context import TokenClaims
from session_store import PlannerSessionState, SessionTurn


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def create_session(self, *, owner_id: str, session_id: str | None = None):
        self.calls.append(("create_session", owner_id, session_id))
        return {
            "session_id": session_id or "session-1",
            "channel": "direct_api",
            "conversation_id": None,
            "created_at": 1.0,
            "last_accessed_at": 1.0,
            "turns": [],
        }

    async def get_session(self, *, session_id: str, owner_id: str):
        self.calls.append(("get_session", session_id, owner_id))
        return {
            "session_id": session_id,
            "channel": "direct_api",
            "conversation_id": None,
            "created_at": 1.0,
            "last_accessed_at": 1.0,
            "turns": [],
        }

    async def run_direct_turn(self, *, session_id: str, owner_id: str, text: str):
        self.calls.append(("run_direct_turn", session_id, owner_id, text))
        return {
            "session_id": session_id,
            "reply": f"echo: {text}",
            "channel": "direct_api",
            "turns": [{"role": "user", "text": text, "created_at": 1.0}],
        }


def test_healthz_is_open() -> None:
    async def _run():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.get("/healthz")

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_session_requires_auth(monkeypatch) -> None:
    async def _run():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post("/api/chat/sessions", json={})

    response = asyncio.run(_run())
    assert response.status_code == 401


def test_create_session_uses_authenticated_owner(monkeypatch) -> None:
    fake_runtime = _FakeRuntime()
    monkeypatch.setattr(api, "runtime", fake_runtime)
    monkeypatch.setattr(
        api,
        "validate_bearer_token",
        lambda token: TokenClaims(
            oid="user-123",
            tid="tenant",
            upn="seller@example.com",
            aud="api://planner-api",
            scp="access_as_user",
        ),
    )

    async def _run():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post(
                "/api/chat/sessions",
                json={},
                headers={"Authorization": "Bearer planner-token"},
            )

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert response.json()["session_id"] == "session-1"
    assert fake_runtime.calls == [("create_session", "user-123", None)]


def test_planner_bot_ingress_is_not_exposed() -> None:
    async def _run():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post("/api/messages", json={})

    response = asyncio.run(_run())
    assert response.status_code == 404


def test_message_history_for_state_uses_clean_text_messages() -> None:
    state = PlannerSessionState(
        session_id="session-1",
        owner_id="user-1",
        channel="direct_api",
        agent_session=None,
        turns=[
            SessionTurn(role="user", text="Where should I focus today?", created_at=1.0),
            SessionTurn(role="assistant", text="Here are the top accounts.", created_at=2.0),
            SessionTurn(role="user", text="Go deeper on the top one.", created_at=3.0),
        ],
    )

    messages = api._message_history_for_state(state)

    assert [(message.role, message.text) for message in messages] == [
        ("user", "Where should I focus today?"),
        ("assistant", "Here are the top accounts."),
        ("user", "Go deeper on the top one."),
    ]


def test_stream_session_message_returns_sse(monkeypatch) -> None:
    class _StreamingRuntime(_FakeRuntime):
        async def stream_direct_turn(self, *, session_id: str, owner_id: str, text: str):
            self.calls.append(("stream_direct_turn", session_id, owner_id, text))
            yield 'event: ack\ndata: {"event":"ack"}\n\n'
            yield 'event: final\ndata: {"event":"final","reply":"echo: streamed"}\n\n'

    fake_runtime = _StreamingRuntime()
    monkeypatch.setattr(api, "runtime", fake_runtime)
    monkeypatch.setattr(
        api,
        "validate_bearer_token",
        lambda token: TokenClaims(
            oid="user-123",
            tid="tenant",
            upn="seller@example.com",
            aud="api://planner-api",
            scp="access_as_user",
        ),
    )

    async def _run():
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post(
                "/api/chat/sessions/session-1/messages/stream",
                json={"text": "stream me"},
                headers={"Authorization": "Bearer planner-token"},
            )

    response = asyncio.run(_run())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: ack" in response.text
    assert "event: final" in response.text
    assert fake_runtime.calls == [("stream_direct_turn", "session-1", "user-123", "stream me")]
