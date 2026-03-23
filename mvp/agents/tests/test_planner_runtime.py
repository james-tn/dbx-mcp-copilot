"""Tests for the ACA handoff planner runtime path."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import sentinel

from agent_framework.exceptions import ChatClientException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner import (
    ACCOUNT_PULSE_HANDOFF_DESCRIPTION,
    NEXT_MOVE_HANDOFF_DESCRIPTION,
    RUNTIME_PLANNER_INSTRUCTIONS,
    PlannerWorkflowAgent,
    _apply_handoff_store_workaround,
    create_runtime_planner_agent,
    extract_reply_from_workflow_result,
    extract_routed_agent_from_workflow_result,
)


class _FakeAgent(SimpleNamespace):
    pass


class _FakeWorkflow:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def run(self, *, message=None, **kwargs):
        self.calls.append({"message": message, **kwargs})
        return self.result


class _FakeBuilder:
    instances: list["_FakeBuilder"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.participants_arg = None
        self.handoffs: list[tuple[object, tuple[object, ...], str | None]] = []
        self.start_agent = None
        self.workflow = SimpleNamespace(kind="workflow")
        self.__class__.instances.append(self)

    def participants(self, participants):
        self.participants_arg = list(participants)
        return self

    def add_handoff(self, source, targets, *, description=None):
        self.handoffs.append((source, tuple(targets), description))
        return self

    def with_start_agent(self, agent):
        self.start_agent = agent
        return self

    def build(self):
        return self.workflow


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_web_search_tool(self):
        return {"type": "web_search"}

    def as_agent(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAgent(kind="fake-agent", **kwargs)


def test_runtime_planner_instructions_route_by_handoff() -> None:
    assert "multi-agent handoff workflow" in RUNTIME_PLANNER_INSTRUCTIONS
    assert "Account Pulse" in RUNTIME_PLANNER_INSTRUCTIONS
    assert "Next Move" in RUNTIME_PLANNER_INSTRUCTIONS
    assert "Hand off instead." in RUNTIME_PLANNER_INSTRUCTIONS
    assert "signed-in user's Databricks access" in RUNTIME_PLANNER_INSTRUCTIONS
    assert "semantic Databricks tools directly" not in RUNTIME_PLANNER_INSTRUCTIONS


def test_create_runtime_planner_agent_builds_handoff_workflow(monkeypatch) -> None:
    client = _FakeResponsesClient()
    _FakeBuilder.instances.clear()
    monkeypatch.setattr("planner.HandoffBuilder", _FakeBuilder)

    agent = create_runtime_planner_agent(client)
    session = agent.create_session()

    assert isinstance(agent, PlannerWorkflowAgent)
    assert session.workflow.kind == "workflow"
    assert len(client.calls) == 3
    assert client.calls[0]["name"] == "DailyAccountPlanner"
    assert client.calls[1]["name"] == "AccountPulse"
    assert client.calls[2]["name"] == "NextMove"

    builder = _FakeBuilder.instances[0]
    assert [participant.name for participant in builder.participants_arg] == [
        "DailyAccountPlanner",
        "AccountPulse",
        "NextMove",
    ]
    assert builder.start_agent.name == "DailyAccountPlanner"
    assert builder.handoffs == [
        (builder.participants_arg[0], (builder.participants_arg[1],), ACCOUNT_PULSE_HANDOFF_DESCRIPTION),
        (builder.participants_arg[0], (builder.participants_arg[2],), NEXT_MOVE_HANDOFF_DESCRIPTION),
        (
            builder.participants_arg[1],
            (builder.participants_arg[0],),
            "Return to the Daily Account Planner when the seller shifts out of briefing mode.",
        ),
        (
            builder.participants_arg[2],
            (builder.participants_arg[0],),
            "Return to the Daily Account Planner when the seller shifts out of focus or outreach mode.",
        ),
    ]


def test_extract_reply_from_workflow_result_uses_request_info_response() -> None:
    agent_response = SimpleNamespace(text="Briefing reply", messages=[])
    request_event = SimpleNamespace(data=SimpleNamespace(agent_response=agent_response))
    result = SimpleNamespace(
        get_request_info_events=lambda: [request_event],
        get_outputs=lambda: [],
    )

    assert extract_reply_from_workflow_result(result) == "Briefing reply"


def test_extract_routed_agent_from_workflow_result_uses_last_output_executor() -> None:
    result = [
        SimpleNamespace(type="output", executor_id="daily_account_planner", data="router"),
        SimpleNamespace(type="output", executor_id="AccountPulse", data="briefing"),
    ]

    assert extract_routed_agent_from_workflow_result(result) == "AccountPulse"


def test_runtime_workflow_wrapper_returns_extracted_reply(monkeypatch) -> None:
    client = _FakeResponsesClient()
    fake_workflow = _FakeWorkflow(
        SimpleNamespace(
            get_request_info_events=lambda: [
                SimpleNamespace(data=SimpleNamespace(agent_response=SimpleNamespace(text="Next Move reply", messages=[])))
            ],
            get_outputs=lambda: [],
        )
    )
    monkeypatch.setattr("planner.create_runtime_planner_workflow", lambda _: fake_workflow)

    agent = create_runtime_planner_agent(client)
    session = agent.create_session()
    result = asyncio.run(agent.run("Where should I focus?", session=session))

    assert result.text == "Next Move reply"
    assert fake_workflow.calls == [{"message": "Where should I focus?"}]


def test_runtime_workflow_wrapper_retries_rate_limited_runs(monkeypatch) -> None:
    class _RetryWorkflow:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, *, message=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ChatClientException("Error code: 429 - {'error': {'code': 'too_many_requests'}}")
            return SimpleNamespace(
                get_request_info_events=lambda: [
                    SimpleNamespace(data=SimpleNamespace(agent_response=SimpleNamespace(text="Recovered reply", messages=[])))
                ],
                get_outputs=lambda: [],
            )

    workflow = _RetryWorkflow()
    monkeypatch.setattr("planner.create_runtime_planner_workflow", lambda _: workflow)

    agent = create_runtime_planner_agent(_FakeResponsesClient())
    session = agent.create_session()
    result = asyncio.run(agent.run("Give me my morning briefing", session=session))

    assert result.text == "Recovered reply"
    assert workflow.calls == 2


def test_handoff_store_workaround_enables_store_for_responses_clients(monkeypatch) -> None:
    class _FakeExecutor:
        pass

    class _FakeClient:
        STORES_BY_DEFAULT = True

    original_clone = lambda self, agent: SimpleNamespace(default_options={"store": False}, client=_FakeClient())
    monkeypatch.setattr("planner._handoff_module.HandoffAgentExecutor", _FakeExecutor)
    _FakeExecutor._clone_chat_agent = original_clone
    _FakeExecutor._ri_store_workaround_applied = False

    _apply_handoff_store_workaround()
    cloned = _FakeExecutor._clone_chat_agent(_FakeExecutor(), sentinel.agent)

    assert cloned.default_options["store"] is True
    assert _FakeExecutor._ri_store_workaround_applied is True
