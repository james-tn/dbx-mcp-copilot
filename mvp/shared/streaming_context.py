"""Per-request streaming event hooks for planner tool execution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Awaitable, Callable

StreamEmitter = Callable[[dict[str, object]], Awaitable[None]]

_STREAM_EMITTER: ContextVar[StreamEmitter | None] = ContextVar("planner_stream_emitter", default=None)


def bind_stream_emitter(emitter: StreamEmitter | None) -> Token[StreamEmitter | None]:
    return _STREAM_EMITTER.set(emitter)


def reset_stream_emitter(token: Token[StreamEmitter | None]) -> None:
    _STREAM_EMITTER.reset(token)


async def emit_stream_event(event: str, **payload: object) -> None:
    emitter = _STREAM_EMITTER.get()
    if emitter is None:
        return
    await emitter({"event": event, **payload})
