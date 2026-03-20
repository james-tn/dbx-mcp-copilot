"""Behavior tests for the thin M365 wrapper runtime."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("PLANNER_SERVICE_BASE_URL", "http://planner.example.com")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from m365_wrapper.app import WrapperRuntime, handle_wrapper_message
from m365_wrapper.planner_client import PlannerServiceAuthError, PlannerServiceError


class FakeContext:
    def __init__(self, *, text: str, session_id: str = "conversation-1", activity_type: str = "message") -> None:
        self.activity = SimpleNamespace(
            type=activity_type,
            text=text,
            conversation=SimpleNamespace(id=session_id),
        )
        self.sent_messages: list[str] = []

    async def send_activity(self, message: str) -> None:
        self.sent_messages.append(message)


class FakeAgentAuth:
    def __init__(self, token: str = "planner-token") -> None:
        self.token = token
        self.calls: list[str] = []

    async def get_token(self, context, *, auth_handler_id: str):
        self.calls.append(auth_handler_id)
        return SimpleNamespace(token=self.token)


class FakeClient:
    def __init__(self, reply: str = "planner reply", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def send_turn(self, *, session_id: str, text: str, access_token: str) -> str:
        self.calls.append(
            {
                "session_id": session_id,
                "text": text,
                "access_token": access_token,
            }
        )
        if self.error:
            raise self.error
        return self.reply


def test_handle_wrapper_message_forwards_turn_to_planner() -> None:
    client = FakeClient(reply="focus on adidas")
    runtime = WrapperRuntime(client)
    context = FakeContext(text="Where should I focus?", session_id="copilot-conversation-42")
    auth = FakeAgentAuth(token="planner-access-token")

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_agentic",
        )
    )

    assert auth.calls == ["planner_api_agentic"]
    assert client.calls == [
        {
            "session_id": "copilot-conversation-42",
            "text": "Where should I focus?",
            "access_token": "planner-access-token",
        }
    ]
    assert context.sent_messages == ["focus on adidas"]


def test_handle_wrapper_message_prompts_when_text_is_empty() -> None:
    runtime = WrapperRuntime(FakeClient())
    context = FakeContext(text="   ")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_connector",
        )
    )

    assert auth.calls == []
    assert context.sent_messages == ["Daily Account Planner is ready. Send a message to begin."]


def test_handle_wrapper_message_prompts_for_sign_in_when_token_missing() -> None:
    runtime = WrapperRuntime(FakeClient())
    context = FakeContext(text="Where should I focus?")
    auth = FakeAgentAuth(token="")

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_agentic",
        )
    )

    assert context.sent_messages == [
        "Daily Account Planner couldn't get your planner access token yet. Please sign in and try again."
    ]


def test_handle_wrapper_message_maps_planner_auth_error_to_sign_in_retry() -> None:
    runtime = WrapperRuntime(FakeClient(error=PlannerServiceAuthError("denied")))
    context = FakeContext(text="Where should I focus?")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_agentic",
        )
    )

    assert context.sent_messages == [
        "Daily Account Planner couldn't validate your delegated access right now. Please sign in again and retry."
    ]


def test_handle_wrapper_message_maps_service_error_to_temporary_unavailable() -> None:
    runtime = WrapperRuntime(FakeClient(error=PlannerServiceError("planner down")))
    context = FakeContext(text="Where should I focus?")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_agentic",
        )
    )

    assert context.sent_messages == [
        "Daily Account Planner is temporarily unavailable. Please try again in a moment."
    ]
