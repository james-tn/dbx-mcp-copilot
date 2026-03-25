"""
Planner-service HTTP client used by the thin M365 wrapper.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

import httpx


class PlannerServiceError(RuntimeError):
    """Base error raised by wrapper-to-planner forwarding."""


class PlannerServiceAuthError(PlannerServiceError):
    """Raised when the planner service rejects or lacks user auth."""


class PlannerServiceClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self.http_client.aclose()

    async def create_session(self, *, session_id: str, access_token: str) -> dict[str, Any]:
        response = await self.http_client.post(
            f"{self.base_url}/api/chat/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"session_id": session_id},
        )
        return self._parse_response(response)

    async def send_turn(self, *, session_id: str, text: str, access_token: str) -> str:
        response = await self.http_client.post(
            f"{self.base_url}/api/chat/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"text": text},
        )
        if response.status_code == 404:
            await self.create_session(session_id=session_id, access_token=access_token)
            response = await self.http_client.post(
                f"{self.base_url}/api/chat/sessions/{session_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"text": text},
            )
        payload = self._parse_response(response)
        return str(payload.get("reply", "")).strip()

    async def stream_turn(
        self,
        *,
        session_id: str,
        text: str,
        access_token: str,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self.http_client.stream(
            "POST",
            f"{self.base_url}/api/chat/sessions/{session_id}/messages/stream",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"text": text},
        ) as response:
            if response.status_code == 404:
                await self.create_session(session_id=session_id, access_token=access_token)
                async with self.http_client.stream(
                    "POST",
                    f"{self.base_url}/api/chat/sessions/{session_id}/messages/stream",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"text": text},
                ) as retry_response:
                    async for payload in self._iter_sse_events(retry_response):
                        yield payload
                return
            async for payload in self._iter_sse_events(response):
                yield payload

    async def collect_streamed_turn(
        self,
        *,
        session_id: str,
        text: str,
        access_token: str,
        event_handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        collected_reply_parts: list[str] = []
        final_payload: dict[str, Any] | None = None
        async for payload in self.stream_turn(session_id=session_id, text=text, access_token=access_token):
            if event_handler is not None:
                await event_handler(payload)
            if payload.get("event") == "text_delta":
                collected_reply_parts.append(str(payload.get("delta", "")))
            if payload.get("event") == "final":
                final_payload = payload
        if final_payload is None:
            return {"reply": "".join(collected_reply_parts).strip()}
        return final_payload

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise PlannerServiceAuthError("Planner service authentication failed.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlannerServiceError("Planner service request failed.") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlannerServiceError("Planner service returned an unexpected response.")
        return payload

    async def _iter_sse_events(self, response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        if response.status_code in {401, 403}:
            raise PlannerServiceAuthError("Planner service authentication failed.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlannerServiceError("Planner service request failed.") from exc

        current_event = "message"
        data_lines: list[str] = []
        async for raw_line in response.aiter_lines():
            line = raw_line.rstrip("\n")
            if not line:
                if data_lines:
                    payload = json.loads("\n".join(data_lines))
                    if isinstance(payload, dict):
                        payload.setdefault("event", current_event)
                        yield payload
                current_event = "message"
                data_lines = []
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
