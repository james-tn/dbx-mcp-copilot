"""
Thin Microsoft 365 custom-engine wrapper for the Daily Account Planner.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import copy
import functools
import inspect
import importlib.metadata
import logging
import time
from typing import Any, Awaitable, Callable, Protocol

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from microsoft_agents.activity import Activity, ActivityTypes, InvokeResponse
from microsoft_agents.hosting.core import AgentApplication, ApplicationOptions, Authorization, MemoryStorage
from microsoft_agents.hosting.core.turn_context import TurnContext
from microsoft_agents.hosting.fastapi import CloudAdapter, JwtAuthorizationMiddleware, start_agent_process
from pydantic import BaseModel, Field

try:
    from .config import (
        get_bot_app_id,
        build_auth_handlers,
        build_connection_manager,
        get_handler_ids,
        get_planner_service_base_url,
        get_wrapper_debug_allowed_upns,
        get_wrapper_debug_chat_enabled,
        get_wrapper_debug_expected_audience,
        get_wrapper_ack_threshold_seconds,
        get_wrapper_incremental_delivery_enabled,
        get_wrapper_long_running_messages_enabled,
        get_wrapper_timeout_seconds,
    )
    from .debug_auth import (
        DebugAuthConfigurationError,
        DebugAuthOboError,
        DebugAuthValidationError,
        acquire_planner_token_on_behalf_of,
        extract_bearer_token,
        validate_debug_token,
    )
    from .planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError
except ImportError:
    from config import (
        get_bot_app_id,
        build_auth_handlers,
        build_connection_manager,
        get_handler_ids,
        get_planner_service_base_url,
        get_wrapper_debug_allowed_upns,
        get_wrapper_debug_chat_enabled,
        get_wrapper_debug_expected_audience,
        get_wrapper_ack_threshold_seconds,
        get_wrapper_incremental_delivery_enabled,
        get_wrapper_long_running_messages_enabled,
        get_wrapper_timeout_seconds,
    )
    from debug_auth import (
        DebugAuthConfigurationError,
        DebugAuthOboError,
        DebugAuthValidationError,
        acquire_planner_token_on_behalf_of,
        extract_bearer_token,
        validate_debug_token,
    )
    from planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError

logger = logging.getLogger(__name__)

READY_MESSAGE = "Daily Account Planner is ready. Send a message to begin."
SIGN_IN_MESSAGE = (
    "Daily Account Planner couldn't get your planner access token yet. Please sign in and try again."
)
AUTH_RETRY_MESSAGE = (
    "Daily Account Planner couldn't validate your delegated access right now. "
    "Please sign in again and retry."
)
UNAVAILABLE_MESSAGE = "Daily Account Planner is temporarily unavailable. Please try again in a moment."
WORKING_MESSAGE = "I'm still working on this request. It may take some time."
BUSY_MESSAGE = "I'm still working on your previous request. I'll send the result here when it's ready."
CHANNEL_SEND_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
CHANNEL_SEND_MAX_ATTEMPTS = 3


class SessionBusyError(RuntimeError):
    """Raised when a wrapper session already has an active planner turn."""


class DebugChatRequest(BaseModel):
    text: str = Field(..., min_length=1)
    session_id: str | None = None


class DebugChatResponse(BaseModel):
    session_id: str
    reply: str
    user_id: str
    upn: str | None = None


def _channel_send_status_code(exc: Exception) -> int | None:
    for attribute_name in ("status", "status_code"):
        value = getattr(exc, attribute_name, None)
        if isinstance(value, int):
            return value
    return None


def _is_retryable_channel_send_error(exc: Exception) -> bool:
    status_code = _channel_send_status_code(exc)
    return status_code in CHANNEL_SEND_RETRYABLE_STATUS_CODES


async def _send_activity_with_retry(
    context: Any,
    activity_or_text: Any,
    *,
    session_id: str,
    auth_handler_id: str,
    purpose: str,
) -> bool:
    for attempt in range(1, CHANNEL_SEND_MAX_ATTEMPTS + 1):
        try:
            await context.send_activity(activity_or_text)
            return True
        except Exception as exc:
            if attempt >= CHANNEL_SEND_MAX_ATTEMPTS or not _is_retryable_channel_send_error(exc):
                logger.exception(
                    "Wrapper failed to send channel activity.",
                    extra={
                        "session_id": session_id,
                        "auth_handler_id": auth_handler_id,
                        "purpose": purpose,
                        "attempt": attempt,
                        "status_code": _channel_send_status_code(exc),
                    },
                )
                return False
            logger.warning(
                "Wrapper send_activity hit transient channel failure; retrying.",
                extra={
                    "session_id": session_id,
                    "auth_handler_id": auth_handler_id,
                    "purpose": purpose,
                    "attempt": attempt,
                    "status_code": _channel_send_status_code(exc),
                },
            )
            await asyncio.sleep(0.5 * attempt)
    return False


def _get_agents_sdk_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name in (
        "microsoft-agents-hosting-core",
        "microsoft-agents-hosting-fastapi",
        "microsoft-agents-authentication-msal",
    ):
        try:
            versions[package_name] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            versions[package_name] = "missing"
    return versions


def _get_continue_conversation_signature(adapter: Any) -> inspect.Signature:
    try:
        continue_conversation = getattr(adapter, "continue_conversation")
    except AttributeError as exc:
        raise RuntimeError("Wrapper adapter is missing continue_conversation().") from exc

    try:
        return inspect.signature(continue_conversation)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Wrapper could not inspect adapter continue_conversation() signature.") from exc


def _validate_long_running_adapter(adapter: Any, bot_app_id: str) -> inspect.Signature:
    if not bot_app_id:
        raise RuntimeError("Wrapper long-running mode requires BOT_APP_ID to be configured.")

    signature = _get_continue_conversation_signature(adapter)
    parameters = list(signature.parameters.values())
    if len(parameters) != 3:
        raise RuntimeError(
            "Wrapper long-running compatibility path requires continue_conversation("
            "agent_app_id, continuation_activity, callback). "
            f"Observed signature: {signature}"
        )

    invalid_parameter = next(
        (
            parameter
            for parameter in parameters
            if parameter.kind
            not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ),
        None,
    )
    if invalid_parameter is not None:
        raise RuntimeError(
            "Wrapper long-running compatibility path requires positional adapter parameters. "
            f"Observed signature: {signature}"
        )

    return signature


def _build_resumed_message_activity(original_activity: Any, proactive_activity: Any) -> Any:
    """Restore the original inbound message shape without preserving a stale reply target."""

    resumed_activity = copy(original_activity)
    resumed_activity.relates_to = getattr(
        proactive_activity,
        "relates_to",
        getattr(resumed_activity, "relates_to", None),
    )
    # Keep routing/auth inputs from the original message, but force outbound sends to
    # use the proactive conversation context instead of replying to an old activity id.
    resumed_activity.id = None
    resumed_activity.reply_to_id = None
    return resumed_activity


class CompatAgentApplication(AgentApplication):
    """Local compatibility wrapper for the broken SDK proactive long-running bridge."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        adapter = getattr(self, "_adapter", None)
        signature = _get_continue_conversation_signature(adapter) if adapter is not None else None
        logger.warning(
            "Wrapper Agents SDK startup diagnostics.",
            extra={
                "sdk_versions": _get_agents_sdk_versions(),
                "adapter_class": adapter.__class__.__name__ if adapter is not None else None,
                "continue_conversation_signature": str(signature) if signature is not None else None,
                "compatibility_path": "wrapper_owned_long_running_bridge",
                "long_running_messages": bool(getattr(self.options, "long_running_messages", False)),
            },
        )
        if getattr(self.options, "long_running_messages", False):
            _validate_long_running_adapter(adapter, self.options.bot_app_id)
            logger.warning(
                "Wrapper selected compatibility proactive bridge for long-running messages.",
                extra={"bot_app_id": self.options.bot_app_id},
            )

    async def _start_long_running_call(
        self,
        context: TurnContext,
        func: Callable[[TurnContext], Awaitable[Any]],
    ) -> Any:
        if (
            self._adapter
            and context.activity is not None
            and context.activity.type == "message"
            and self._options.long_running_messages
        ):
            session_id = str(
                getattr(getattr(context.activity, "conversation", None), "id", "") or ""
            ).strip()
            logger.warning(
                "Wrapper entered compatibility proactive bridge.",
                extra={
                    "session_id": session_id,
                    "activity_id": getattr(context.activity, "id", None),
                },
            )
            original_activity = copy(context.activity)
            reference = context.activity.get_conversation_reference()
            continuation_activity = reference.get_continuation_activity()

            async def _resume_with_original_activity(proactive_context: TurnContext) -> Any:
                resumed_context = copy(proactive_context)
                resumed_context.activity = _build_resumed_message_activity(
                    original_activity,
                    proactive_context.activity,
                )
                return await func(resumed_context)

            return await self._adapter.continue_conversation(
                self.options.bot_app_id,
                continuation_activity,
                _resume_with_original_activity,
            )

        return await func(context)


class WrapperRuntime:
    def __init__(
        self,
        client: PlannerServiceClient,
        *,
        long_running_messages_enabled: bool,
        ack_threshold_seconds: float,
        incremental_delivery_enabled: bool = False,
    ) -> None:
        self.client = client
        self.long_running_messages_enabled = long_running_messages_enabled
        self.ack_threshold_seconds = ack_threshold_seconds
        self.incremental_delivery_enabled = incremental_delivery_enabled
        self._busy_sessions: set[str] = set()
        self._busy_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def forward_message(
        self,
        *,
        session_id: str,
        text: str,
        planner_access_token: str,
        event_handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        if hasattr(self.client, "collect_streamed_turn"):
            payload = await self.client.collect_streamed_turn(
                session_id=session_id,
                text=text,
                access_token=planner_access_token,
                event_handler=event_handler,
            )
            return str(payload.get("reply", "") or "").strip()
        return await self.client.send_turn(
            session_id=session_id,
            text=text,
            access_token=planner_access_token,
        )

    async def try_begin_turn(self, session_id: str) -> bool:
        async with self._busy_lock:
            if session_id in self._busy_sessions:
                return False
            self._busy_sessions.add(session_id)
            return True

    async def finish_turn(self, session_id: str) -> None:
        async with self._busy_lock:
            self._busy_sessions.discard(session_id)

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def wait_for_background_tasks(self) -> None:
        if not self._background_tasks:
            return
        await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)


@asynccontextmanager
async def _reserve_turn_slot(runtime: WrapperRuntime, session_id: str):
    if not await runtime.try_begin_turn(session_id):
        raise SessionBusyError(session_id)
    try:
        yield
    finally:
        await runtime.finish_turn(session_id)


async def _run_direct_wrapper_turn(
    *,
    runtime: WrapperRuntime,
    session_id: str,
    text: str,
    planner_access_token: str,
) -> str:
    try:
        async with _reserve_turn_slot(runtime, session_id):
            return await runtime.forward_message(
                session_id=session_id,
                text=text,
                planner_access_token=planner_access_token,
            )
    except SessionBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=BUSY_MESSAGE) from exc


class _AgentAuth(Protocol):
    async def get_token(self, context: Any, *, auth_handler_id: str):
        ...


def _map_planner_exception(exc: Exception) -> str:
    if isinstance(exc, PlannerServiceAuthError):
        return AUTH_RETRY_MESSAGE
    if isinstance(exc, (PlannerServiceError, httpx.TimeoutException)):
        return UNAVAILABLE_MESSAGE
    return UNAVAILABLE_MESSAGE


async def _await_planner_reply(
    planner_task: asyncio.Task[str],
    *,
    session_id: str,
    auth_handler_id: str,
    after_ack: bool,
) -> tuple[str, str]:
    try:
        return await planner_task, "none"
    except Exception as exc:
        failure_reason = exc.__class__.__name__
        if after_ack:
            logger.exception(
                "Wrapper planner call failed after delayed acknowledgement.",
                extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
        elif isinstance(exc, PlannerServiceAuthError):
            logger.warning(
                "Wrapper planner call hit auth error.",
                extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
        elif isinstance(exc, httpx.TimeoutException):
            logger.warning(
                "Wrapper planner call timed out.",
                extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
        else:
            logger.exception(
                "Wrapper planner call failed before acknowledgement threshold.",
                extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
        return _map_planner_exception(exc), failure_reason


def _build_continuation_activity(context: Any, *, session_id: str, auth_handler_id: str) -> Any | None:
    activity = getattr(context, "activity", None)
    if activity is None:
        return None
    try:
        reference = activity.get_conversation_reference()
        return reference.get_continuation_activity()
    except Exception:
        logger.exception(
            "Wrapper could not create a continuation activity for deferred delivery.",
            extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
        )
        return None


async def _send_proactive_activity(
    *,
    adapter: Any,
    bot_app_id: str,
    continuation_activity: Any,
    activity_or_text: Any,
    session_id: str,
    auth_handler_id: str,
    purpose: str,
) -> bool:
    delivered = False

    async def _callback(proactive_context: TurnContext) -> bool:
        nonlocal delivered
        delivered = await _send_activity_with_retry(
            proactive_context,
            activity_or_text,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            purpose=purpose,
        )
        return delivered

    try:
        await adapter.continue_conversation(
            bot_app_id,
            continuation_activity,
            _callback,
        )
    except Exception:
        logger.exception(
            "Wrapper failed to resume the conversation for deferred delivery.",
            extra={"session_id": session_id, "auth_handler_id": auth_handler_id, "purpose": purpose},
        )
        return False

    return delivered


async def _complete_deferred_turn(
    *,
    runtime: WrapperRuntime,
    planner_task: asyncio.Task[str],
    adapter: Any,
    bot_app_id: str,
    continuation_activity: Any,
    session_id: str,
    auth_handler_id: str,
    started: float,
) -> None:
    failure_reason = "none"
    try:
        reply, failure_reason = await _await_planner_reply(
            planner_task,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            after_ack=True,
        )
        delivered = await _send_proactive_activity(
            adapter=adapter,
            bot_app_id=bot_app_id,
            continuation_activity=continuation_activity,
            activity_or_text=reply or UNAVAILABLE_MESSAGE,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            purpose="planner_reply",
        )
        if not delivered:
            failure_reason = "channel_delivery_failed"
    except Exception:
        failure_reason = "unexpected_background_error"
        logger.exception(
            "Wrapper deferred delivery failed unexpectedly.",
            extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
        )
    finally:
        await runtime.finish_turn(session_id)
        logger.info(
            "Wrapper turn completed.",
            extra={
                "session_id": session_id,
                "auth_handler_id": auth_handler_id,
                "long_running_enabled": runtime.long_running_messages_enabled,
                "ack_sent": True,
                "failure_reason": failure_reason,
                "planner_elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            },
        )


async def handle_wrapper_message(
    *,
    context: Any,
    agent_auth: _AgentAuth,
    runtime: WrapperRuntime,
    auth_handler_id: str,
    delivery_adapter: Any | None = None,
    delivery_bot_app_id: str = "",
) -> None:
    text = (context.activity.text or "").strip()
    if not text:
        logger.warning(
            "Wrapper received message turn without text.",
            extra={
                "activity_type": getattr(context.activity, "type", None),
                "activity_name": getattr(context.activity, "name", None),
                "has_value": getattr(context.activity, "value", None) is not None,
                "has_channel_data": getattr(context.activity, "channel_data", None) is not None,
                "session_id": str(
                    getattr(getattr(context.activity, "conversation", None), "id", "") or ""
                ).strip(),
            },
        )
        await _send_activity_with_retry(
            context,
            READY_MESSAGE,
            session_id=str(
                getattr(getattr(context.activity, "conversation", None), "id", "") or ""
            ).strip(),
            auth_handler_id="none",
            purpose="ready_message",
        )
        return

    session_id = str(getattr(getattr(context.activity, "conversation", None), "id", "") or "").strip()
    if not session_id:
        await _send_activity_with_retry(
            context,
            UNAVAILABLE_MESSAGE,
            session_id="",
            auth_handler_id=auth_handler_id,
            purpose="missing_session_unavailable_message",
        )
        return

    try:
        token_response = await agent_auth.get_token(context, auth_handler_id=auth_handler_id)
        planner_access_token = str(getattr(token_response, "token", "") or "").strip()
    except Exception:
        logger.exception(
            "Wrapper token acquisition failed.",
            extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
        )
        await _send_activity_with_retry(
            context,
            UNAVAILABLE_MESSAGE,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            purpose="token_failure_unavailable_message",
        )
        return

    if not planner_access_token:
        await _send_activity_with_retry(
            context,
            SIGN_IN_MESSAGE,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            purpose="sign_in_message",
        )
        return

    started = time.perf_counter()
    ack_sent = False
    failure_reason = "none"
    completion_deferred = False
    response_already_delivered = False
    logger.info(
        "Wrapper turn started.",
        extra={
            "session_id": session_id,
            "auth_handler_id": auth_handler_id,
            "long_running_enabled": runtime.long_running_messages_enabled,
            "ack_threshold_seconds": runtime.ack_threshold_seconds,
        },
    )
    try:
        if not await runtime.try_begin_turn(session_id):
            logger.info(
                "Wrapper rejected message while session was busy.",
                extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
            await _send_activity_with_retry(
                context,
                BUSY_MESSAGE,
                session_id=session_id,
                auth_handler_id=auth_handler_id,
                purpose="busy_message",
            )
            return

        planner_task = asyncio.create_task(
            runtime.forward_message(
                session_id=session_id,
                text=text,
                planner_access_token=planner_access_token,
            )
        )

        try:
            if runtime.long_running_messages_enabled:
                reply = await asyncio.wait_for(
                    asyncio.shield(planner_task),
                    timeout=runtime.ack_threshold_seconds,
                )
            else:
                reply = await planner_task
        except asyncio.TimeoutError:
            ack_sent = True
            logger.warning(
                "Wrapper crossed long-running acknowledgement threshold.",
                extra={
                    "session_id": session_id,
                    "auth_handler_id": auth_handler_id,
                    "ack_threshold_seconds": runtime.ack_threshold_seconds,
                },
            )
            continuation_activity = _build_continuation_activity(
                context,
                session_id=session_id,
                auth_handler_id=auth_handler_id,
            )
            await _send_activity_with_retry(
                context,
                WORKING_MESSAGE,
                session_id=session_id,
                auth_handler_id=auth_handler_id,
                purpose="working_message",
            )

            if continuation_activity is None or delivery_adapter is None or not delivery_bot_app_id:
                reply, failure_reason = await _await_planner_reply(
                    planner_task,
                    session_id=session_id,
                    auth_handler_id=auth_handler_id,
                    after_ack=True,
                )
                delivered = await _send_activity_with_retry(
                    context,
                    reply or UNAVAILABLE_MESSAGE,
                    session_id=session_id,
                    auth_handler_id=auth_handler_id,
                    purpose="planner_reply",
                )
                if not delivered:
                    failure_reason = "channel_delivery_failed"
                response_already_delivered = True
            else:
                completion_deferred = True
                logger.info(
                    "Wrapper deferred the final reply to background proactive delivery.",
                    extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
                )
                runtime.track_background_task(
                    asyncio.create_task(
                        _complete_deferred_turn(
                            runtime=runtime,
                            planner_task=planner_task,
                            adapter=delivery_adapter,
                            bot_app_id=delivery_bot_app_id,
                            continuation_activity=continuation_activity,
                            session_id=session_id,
                            auth_handler_id=auth_handler_id,
                            started=started,
                        )
                    )
                )
                logger.info(
                    "Wrapper turn handed off for deferred completion.",
                    extra={
                        "session_id": session_id,
                        "auth_handler_id": auth_handler_id,
                        "long_running_enabled": runtime.long_running_messages_enabled,
                        "ack_sent": ack_sent,
                    },
                )
                return
        except Exception as exc:
            failure_reason = exc.__class__.__name__
            if isinstance(exc, PlannerServiceAuthError):
                logger.warning(
                    "Wrapper planner call hit auth error.",
                    extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
                )
            elif isinstance(exc, httpx.TimeoutException):
                logger.warning(
                    "Wrapper planner call timed out.",
                    extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
                )
            else:
                logger.exception(
                    "Wrapper planner call failed before acknowledgement threshold.",
                    extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
            )
            reply = _map_planner_exception(exc)

        if not response_already_delivered:
            delivered = await _send_activity_with_retry(
                context,
                reply or UNAVAILABLE_MESSAGE,
                session_id=session_id,
                auth_handler_id=auth_handler_id,
                purpose="planner_reply",
            )
            if not delivered:
                failure_reason = "channel_delivery_failed"
    except Exception:
        failure_reason = "unexpected_wrapper_error"
        logger.exception(
            "Wrapper turn failed unexpectedly.",
            extra={"session_id": session_id, "auth_handler_id": auth_handler_id},
        )
        await _send_activity_with_retry(
            context,
            UNAVAILABLE_MESSAGE,
            session_id=session_id,
            auth_handler_id=auth_handler_id,
            purpose="unexpected_wrapper_error_message",
        )
    finally:
        if not completion_deferred:
            await runtime.finish_turn(session_id)
            logger.info(
                "Wrapper turn completed.",
                extra={
                    "session_id": session_id,
                    "auth_handler_id": auth_handler_id,
                    "long_running_enabled": runtime.long_running_messages_enabled,
                    "ack_sent": ack_sent,
                    "failure_reason": failure_reason,
                    "planner_elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
                },
            )


async def acknowledge_invoke_activity(context: Any) -> None:
    logger.info(
        "Wrapper acknowledged invoke activity.",
        extra={
            "activity_name": getattr(context.activity, "name", None),
            "session_id": str(getattr(getattr(context.activity, "conversation", None), "id", "") or "").strip(),
        },
    )
    await context.send_activity(
        Activity(
            type=ActivityTypes.invoke_response,
            value=InvokeResponse(status=200),
        )
    )


class ConditionalJwtAuthorizationMiddleware(JwtAuthorizationMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") in {"/healthz", "/api/debug/chat"}:
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def create_app() -> FastAPI:
    connection_manager = build_connection_manager()
    storage = MemoryStorage()
    auth_handlers = build_auth_handlers()
    authorization = Authorization(
        storage=storage,
        connection_manager=connection_manager,
        auth_handlers=auth_handlers,
    )
    adapter = CloudAdapter(connection_manager=connection_manager)
    bot_app_id = get_bot_app_id()
    long_running_messages_enabled = get_wrapper_long_running_messages_enabled()
    agent_app = CompatAgentApplication(
        options=ApplicationOptions(
            adapter=adapter,
            storage=storage,
            bot_app_id=bot_app_id,
            long_running_messages=False,
        ),
        connection_manager=connection_manager,
        authorization=authorization,
    )
    runtime = WrapperRuntime(
        PlannerServiceClient(
            base_url=get_planner_service_base_url(),
            timeout_seconds=get_wrapper_timeout_seconds(),
        ),
        long_running_messages_enabled=long_running_messages_enabled,
        ack_threshold_seconds=get_wrapper_ack_threshold_seconds(),
        incremental_delivery_enabled=get_wrapper_incremental_delivery_enabled(),
    )
    debug_chat_enabled = get_wrapper_debug_chat_enabled()
    debug_expected_audience = get_wrapper_debug_expected_audience() if debug_chat_enabled else ""
    debug_allowed_upns = get_wrapper_debug_allowed_upns() if debug_chat_enabled else set()
    agentic_handler_id, connector_handler_id = get_handler_ids()

    async def _handle_message(context, state, *, auth_handler_id: str):
        await handle_wrapper_message(
            context=context,
            agent_auth=agent_app.auth,
            runtime=runtime,
            auth_handler_id=auth_handler_id,
            delivery_adapter=adapter,
            delivery_bot_app_id=bot_app_id,
        )

    async def _handle_non_message(context, state):
        session_id = str(getattr(getattr(context.activity, "conversation", None), "id", "") or "").strip()
        await _send_activity_with_retry(
            context,
            READY_MESSAGE,
            session_id=session_id,
            auth_handler_id="none",
            purpose="non_message_ready_message",
        )

    async def _handle_invoke(context, state):
        await acknowledge_invoke_activity(context)

    @agent_app.error
    async def _handle_app_error(context, err: Exception):
        session_id = str(getattr(getattr(context.activity, "conversation", None), "id", "") or "").strip()
        logger.error(
            "Wrapper application error bubbled to the SDK handler.",
            extra={"session_id": session_id},
            exc_info=(type(err), err, err.__traceback__),
        )
        try:
            await _send_activity_with_retry(
                context,
                UNAVAILABLE_MESSAGE,
                session_id=session_id,
                auth_handler_id="none",
                purpose="sdk_error_unavailable_message",
            )
        except Exception:
            logger.exception(
                "Wrapper failed to send fallback error message.",
                extra={"session_id": session_id},
            )

    def _agentic_selector(context) -> bool:
        return context.activity.type == "message" and bool(context.activity.is_agentic_request())

    def _connector_selector(context) -> bool:
        return context.activity.type == "message" and not bool(context.activity.is_agentic_request())

    agent_app.add_route(
        _agentic_selector,
        functools.partial(_handle_message, auth_handler_id=agentic_handler_id),
        auth_handlers=[agentic_handler_id],
    )
    agent_app.add_route(
        _connector_selector,
        functools.partial(_handle_message, auth_handler_id=connector_handler_id),
        auth_handlers=[connector_handler_id],
    )
    agent_app.add_route(
        lambda context: context.activity.type == "invoke",
        _handle_invoke,
        is_invoke=True,
    )
    agent_app.add_route(
        lambda context: context.activity.type not in {"message", "invoke"},
        _handle_non_message,
    )

    fastapi_app = FastAPI(title="Daily Account Planner M365 Wrapper", version="1.0.0")
    fastapi_app.state.agent_configuration = connection_manager.get_default_connection_configuration()
    fastapi_app.state.agent_application = agent_app
    fastapi_app.state.adapter = adapter
    fastapi_app.add_middleware(ConditionalJwtAuthorizationMiddleware)

    @fastapi_app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @fastapi_app.post("/api/messages", response_model=None)
    async def messages(request: Request):
        return await start_agent_process(request, agent_app, adapter)

    @fastapi_app.post("/api/debug/chat", response_model=DebugChatResponse)
    async def debug_chat(payload: DebugChatRequest, request: Request) -> DebugChatResponse:
        if not debug_chat_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

        try:
            user_assertion = extract_bearer_token(request.headers.get("authorization"))
            claims = validate_debug_token(
                user_assertion,
                expected_audience=debug_expected_audience,
            )
        except DebugAuthValidationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except DebugAuthConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc

        normalized_upn = (claims.upn or "").strip().lower()
        if debug_allowed_upns and normalized_upn not in debug_allowed_upns:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Caller is not allowed to use wrapper debug chat.",
            )

        try:
            planner_access_token = acquire_planner_token_on_behalf_of(
                user_assertion=user_assertion,
                expected_audience=debug_expected_audience,
            )
        except DebugAuthOboError as exc:
            logger.warning(
                "Wrapper debug chat planner OBO failed.",
                extra={"user_id": claims.user_id, "upn": claims.upn},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Planner delegated access is unavailable.",
            ) from exc
        except DebugAuthConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc

        session_id = (payload.session_id or f"debug::{claims.user_id}").strip()
        if not session_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required.")

        try:
            reply = await _run_direct_wrapper_turn(
                runtime=runtime,
                session_id=session_id,
                text=payload.text,
                planner_access_token=planner_access_token,
            )
        except HTTPException:
            raise
        except PlannerServiceAuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AUTH_RETRY_MESSAGE) from exc
        except (PlannerServiceError, httpx.TimeoutException) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=UNAVAILABLE_MESSAGE,
            ) from exc

        return DebugChatResponse(
            session_id=session_id,
            reply=reply or UNAVAILABLE_MESSAGE,
            user_id=claims.user_id,
            upn=claims.upn,
        )

    return fastapi_app


app = create_app()
