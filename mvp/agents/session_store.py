"""
In-memory session state for the Daily Account Planner API.

The MVP intentionally keeps session state in app memory and is expected to run
as a single-replica Azure Container App.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionTurn:
    role: str
    text: str
    created_at: float


@dataclass
class PlannerSessionState:
    session_id: str
    owner_id: str
    channel: str
    agent_session: Any
    conversation_id: str | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    turns: list[SessionTurn] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def touch(self) -> None:
        self.last_accessed_at = time.time()


class InMemorySessionStore:
    def __init__(
        self,
        *,
        max_turns: int = 20,
        max_sessions: int = 500,
        idle_ttl_seconds: float = 28800.0,
    ) -> None:
        self.max_turns = max(1, max_turns)
        self.max_sessions = max(1, max_sessions)
        self.idle_ttl_seconds = max(60.0, idle_ttl_seconds)
        self._sessions: dict[str, PlannerSessionState] = {}
        self._conversation_index: dict[str, str] = {}

    def _delete_session(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state and state.conversation_id:
            self._conversation_index.pop(state.conversation_id, None)

    def _is_expired(self, state: PlannerSessionState, *, now: float) -> bool:
        return (now - state.last_accessed_at) > self.idle_ttl_seconds

    def _prune(self) -> None:
        now = time.time()
        expired_ids = [
            session_id
            for session_id, state in self._sessions.items()
            if self._is_expired(state, now=now)
        ]
        for session_id in expired_ids:
            self._delete_session(session_id)

        overflow = len(self._sessions) - self.max_sessions
        if overflow <= 0:
            return

        oldest_sessions = sorted(
            self._sessions.values(),
            key=lambda state: (state.last_accessed_at, state.created_at, state.session_id),
        )[:overflow]
        for state in oldest_sessions:
            self._delete_session(state.session_id)

    def create(
        self,
        *,
        owner_id: str,
        channel: str,
        agent_session: Any,
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> PlannerSessionState:
        resolved_session_id = (session_id or str(uuid.uuid4())).strip()
        if not resolved_session_id:
            resolved_session_id = str(uuid.uuid4())
        self._prune()
        state = PlannerSessionState(
            session_id=resolved_session_id,
            owner_id=owner_id,
            channel=channel,
            agent_session=agent_session,
            conversation_id=conversation_id,
        )
        self._sessions[state.session_id] = state
        if conversation_id:
            self._conversation_index[conversation_id] = state.session_id
        self._prune()
        return state

    def get(self, session_id: str) -> PlannerSessionState | None:
        self._prune()
        state = self._sessions.get(session_id)
        if state:
            state.touch()
        return state

    def get_for_conversation(self, conversation_id: str) -> PlannerSessionState | None:
        session_id = self._conversation_index.get(conversation_id)
        if not session_id:
            return None
        return self.get(session_id)

    def get_or_create_for_conversation(
        self,
        *,
        conversation_id: str,
        owner_id: str,
        channel: str,
        agent_session_factory,
    ) -> PlannerSessionState:
        existing = self.get_for_conversation(conversation_id)
        if existing is not None:
            return existing
        return self.create(
            owner_id=owner_id,
            channel=channel,
            conversation_id=conversation_id,
            agent_session=agent_session_factory(),
            session_id=conversation_id,
        )

    def append_turn(self, state: PlannerSessionState, role: str, text: str) -> None:
        state.turns.append(SessionTurn(role=role, text=text, created_at=time.time()))
        max_entries = self.max_turns * 2
        if len(state.turns) > max_entries:
            state.turns = state.turns[-max_entries:]
        state.touch()

    def public_view(self, state: PlannerSessionState) -> dict[str, Any]:
        return {
            "session_id": state.session_id,
            "channel": state.channel,
            "conversation_id": state.conversation_id,
            "created_at": state.created_at,
            "last_accessed_at": state.last_accessed_at,
            "turns": [
                {
                    "role": turn.role,
                    "text": turn.text,
                    "created_at": turn.created_at,
                }
                for turn in state.turns
            ],
        }
