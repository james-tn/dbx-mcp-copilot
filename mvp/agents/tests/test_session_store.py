"""Tests for the in-memory planner session store."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from session_store import InMemorySessionStore


def test_session_store_creates_and_fetches_sessions() -> None:
    store = InMemorySessionStore(max_turns=3)

    state = store.create(owner_id="user-1", channel="direct_api", agent_session=object(), session_id="abc")

    fetched = store.get("abc")

    assert fetched is state
    assert fetched.owner_id == "user-1"
    assert fetched.channel == "direct_api"


def test_session_store_caps_turn_history() -> None:
    store = InMemorySessionStore(max_turns=2)
    state = store.create(owner_id="user-1", channel="direct_api", agent_session=object(), session_id="abc")

    for index in range(6):
        store.append_turn(state, "user" if index % 2 == 0 else "assistant", f"turn-{index}")

    turns = [turn["text"] for turn in store.public_view(state)["turns"]]
    assert turns == ["turn-2", "turn-3", "turn-4", "turn-5"]
