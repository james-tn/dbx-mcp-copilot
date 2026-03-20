"""Tests for the thin wrapper's planner forwarding client."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner_client import PlannerServiceAuthError, PlannerServiceClient


def test_send_turn_creates_session_on_404() -> None:
    calls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/messages") and len(calls) == 1:
            return httpx.Response(404, json={"detail": "Session not found"})
        if request.url.path.endswith("/api/chat/sessions"):
            payload = {"session_id": "conversation-1", "channel": "direct_api", "turns": []}
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"reply": "planner reply"})

    transport = httpx.MockTransport(_handler)
    client = PlannerServiceClient(
        base_url="https://planner.example.com",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(transport=transport),
    )

    reply = asyncio.run(
        client.send_turn(
            session_id="conversation-1",
            text="Where should I focus?",
            access_token="planner-token",
        )
    )

    assert reply == "planner reply"
    assert calls == [
        "POST /api/chat/sessions/conversation-1/messages",
        "POST /api/chat/sessions",
        "POST /api/chat/sessions/conversation-1/messages",
    ]
    asyncio.run(client.close())


def test_send_turn_raises_auth_error_for_unauthorized_response() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "auth required"})

    transport = httpx.MockTransport(_handler)
    client = PlannerServiceClient(
        base_url="https://planner.example.com",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(transport=transport),
    )

    with pytest.raises(PlannerServiceAuthError):
        asyncio.run(
            client.send_turn(
                session_id="conversation-1",
                text="Where should I focus?",
                access_token="planner-token",
            )
        )
    asyncio.run(client.close())
