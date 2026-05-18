"""Tests for the safety WebSocket server constants + dataclasses.

Pin documented limits (rate, size, queue depth, heartbeat) and
the per-connection state lifecycle. The full connect/disconnect
flow needs FastAPI fixtures and is exercised in api-level tests.
"""

from __future__ import annotations

import time

import pytest

from app.services.realtime.websocket_server import (
    HEARTBEAT_INTERVAL,
    MAX_CONNECTIONS_PER_PROJECT,
    MAX_MESSAGE_SIZE,
    MAX_MESSAGES_PER_SECOND,
    MESSAGE_QUEUE_MAXSIZE,
    PONG_TIMEOUT,
    RECONNECT_TOKEN_TTL,
    SafetyWebSocketManager,
    _ClientState,
    _QueuedMessage,
)

# =========================================================================
# Constants — pin documented limits
# =========================================================================


def test_heartbeat_interval_30s():
    """[contract] 30s heartbeat. Pin so a refactor doesn't quietly
    increase (slower disconnect detection) or decrease (network
    chatter)."""
    assert HEARTBEAT_INTERVAL == 30.0


def test_pong_timeout_10s():
    """[contract] 10s pong timeout. Refactor must not exceed
    HEARTBEAT_INTERVAL or pings will pile up."""
    assert PONG_TIMEOUT == 10.0
    assert PONG_TIMEOUT < HEARTBEAT_INTERVAL


def test_message_queue_maxsize_100():
    """[contract] 100-message replay queue per project. Pin so a
    refactor doesn't go unbounded (memory) or too small
    (reconnect replay misses recent events)."""
    assert MESSAGE_QUEUE_MAXSIZE == 100


def test_reconnect_token_ttl_5_min():
    """[contract] 5-min reconnect window. Pin so a refactor
    doesn't extend (security risk) or shorten (bad UX on long
    network blips)."""
    assert RECONNECT_TOKEN_TTL == 300.0


def test_max_message_size_4096():
    """[security] 4KB message size cap (DoS guard). Pin so a
    refactor can't quietly raise the limit."""
    assert MAX_MESSAGE_SIZE == 4096


def test_max_messages_per_second_10():
    """[security/rate-limit] 10 msg/s per client. Pin: refactor must
    NOT raise this without explicit review (DoS surface)."""
    assert MAX_MESSAGES_PER_SECOND == 10


def test_max_connections_per_project_50():
    """[contract] 50 concurrent WebSocket connections per project.
    Pin so a refactor doesn't quietly bump this (resource
    exhaustion risk)."""
    assert MAX_CONNECTIONS_PER_PROJECT == 50


# =========================================================================
# _ClientState dataclass
# =========================================================================


def test_client_state_defaults_user_id_empty():
    """[contract] user_id defaults to '' so legacy callers that
    don't pass it still type-check."""
    cs = _ClientState(websocket=object(), project_id="p-1")
    assert cs.user_id == ""


def test_client_state_default_factories_independent():
    """[invariant] Each instance gets its own last_pong timestamp
    and reconnect_token (no shared mutable state)."""
    cs1 = _ClientState(websocket=object(), project_id="p-1")
    time.sleep(0.001)  # ensure timestamps differ
    cs2 = _ClientState(websocket=object(), project_id="p-2")
    # Different reconnect tokens (URL-safe random):
    assert cs1.reconnect_token != cs2.reconnect_token
    # last_pong is monotonic time -> later instance has higher value:
    assert cs2.last_pong >= cs1.last_pong


def test_client_state_reconnect_token_url_safe_length():
    """[security] secrets.token_urlsafe(32) -> ~43 chars (32 bytes
    base64-encoded). Pin to confirm sufficient entropy for token
    randomization."""
    cs = _ClientState(websocket=object(), project_id="p-1")
    # 32 bytes URL-safe base64 -> 43 chars (no padding):
    assert len(cs.reconnect_token) >= 32
    # URL-safe characters only (alphanumerics + - + _):
    assert all(c.isalnum() or c in "-_" for c in cs.reconnect_token)


def test_client_state_heartbeat_task_starts_none():
    cs = _ClientState(websocket=object(), project_id="p-1")
    assert cs.heartbeat_task is None


def test_client_state_explicit_user_id():
    cs = _ClientState(websocket=object(), project_id="p-1", user_id="user-xyz")
    assert cs.user_id == "user-xyz"


# =========================================================================
# _QueuedMessage dataclass
# =========================================================================


def test_queued_message_holds_timestamp_message_sequence():
    qm = _QueuedMessage(timestamp=123.456, message={"alert": "x"}, sequence=5)
    assert qm.timestamp == 123.456
    assert qm.message == {"alert": "x"}
    assert qm.sequence == 5


def test_queued_message_message_can_be_mutated_independently():
    """Each instance has its own message dict (no aliasing across
    instances)."""
    a = _QueuedMessage(timestamp=1.0, message={"id": "a"}, sequence=1)
    b = _QueuedMessage(timestamp=2.0, message={"id": "b"}, sequence=2)
    a.message["new"] = "x"
    assert "new" not in b.message


# =========================================================================
# SafetyWebSocketManager initial state
# =========================================================================


def test_manager_starts_with_empty_state():
    """Fresh manager has no connections, no queued messages, no
    sequences, no reconnect tokens."""
    m = SafetyWebSocketManager()
    assert m.connections == {}
    assert m._client_states == {}
    assert m._project_queues == {}
    assert m._project_sequences == {}
    assert m._reconnect_tokens == {}


def test_manager_state_is_per_instance_not_class():
    """[invariant] Multiple manager instances don't share state.
    Pin: refactor must not accidentally use class-level dicts."""
    m1 = SafetyWebSocketManager()
    m2 = SafetyWebSocketManager()
    m1.connections["p-1"] = []
    assert "p-1" not in m2.connections


# =========================================================================
# connect — early auth rejection paths (no FastAPI fixtures needed)
# =========================================================================


class _FakeWebSocket:
    """Minimal WebSocket stub that just records close() calls."""

    def __init__(self):
        self.closed_with: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed_with = (code, reason)


@pytest.mark.asyncio
async def test_connect_rejects_no_token():
    """[security/H-19] No token -> close with code 4001."""
    m = SafetyWebSocketManager()
    ws = _FakeWebSocket()
    out = await m.connect(websocket=ws, project_id="p-1", token=None)
    assert out is False
    assert ws.closed_with is not None
    assert ws.closed_with[0] == 4001
    assert "Authentication" in ws.closed_with[1]


@pytest.mark.asyncio
async def test_connect_rejects_invalid_token():
    """[security/H-19] Invalid JWT -> close with code 4001."""
    from unittest.mock import patch

    m = SafetyWebSocketManager()
    ws = _FakeWebSocket()
    with patch(
        "app.services.realtime.websocket_server.decode_access_token",
        return_value=None,
    ):
        out = await m.connect(
            websocket=ws,
            project_id="p-1",
            token="invalid.jwt.token",
        )

    assert out is False
    assert ws.closed_with is not None
    assert ws.closed_with[0] == 4001
