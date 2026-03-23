from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, TypeVar

from agent_framework.exceptions import ChatClientException

logger = logging.getLogger(__name__)
T = TypeVar("T")

_RATE_LIMIT_MARKERS = (
    "too many requests",
    "too_many_requests",
    "error code: 429",
    "429 -",
)


def is_rate_limit_exception(exc: Exception) -> bool:
    if not isinstance(exc, ChatClientException):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


def get_openai_rate_limit_retry_count() -> int:
    try:
        return max(0, int(os.environ.get("AZURE_OPENAI_RATE_LIMIT_RETRY_COUNT", "4")))
    except ValueError:
        return 4


def get_openai_rate_limit_backoff_seconds() -> float:
    try:
        return max(0.1, float(os.environ.get("AZURE_OPENAI_RATE_LIMIT_BACKOFF_SECONDS", "2.0")))
    except ValueError:
        return 2.0


async def run_with_rate_limit_retry(
    operation_name: str,
    func: Callable[[], Awaitable[T]],
    *,
    retry_count: int | None = None,
    base_delay_seconds: float | None = None,
) -> T:
    attempts = get_openai_rate_limit_retry_count() if retry_count is None else max(0, retry_count)
    base_delay = (
        get_openai_rate_limit_backoff_seconds()
        if base_delay_seconds is None
        else max(0.1, base_delay_seconds)
    )

    for attempt in range(attempts + 1):
        try:
            return await func()
        except Exception as exc:
            if not is_rate_limit_exception(exc) or attempt >= attempts:
                raise
            sleep_seconds = base_delay * (2**attempt)
            logger.warning(
                "%s hit Azure OpenAI rate limits; retrying after backoff.",
                operation_name,
                extra={
                    "operation_name": operation_name,
                    "attempt": attempt + 1,
                    "retry_count": attempts,
                    "sleep_seconds": round(sleep_seconds, 2),
                },
            )
            await asyncio.sleep(sleep_seconds)

    raise RuntimeError(f"{operation_name} exhausted retry loop unexpectedly.")
