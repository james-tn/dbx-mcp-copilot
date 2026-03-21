"""Behavior tests for the thin M365 wrapper runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from types import SimpleNamespace

import httpx
from microsoft_agents.hosting.core import ApplicationOptions, MemoryStorage

os.environ.setdefault("PLANNER_SERVICE_BASE_URL", "http://planner.example.com")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from m365_wrapper.app import (
    AUTH_RETRY_MESSAGE,
    BUSY_MESSAGE,
    CompatAgentApplication,
    READY_MESSAGE,
    SIGN_IN_MESSAGE,
    UNAVAILABLE_MESSAGE,
    WORKING_MESSAGE,
    WrapperRuntime,
    handle_wrapper_message,
)
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
    def __init__(
        self,
        reply: str = "planner reply",
        error: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.reply = reply
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, str]] = []

    async def send_turn(self, *, session_id: str, text: str, access_token: str) -> str:
        self.calls.append(
            {
                "session_id": session_id,
                "text": text,
                "access_token": access_token,
            }
        )
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        return self.reply


class ContinueConversationSpy:
    __signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "agent_app_id",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ),
            inspect.Parameter(
                "continuation_activity",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ),
            inspect.Parameter(
                "callback",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ),
        ]
    )

    def __init__(self, callback_context) -> None:
        self.callback_context = callback_context
        self.args = None
        self.kwargs = None

    async def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return await args[2](self.callback_context)


class FakeAdapter:
    def __init__(self, callback_context) -> None:
        self.continue_conversation = ContinueConversationSpy(callback_context)


def test_compat_agent_application_uses_activity_reference_and_positional_adapter_call() -> None:
    continuation_activity = SimpleNamespace(type="event", id="continued-activity", relates_to="conversation-ref")
    reference = SimpleNamespace()
    reference_calls = {"count": 0}

    def _get_continuation_activity():
        reference_calls["count"] += 1
        return continuation_activity

    reference.get_continuation_activity = _get_continuation_activity
    activity_calls = {"count": 0}

    def _get_conversation_reference():
        activity_calls["count"] += 1
        return reference

    activity = SimpleNamespace(
        type="message",
        id="activity-1",
        text="tell me what to focus on",
        conversation=SimpleNamespace(id="conversation-1"),
        get_conversation_reference=_get_conversation_reference,
    )
    callback_context = SimpleNamespace(activity=continuation_activity)
    adapter = FakeAdapter(callback_context)
    agent_app = CompatAgentApplication(
        options=ApplicationOptions(
            adapter=adapter,
            bot_app_id="bot-app-id",
            storage=MemoryStorage(),
            long_running_messages=True,
        )
    )
    original_context = SimpleNamespace(activity=activity)
    seen = {}

    async def _callback(turn_context):
        seen["context"] = turn_context
        return "ok"

    result = asyncio.run(agent_app._start_long_running_call(original_context, _callback))

    assert result == "ok"
    assert activity_calls["count"] == 1
    assert reference_calls["count"] == 1
    assert adapter.continue_conversation.args[0] == "bot-app-id"
    assert adapter.continue_conversation.args[1] == continuation_activity
    assert callable(adapter.continue_conversation.args[2])
    assert adapter.continue_conversation.kwargs == {}
    assert seen["context"] is not callback_context
    assert seen["context"].activity.type == "message"


def test_compat_agent_application_restores_original_message_activity_for_callback() -> None:
    continuation_activity = SimpleNamespace(type="event", id="continued-activity", relates_to="continuation-ref")
    reference = SimpleNamespace(get_continuation_activity=lambda: continuation_activity)
    original_activity = SimpleNamespace(
        type="message",
        id="activity-1",
        text="morning briefing please",
        conversation=SimpleNamespace(id="conversation-1"),
        get_conversation_reference=lambda: reference,
        relates_to=None,
    )
    callback_context = SimpleNamespace(activity=continuation_activity)
    adapter = FakeAdapter(callback_context)
    agent_app = CompatAgentApplication(
        options=ApplicationOptions(
            adapter=adapter,
            bot_app_id="bot-app-id",
            storage=MemoryStorage(),
            long_running_messages=True,
        )
    )
    original_context = SimpleNamespace(activity=original_activity)
    seen = {}

    async def _callback(turn_context):
        seen["activity"] = turn_context.activity
        seen["context"] = turn_context
        return "ok"

    result = asyncio.run(agent_app._start_long_running_call(original_context, _callback))

    assert result == "ok"
    assert seen["context"] is not callback_context
    assert seen["activity"] is not original_activity
    assert seen["activity"].type == "message"
    assert seen["activity"].text == "morning briefing please"
    assert seen["activity"].relates_to == "continuation-ref"


def test_compat_agent_application_fails_startup_for_unsupported_adapter_signature() -> None:
    class UnsupportedAdapter:
        async def continue_conversation(self, *, reference, callback, bot_app_id):
            return None

    try:
        CompatAgentApplication(
            options=ApplicationOptions(
                adapter=UnsupportedAdapter(),
                bot_app_id="bot-app-id",
                storage=MemoryStorage(),
                long_running_messages=True,
            )
        )
    except RuntimeError as exc:
        assert "positional adapter parameters" in str(exc)
    else:
        raise AssertionError("Expected unsupported adapter signature to fail startup.")


def test_handle_wrapper_message_forwards_turn_to_planner() -> None:
    client = FakeClient(reply="focus on adidas")
    runtime = WrapperRuntime(
        client,
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
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
    runtime = WrapperRuntime(
        FakeClient(),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
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
    assert context.sent_messages == [READY_MESSAGE]


def test_handle_wrapper_message_prompts_for_sign_in_when_token_missing() -> None:
    runtime = WrapperRuntime(
        FakeClient(),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
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

    assert context.sent_messages == [SIGN_IN_MESSAGE]


def test_handle_wrapper_message_maps_planner_auth_error_to_sign_in_retry() -> None:
    runtime = WrapperRuntime(
        FakeClient(error=PlannerServiceAuthError("denied")),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
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

    assert context.sent_messages == [AUTH_RETRY_MESSAGE]


def test_handle_wrapper_message_maps_service_error_to_temporary_unavailable() -> None:
    runtime = WrapperRuntime(
        FakeClient(error=PlannerServiceError("planner down")),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
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

    assert context.sent_messages == [UNAVAILABLE_MESSAGE]


def test_handle_wrapper_message_sends_delayed_ack_for_long_running_requests() -> None:
    runtime = WrapperRuntime(
        FakeClient(reply="full briefing", delay_seconds=0.05),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.01,
    )
    context = FakeContext(text="Give me my morning briefing")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_connector",
        )
    )

    assert context.sent_messages == [WORKING_MESSAGE, "full briefing"]


def test_handle_wrapper_message_does_not_send_ack_before_threshold() -> None:
    runtime = WrapperRuntime(
        FakeClient(reply="quick reply", delay_seconds=0.005),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.05,
    )
    context = FakeContext(text="Where should I focus?")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_connector",
        )
    )

    assert context.sent_messages == ["quick reply"]


def test_handle_wrapper_message_rejects_new_turn_when_session_is_busy() -> None:
    runtime = WrapperRuntime(
        FakeClient(),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
    asyncio.run(runtime.try_begin_turn("conversation-1"))
    context = FakeContext(text="hello again", session_id="conversation-1")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_connector",
        )
    )

    assert context.sent_messages == [BUSY_MESSAGE]
    asyncio.run(runtime.finish_turn("conversation-1"))


def test_handle_wrapper_message_maps_timeout_to_temporary_unavailable() -> None:
    runtime = WrapperRuntime(
        FakeClient(error=httpx.ReadTimeout("timed out")),
        long_running_messages_enabled=True,
        ack_threshold_seconds=0.5,
    )
    context = FakeContext(text="Give me my morning briefing")
    auth = FakeAgentAuth()

    asyncio.run(
        handle_wrapper_message(
            context=context,
            agent_auth=auth,
            runtime=runtime,
            auth_handler_id="planner_api_connector",
        )
    )

    assert context.sent_messages == [UNAVAILABLE_MESSAGE]
