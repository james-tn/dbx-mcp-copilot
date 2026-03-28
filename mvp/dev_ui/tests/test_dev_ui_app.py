"""Tests for the local planner chat UI."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import dev_ui.app as dev_app


def _claims(upn: str = "seller@example.com", user_id: str = "user-123") -> SimpleNamespace:
    return SimpleNamespace(upn=upn, user_id=user_id)


async def _request(method: str, path: str, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=dev_app.app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_home_shows_sign_in_and_hides_manual_token(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "local-client")
    monkeypatch.setenv("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "api://botid-local-bot")
    monkeypatch.setenv("PLANNER_API_BASE_URL", "http://planner.example.com")
    dev_app._LOCAL_AUTH_SESSIONS.clear()
    dev_app._PENDING_SIGN_INS.clear()

    response = asyncio.run(_request("GET", "/"))

    assert response.status_code == 200
    assert "Sign in required." in response.text
    assert "Caller bearer token" not in response.text
    assert "Ready to chat" in response.text


def test_auth_start_uses_cached_token(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "local-client")
    monkeypatch.setenv("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "api://botid-local-bot")
    monkeypatch.setenv("PLANNER_API_BASE_URL", "http://planner.example.com")
    dev_app._LOCAL_AUTH_SESSIONS.clear()
    dev_app._PENDING_SIGN_INS.clear()
    monkeypatch.setattr(dev_app, "_try_acquire_cached_caller_token", lambda: ("caller-token", _claims()))

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=dev_app.app),
            base_url="http://test",
            follow_redirects=True,
        ) as client:
            return await client.post("/auth/start")

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert "Signed in as <strong>seller@example.com</strong>" in response.text
    assert len(dev_app._LOCAL_AUTH_SESSIONS) == 1
    assert next(iter(dev_app._LOCAL_AUTH_SESSIONS.values())).caller_bearer == "caller-token"


def test_chat_requires_sign_in(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "local-client")
    monkeypatch.setenv("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "api://botid-local-bot")
    monkeypatch.setenv("PLANNER_API_BASE_URL", "http://planner.example.com")
    dev_app._LOCAL_AUTH_SESSIONS.clear()
    dev_app._PENDING_SIGN_INS.clear()

    response = asyncio.run(_request("POST", "/chat", data={"prompt": "hello", "session_id": ""}))

    assert response.status_code == 401
    assert "Sign in first before sending chat turns." in response.text


def test_chat_uses_signed_in_session(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "local-client")
    monkeypatch.setenv("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "api://botid-local-bot")
    monkeypatch.setenv("PLANNER_API_BASE_URL", "http://planner.example.com")
    monkeypatch.setenv("WRAPPER_FORWARD_TIMEOUT_SECONDS", "30")
    dev_app._LOCAL_AUTH_SESSIONS.clear()
    dev_app._PENDING_SIGN_INS.clear()
    monkeypatch.setattr(dev_app, "_try_acquire_cached_caller_token", lambda: ("caller-token", _claims()))
    monkeypatch.setattr(dev_app, "extract_bearer_token", lambda authorization: authorization.split(" ", 1)[1])
    monkeypatch.setattr(dev_app, "validate_debug_token", lambda token, *, expected_audience: _claims())
    monkeypatch.setattr(
        dev_app,
        "acquire_planner_token_on_behalf_of",
        lambda *, user_assertion, expected_audience: "planner-obo-token",
    )

    captured: dict[str, str] = {}

    class _FakePlannerServiceClient:
        def __init__(self, *, base_url: str, timeout_seconds: float, http_client=None) -> None:
            captured["base_url"] = base_url
            captured["timeout_seconds"] = str(timeout_seconds)

        async def send_turn_payload(self, *, session_id: str, text: str, access_token: str) -> dict[str, object]:
            captured["session_id"] = session_id
            captured["text"] = text
            captured["access_token"] = access_token
            return {
                "reply": "planner reply",
                "turns": [
                    {"role": "user", "text": text},
                    {"role": "assistant", "text": "planner reply"},
                ],
            }

    monkeypatch.setattr(dev_app, "PlannerServiceClient", _FakePlannerServiceClient)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=dev_app.app),
            base_url="http://test",
            follow_redirects=True,
        ) as client:
            await client.post("/auth/start")
            return await client.post(
                "/chat",
                data={"prompt": "where should I focus?", "session_id": "local-session-1"},
            )

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert "planner reply" in response.text
    assert "Signed in as <strong>seller@example.com</strong>" in response.text
    assert "Daily Account Planner" in response.text
    assert captured == {
        "base_url": "http://planner.example.com",
        "timeout_seconds": "30.0",
        "session_id": "local-session-1",
        "text": "where should I focus?",
        "access_token": "planner-obo-token",
    }
    session = next(iter(dev_app._LOCAL_AUTH_SESSIONS.values()))
    assert session.planner_session_id == "local-session-1"
    assert session.turns == [
        {"role": "user", "text": "where should I focus?"},
        {"role": "assistant", "text": "planner reply"},
    ]
