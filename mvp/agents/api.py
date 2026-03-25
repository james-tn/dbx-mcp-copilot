"""
Stateful Daily Account Planner API for Azure Container Apps.

Exposes:
- POST /api/chat/sessions
- POST /api/chat/sessions/{session_id}/messages
- GET /api/chat/sessions/{session_id}
- GET /healthz
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent_framework import Message

try:
    from .auth_context import (
        AuthenticationRequiredError,
        AuthConfigurationError,
        bind_request_identity,
        extract_bearer_token,
        get_request_user_id,
        reset_request_identity,
        validate_bearer_token,
    )
    from .config import (
        get_client,
        get_session_idle_ttl_seconds,
        get_session_max_sessions,
        get_session_max_turns,
        get_session_store_mode,
    )
    from .planner import create_runtime_planner_agent, extract_routed_agent_from_workflow_result
    from .session_store import InMemorySessionStore
    from ..shared.streaming_context import bind_stream_emitter, reset_stream_emitter
except ImportError:
    from auth_context import (
        AuthenticationRequiredError,
        AuthConfigurationError,
        bind_request_identity,
        extract_bearer_token,
        get_request_user_id,
        reset_request_identity,
        validate_bearer_token,
    )
    from config import (
        get_client,
        get_session_idle_ttl_seconds,
        get_session_max_sessions,
        get_session_max_turns,
        get_session_store_mode,
    )
    from planner import create_runtime_planner_agent, extract_routed_agent_from_workflow_result
    from session_store import InMemorySessionStore
    from shared.streaming_context import bind_stream_emitter, reset_stream_emitter


logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    session_id: str | None = None


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)


class SessionResponse(BaseModel):
    session_id: str
    channel: str
    conversation_id: str | None = None
    created_at: float
    last_accessed_at: float
    turns: list[dict[str, Any]]


class MessageResponse(BaseModel):
    session_id: str
    reply: str
    channel: str
    turns: list[dict[str, Any]]


class PlannerRuntime:
    def __init__(self) -> None:
        if get_session_store_mode() != "memory":
            raise ValueError("Only SESSION_STORE_MODE=memory is implemented in this MVP.")
        self.session_store = InMemorySessionStore(
            max_turns=get_session_max_turns(),
            max_sessions=get_session_max_sessions(),
            idle_ttl_seconds=get_session_idle_ttl_seconds(),
        )
        self.agent = create_runtime_planner_agent(get_client())

    async def create_session(self, *, owner_id: str, session_id: str | None = None) -> dict[str, Any]:
        state = self.session_store.create(
            owner_id=owner_id,
            channel="direct_api",
            session_id=session_id,
            agent_session=self.agent.create_session(),
        )
        return self.session_store.public_view(state)

    async def get_session(self, *, session_id: str, owner_id: str) -> dict[str, Any]:
        state = self.session_store.get(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if state.owner_id != owner_id:
            raise HTTPException(status_code=403, detail="Session does not belong to this user.")
        return self.session_store.public_view(state)

    async def run_direct_turn(self, *, session_id: str, owner_id: str, text: str) -> dict[str, Any]:
        state = self.session_store.get(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if state.owner_id != owner_id:
            raise HTTPException(status_code=403, detail="Session does not belong to this user.")
        return await self._run_turn(state=state, text=text)

    async def stream_direct_turn(
        self,
        *,
        session_id: str,
        owner_id: str,
        text: str,
    ) -> AsyncIterator[str]:
        state = self.session_store.get(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if state.owner_id != owner_id:
            raise HTTPException(status_code=403, detail="Session does not belong to this user.")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _emit(payload: dict[str, object]) -> None:
            await queue.put(payload)

        async def _run_turn() -> dict[str, Any]:
            token = bind_stream_emitter(_emit)
            try:
                return await self._run_turn(state=state, text=text)
            finally:
                reset_stream_emitter(token)

        await queue.put({"event": "ack", "session_id": session_id, "message": "planner_turn_started"})
        turn_task = asyncio.create_task(_run_turn())

        try:
            while not turn_task.done() or not queue.empty():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                yield _format_sse(payload["event"], payload)

            result = await turn_task
            for chunk in _chunk_text(result["reply"]):
                yield _format_sse("text_delta", {"event": "text_delta", "delta": chunk})
            yield _format_sse(
                "final",
                {
                    "event": "final",
                    "session_id": result["session_id"],
                    "reply": result["reply"],
                    "channel": result["channel"],
                    "turns": result["turns"],
                },
            )
        except Exception as exc:
            logger.exception("Planner streaming turn failed.", extra={"session_id": session_id, "owner_id": owner_id})
            yield _format_sse("error", {"event": "error", "detail": str(exc)})

    async def _run_turn(self, *, state, text: str) -> dict[str, Any]:
        started = time.perf_counter()
        routed_agent = None
        async with state.lock:
            logger.info(
                "Planner turn started.",
                extra={
                    "session_id": state.session_id,
                    "owner_id": state.owner_id,
                    "channel": state.channel,
                },
            )
            self.session_store.append_turn(state, "user", text)
            try:
                result = await self.agent.run(_message_history_for_state(state))
                routed_agent = extract_routed_agent_from_workflow_result(result.raw_result)
                reply = result.text
                self.session_store.append_turn(state, "assistant", reply)
            except Exception:
                logger.exception(
                    "Planner turn failed.",
                    extra={
                        "session_id": state.session_id,
                        "owner_id": state.owner_id,
                        "channel": state.channel,
                    },
                )
                raise
        view = self.session_store.public_view(state)
        logger.info(
            "Planner turn completed.",
            extra={
                "session_id": state.session_id,
                "owner_id": state.owner_id,
                "channel": state.channel,
                "routed_agent": routed_agent or "unknown",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            },
        )
        return {
            "session_id": state.session_id,
            "reply": reply,
            "channel": state.channel,
            "turns": view["turns"],
        }


runtime = PlannerRuntime()
app = FastAPI(title="Daily Account Planner API", version="1.0.0")
_AUTHENTICATED_PATH_PREFIXES = ("/api/chat/sessions",)


def _chunk_text(text: str, *, chunk_size: int = 160) -> list[str]:
    normalized = (text or "").strip()
    if not normalized:
        return []
    return [normalized[index : index + chunk_size] for index in range(0, len(normalized), chunk_size)]


def _format_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _message_history_for_state(state) -> list[Message]:
    return [
        Message(role=turn.role, text=turn.text)
        for turn in state.turns
    ]


@app.middleware("http")
async def attach_request_identity(request: Request, call_next):
    if request.url.path == "/healthz" or not request.url.path.startswith(_AUTHENTICATED_PATH_PREFIXES):
        return await call_next(request)

    authorization = request.headers.get("authorization")
    try:
        user_assertion = extract_bearer_token(authorization)
        claims = validate_bearer_token(user_assertion)
    except AuthenticationRequiredError as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )
    except AuthConfigurationError as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    token_ref, claims_ref = bind_request_identity(user_assertion, claims)
    try:
        return await call_next(request)
    finally:
        reset_request_identity(token_ref, claims_ref)


def _require_user_id() -> str:
    user_id = get_request_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing authenticated user context.")
    return user_id


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat/sessions", response_model=SessionResponse)
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    owner_id = _require_user_id()
    return await runtime.create_session(owner_id=owner_id, session_id=payload.session_id)


@app.get("/api/chat/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> dict[str, Any]:
    owner_id = _require_user_id()
    return await runtime.get_session(session_id=session_id, owner_id=owner_id)


@app.post("/api/chat/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_session_message(session_id: str, payload: SendMessageRequest) -> dict[str, Any]:
    owner_id = _require_user_id()
    return await runtime.run_direct_turn(session_id=session_id, owner_id=owner_id, text=payload.text)


@app.post("/api/chat/sessions/{session_id}/messages/stream")
async def stream_session_message(session_id: str, payload: SendMessageRequest) -> StreamingResponse:
    owner_id = _require_user_id()
    return StreamingResponse(
        runtime.stream_direct_turn(session_id=session_id, owner_id=owner_id, text=payload.text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
