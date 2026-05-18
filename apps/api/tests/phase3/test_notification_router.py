from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.safety.notification_router import route_notification


class TestNotificationRouter:
    async def test_p1_triggers_sms(self):
        alert = {"id": "a1", "priority": "P1_critical", "description": "Test"}
        with patch(
            "app.services.safety.notification_router._send_sms",
            new_callable=AsyncMock,
        ) as mock_sms:
            result = await route_notification(alert)
            mock_sms.assert_called_once()
            assert "sms" in result["channels"]

    async def test_p2_triggers_email(self):
        alert = {"id": "a2", "priority": "P2_high", "description": "Test"}
        with patch(
            "app.services.safety.notification_router._send_email",
            new_callable=AsyncMock,
        ) as mock_email:
            result = await route_notification(alert)
            mock_email.assert_called_once()
            assert "email" in result["channels"]

    async def test_p3_queued_for_review(self):
        alert = {"id": "a3", "priority": "P3_medium", "description": "Test"}
        result = await route_notification(alert)
        assert "review_queue" in result["channels"]

    async def test_p5_added_to_digest(self):
        alert = {"id": "a5", "priority": "P5_info", "description": "Test"}
        result = await route_notification(alert)
        assert "digest" in result["channels"]
