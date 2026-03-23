"""Tests for the in-memory planner session store."""

from __future__ import annotations

import os
import sys
import time

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


def test_session_store_evicts_least_recently_used_session_when_limit_reached() -> None:
    store = InMemorySessionStore(max_turns=2, max_sessions=2, idle_ttl_seconds=3600)

    first = store.create(owner_id="user-1", channel="direct_api", agent_session=object(), session_id="s1")
    second = store.create(owner_id="user-2", channel="direct_api", agent_session=object(), session_id="s2")
    now = time.time()
    first.last_accessed_at = now - 10
    second.last_accessed_at = now - 20
    third = store.create(owner_id="user-3", channel="direct_api", agent_session=object(), session_id="s3")

    assert third.session_id == "s3"
    assert store.get("s1") is first
    assert store.get("s2") is None
    assert store.get("s3") is third


def test_session_store_expires_idle_sessions() -> None:
    store = InMemorySessionStore(max_turns=2, max_sessions=5, idle_ttl_seconds=60)

    state = store.create(owner_id="user-1", channel="direct_api", agent_session=object(), session_id="s1")
    state.last_accessed_at = time.time() - 120
    expired = store.get("s1")

    assert expired is None
