"""
Thin Microsoft 365 custom-engine wrapper for the Daily Account Planner.
"""

from __future__ import annotations

import functools
from typing import Any, Protocol

from fastapi import FastAPI, Request, Response
from microsoft_agents.hosting.core import AgentApplication, ApplicationOptions, Authorization, MemoryStorage
from microsoft_agents.hosting.fastapi import CloudAdapter, JwtAuthorizationMiddleware, start_agent_process

try:
    from .config import (
        build_auth_handlers,
        build_connection_manager,
        get_handler_ids,
        get_planner_service_base_url,
        get_wrapper_timeout_seconds,
    )
    from .planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError
except ImportError:
    from config import (
        build_auth_handlers,
        build_connection_manager,
        get_handler_ids,
        get_planner_service_base_url,
        get_wrapper_timeout_seconds,
    )
    from planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError


class WrapperRuntime:
    def __init__(self, client: PlannerServiceClient) -> None:
        self.client = client

    async def forward_message(
        self,
        *,
        session_id: str,
        text: str,
        planner_access_token: str,
    ) -> str:
        return await self.client.send_turn(
            session_id=session_id,
            text=text,
            access_token=planner_access_token,
        )


class _AgentAuth(Protocol):
    async def get_token(self, context: Any, *, auth_handler_id: str):
        ...


async def handle_wrapper_message(
    *,
    context: Any,
    agent_auth: _AgentAuth,
    runtime: WrapperRuntime,
    auth_handler_id: str,
) -> None:
    text = (context.activity.text or "").strip()
    if not text:
        await context.send_activity("Daily Account Planner is ready. Send a message to begin.")
        return

    token_response = await agent_auth.get_token(context, auth_handler_id=auth_handler_id)
    planner_access_token = str(getattr(token_response, "token", "") or "").strip()
    if not planner_access_token:
        await context.send_activity(
            "Daily Account Planner couldn't get your planner access token yet. Please sign in and try again."
        )
        return

    try:
        reply = await runtime.forward_message(
            session_id=context.activity.conversation.id,
            text=text,
            planner_access_token=planner_access_token,
        )
    except PlannerServiceAuthError:
        reply = (
            "Daily Account Planner couldn't validate your delegated access right now. "
            "Please sign in again and retry."
        )
    except PlannerServiceError:
        reply = "Daily Account Planner is temporarily unavailable. Please try again in a moment."

    await context.send_activity(reply)


class ConditionalJwtAuthorizationMiddleware(JwtAuthorizationMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/healthz":
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
    agent_app = AgentApplication(
        options=ApplicationOptions(storage=storage),
        connection_manager=connection_manager,
        authorization=authorization,
    )
    runtime = WrapperRuntime(
        PlannerServiceClient(
            base_url=get_planner_service_base_url(),
            timeout_seconds=get_wrapper_timeout_seconds(),
        )
    )
    agentic_handler_id, connector_handler_id = get_handler_ids()

    async def _handle_message(context, state, *, auth_handler_id: str):
        await handle_wrapper_message(
            context=context,
            agent_auth=agent_app.auth,
            runtime=runtime,
            auth_handler_id=auth_handler_id,
        )

    async def _handle_non_message(context, state):
        await context.send_activity("Daily Account Planner is ready. Send a message to begin.")

    async def _handle_invoke(context, state):
        return

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

    return fastapi_app


app = create_app()
