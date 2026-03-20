"""
Planner-service HTTP client used by the thin M365 wrapper.
"""

from __future__ import annotations

from typing import Any

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
