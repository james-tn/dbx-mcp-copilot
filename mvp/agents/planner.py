"""
Daily Account Planner agent definitions.

The production runtime is a Microsoft Agent Framework handoff workflow:
DailyAccountPlanner -> AccountPulse or NextMove. A simple tool-router remains
available only for local experimentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_framework import Agent, AgentResponse, Message
from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from agent_framework.orchestrations import HandoffAgentUserRequest, HandoffBuilder
import agent_framework_orchestrations._handoff as _handoff_module

try:
    from .account_pulse import create_account_pulse_agent
    from .next_move import create_next_move_agent
except ImportError:
    from account_pulse import create_account_pulse_agent
    from next_move import create_next_move_agent

PLANNER_INSTRUCTIONS = """You are the Daily Account Planner for Veeam field sellers.

You have two specialized agents available as tools:

1. **account_pulse** — Morning intelligence briefing. Scans news, SEC filings, cybersecurity events across the seller's accounts.
   Trigger on: "briefing", "what's happening", "any news", "what's going on", "morning briefing", "what should I know"

2. **next_move** — Propensity ranking and outreach. Finds top accounts by AIQ score, explains why, shows contacts, drafts JOLT emails.
   Trigger on: "where should I focus", "top accounts", "best opportunities", "draft email", "outreach", territory strings like "GreatLakes-ENT-Named-1"

When the seller's intent is clear, call the appropriate agent tool immediately. Pass through the seller's exact words as the request.

When ambiguous, ask: "Would you like your morning intelligence briefing (Account Pulse) or your top propensity accounts with outreach drafts (Next Move)?"

Do not attempt to answer sales questions yourself — always route to the appropriate agent tool.

After the agent tool returns its result, relay the full output to the seller. Do not summarize or abbreviate — include the complete response from the tool."""

RUNTIME_PLANNER_INSTRUCTIONS = """You are the Daily Account Planner for Veeam field sellers.

You are the top-level routing agent in a multi-agent handoff workflow.

Your responsibilities:
- identify whether the seller wants Account Pulse or Next Move
- hand off immediately to the correct specialist agent when intent is clear
- ask one short clarification only when the request is genuinely ambiguous
- preserve the seller experience as one planner, without mentioning internal agent boundaries

Use Account Pulse when the seller wants a briefing, current events, public-company signals, cyber events,
or asks what is happening in their accounts.

Use Next Move when the seller wants where to focus, top opportunities, contacts, outreach help,
or email drafting.

When the request is ambiguous, ask:
"Would you like your morning intelligence briefing or your top accounts with outreach guidance?"

Never answer the sales task yourself when a specialist should handle it. Hand off instead.

Data access rule:
- rely on the signed-in user's Databricks access and secure views
- local-only testing helpers may allow territory overrides, but authenticated sessions should not
- if the seller asks how access works, explain that the planner uses their signed-in access to secure Databricks views
"""


ACCOUNT_PULSE_HANDOFF_DESCRIPTION = (
    "Morning intelligence briefing for seller accounts. Use for news, SEC filings, "
    "cybersecurity incidents, and what-is-happening style requests."
)

NEXT_MOVE_HANDOFF_DESCRIPTION = (
    "Focus ranking, contact selection, and outreach guidance. Use for where-to-focus, "
    "top-accounts, and draft-email style requests."
)


@dataclass
class PlannerWorkflowSession:
    workflow: Any


@dataclass
class PlannerWorkflowResponse:
    text: str
    raw_result: Any | None = None


def _apply_handoff_store_workaround() -> None:
    """Work around Azure Responses handoff replay failures in the current MAF release.

    HandoffBuilder currently clones participant agents with ``store=False``. With Azure
    Responses clients, that can cause cross-agent replay errors when the workflow hands off
    or resumes across turns. For planner-owned state, we keep session history in memory and
    allow the cloned agents to use provider-side storage inside each per-turn workflow run.
    """

    if getattr(_handoff_module.HandoffAgentExecutor, "_ri_store_workaround_applied", False):
        return

    original_clone = _handoff_module.HandoffAgentExecutor._clone_chat_agent

    def patched_clone(self, agent):
        cloned = original_clone(self, agent)
        if getattr(cloned.client, "STORES_BY_DEFAULT", False):
            cloned.default_options["store"] = True
        return cloned

    _handoff_module.HandoffAgentExecutor._clone_chat_agent = patched_clone
    _handoff_module.HandoffAgentExecutor._ri_store_workaround_applied = True


def _extract_text_from_messages(messages: list[Message]) -> str:
    for message in reversed(messages):
        text = (message.text or "").strip()
        if message.role == "assistant" and text:
            return text
    for message in reversed(messages):
        text = (message.text or "").strip()
        if text:
            return text
    return ""


def extract_reply_from_workflow_result(result: Any) -> str:
    """Extract the seller-facing reply from a non-streaming handoff workflow run."""
    request_events = list(getattr(result, "get_request_info_events", lambda: [])())
    for event in reversed(request_events):
        payload = getattr(event, "data", None)
        if isinstance(payload, HandoffAgentUserRequest) or hasattr(payload, "agent_response"):
            agent_response = getattr(payload, "agent_response", None)
            reply = getattr(agent_response, "text", "") or ""
            if reply.strip():
                return reply.strip()
            reply = _extract_text_from_messages(list(getattr(agent_response, "messages", []) or []))
            if reply:
                return reply

    outputs = list(getattr(result, "get_outputs", lambda: [])())
    for output in reversed(outputs):
        if isinstance(output, AgentResponse):
            reply = (output.text or "").strip()
            if reply:
                return reply
            reply = _extract_text_from_messages(list(output.messages or []))
            if reply:
                return reply
        if isinstance(output, list) and output and all(isinstance(message, Message) for message in output):
            reply = _extract_text_from_messages(output)
            if reply:
                return reply
        text = getattr(output, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        if isinstance(output, str) and output.strip():
            return output.strip()

    return ""


class PlannerWorkflowAgent:
    """Session-scoped handoff workflow wrapper with the familiar agent API."""

    def __init__(self, responses_client: AzureOpenAIResponsesClient) -> None:
        self._responses_client = responses_client

    def create_session(self) -> PlannerWorkflowSession:
        return PlannerWorkflowSession(workflow=create_runtime_planner_workflow(self._responses_client))

    async def run(
        self,
        messages: str | list[Message] | None = None,
        *,
        session: PlannerWorkflowSession | None = None,
        **kwargs: Any,
    ) -> PlannerWorkflowResponse:
        active_session = session or self.create_session()
        result = await active_session.workflow.run(message=messages, **kwargs)
        reply = extract_reply_from_workflow_result(result)
        return PlannerWorkflowResponse(text=reply, raw_result=result)


def create_planner_agent(
    chat_client: AzureOpenAIChatClient,
    responses_client: AzureOpenAIResponsesClient,
) -> Agent:
    """Create the planner agent (ChatClient) with sub-agents wrapped as tools."""
    account_pulse = create_account_pulse_agent(responses_client)
    next_move = create_next_move_agent(responses_client)

    pulse_tool = account_pulse.as_tool(
        name="account_pulse",
        description=(
            "Run Account Pulse — daily intelligence briefing that scans news, "
            "SEC filings, and cybersecurity events across the seller's accounts. "
            "Use when the seller asks for a briefing, news, or 'what's happening'."
        ),
        arg_name="request",
        arg_description="The seller's briefing request",
    )

    next_move_tool = next_move.as_tool(
        name="next_move",
        description=(
            "Run Next Move — finds highest-potential accounts by propensity score, "
            "explains why, shows contacts, and drafts JOLT outreach emails. "
            "Use when the seller asks 'where should I focus?', 'top accounts', "
            "'draft me an email', or provides a territory string."
        ),
        arg_name="request",
        arg_description="The seller's propensity or outreach request",
    )

    return chat_client.as_agent(
        name="DailyAccountPlanner",
        instructions=PLANNER_INSTRUCTIONS,
        tools=[pulse_tool, next_move_tool],
    )


def create_runtime_planner_router_agent(
    responses_client: AzureOpenAIResponsesClient,
) -> Agent:
    return responses_client.as_agent(
        id="daily_account_planner",
        name="DailyAccountPlanner",
        instructions=RUNTIME_PLANNER_INSTRUCTIONS,
        description=(
            "Top-level seller planner that routes each request to Account Pulse or "
            "Next Move via handoff."
        ),
    )


def create_runtime_planner_workflow(
    responses_client: AzureOpenAIResponsesClient,
) -> Any:
    _apply_handoff_store_workaround()
    planner = create_runtime_planner_router_agent(responses_client)
    account_pulse = create_account_pulse_agent(responses_client)
    next_move = create_next_move_agent(responses_client)

    return (
        HandoffBuilder(
            name="daily_account_planner_handoff",
            description="Daily Account Planner handoff workflow for Account Pulse and Next Move.",
        )
        .participants([planner, account_pulse, next_move])
        .add_handoff(
            planner,
            [account_pulse],
            description=ACCOUNT_PULSE_HANDOFF_DESCRIPTION,
        )
        .add_handoff(
            planner,
            [next_move],
            description=NEXT_MOVE_HANDOFF_DESCRIPTION,
        )
        .add_handoff(
            account_pulse,
            [planner],
            description="Return to the Daily Account Planner when the seller shifts out of briefing mode.",
        )
        .add_handoff(
            next_move,
            [planner],
            description="Return to the Daily Account Planner when the seller shifts out of focus or outreach mode.",
        )
        .with_start_agent(planner)
        .build()
    )


def create_runtime_planner_agent(
    responses_client: AzureOpenAIResponsesClient,
) -> PlannerWorkflowAgent:
    """Create the production runtime wrapper around a session-scoped handoff workflow."""
    return PlannerWorkflowAgent(responses_client)
