from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.realtime.websocket_server import SafetyWebSocketManager


def _mock_token_payload() -> dict:
    # Omit org_id so the DB-backed project access check is skipped.
    return {
        "sub": "user-1",
        "scope": "user",
        "iss": "constructai",
        "aud": "constructai-api",
    }


@pytest.fixture
def patched_decode():
    """Bypass JWT validation in WebSocket tests."""
    with patch(
        "app.services.realtime.websocket_server.decode_access_token",
        return_value=_mock_token_payload(),
    ):
        yield


class TestWebSocketServer:
    @pytest.mark.asyncio
    async def test_connect_adds_to_connections(self, patched_decode):
        mgr = SafetyWebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws, "proj-1", token="dummy")
        assert len(mgr.connections["proj-1"]) == 1

    @pytest.mark.asyncio
    async def test_disconnect_removes(self, patched_decode):
        mgr = SafetyWebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws, "proj-1", token="dummy")
        mgr.disconnect(ws, "proj-1")
        assert len(mgr.connections["proj-1"]) == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self, patched_decode):
        mgr = SafetyWebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, "proj-1", token="dummy")
        await mgr.connect(ws2, "proj-1", token="dummy")
        await mgr.broadcast("proj-1", {"test": True})
        ws1.send_json.assert_any_call({"test": True})
        ws2.send_json.assert_any_call({"test": True})
