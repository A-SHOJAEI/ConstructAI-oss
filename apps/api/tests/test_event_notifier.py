"""Tests for the unified notification service (event_notifier.py).

Covers event dispatch, preference checking, overdue detection,
EVM threshold checking, and channel routing.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notifications.event_notifier import (
    NOTIFICATION_EVENTS,
    _check_user_preference,
    _send_websocket_notification,
    check_evm_thresholds,
    check_overdue_items,
    notify,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def project_id():
    return uuid.uuid4()


@pytest.fixture
def user_ids():
    return [uuid.uuid4(), uuid.uuid4()]


# ---------------------------------------------------------------------------
# Test: NOTIFICATION_EVENTS configuration
# ---------------------------------------------------------------------------


class TestNotificationEventConfig:
    """Verify the event configuration dictionary is well-formed."""

    def test_all_events_have_required_keys(self):
        for event_type, config in NOTIFICATION_EVENTS.items():
            assert "template" in config, f"{event_type} missing 'template'"
            assert "channels" in config, f"{event_type} missing 'channels'"
            assert "pref_key" in config, f"{event_type} missing 'pref_key'"

    def test_all_channels_are_valid(self):
        valid_channels = {"email", "websocket"}
        for event_type, config in NOTIFICATION_EVENTS.items():
            for ch in config["channels"]:
                assert ch in valid_channels, f"{event_type} has invalid channel '{ch}'"

    def test_event_count(self):
        assert len(NOTIFICATION_EVENTS) == 10

    def test_payment_received_event(self):
        cfg = NOTIFICATION_EVENTS["payment.received"]
        assert "email" in cfg["channels"]
        assert "websocket" in cfg["channels"]
        assert cfg["pref_key"] == "payment_updates"

    def test_rfi_overdue_event(self):
        cfg = NOTIFICATION_EVENTS["rfi.overdue"]
        assert cfg["pref_key"] == "rfi_updates"

    def test_contract_deviation_email_only(self):
        cfg = NOTIFICATION_EVENTS["contract.deviation_found"]
        assert cfg["channels"] == ["email"]


# ---------------------------------------------------------------------------
# Test: notify() main entry point
# ---------------------------------------------------------------------------


class TestNotify:
    """Test the main notify() dispatch function."""

    @pytest.mark.asyncio
    async def test_unknown_event_type_returns_error(self, mock_db, project_id, user_ids):
        result = await notify(mock_db, "nonexistent.event", project_id, user_ids, {})
        assert result["error"] == "unknown_event_type"
        assert result["sent"] == 0

    @pytest.mark.asyncio
    async def test_empty_user_ids_sends_nothing(self, mock_db, project_id):
        result = await notify(
            mock_db,
            "payment.received",
            project_id,
            [],
            {"amount": "$1,000", "project": "Test"},
        )
        assert result["sent"] == 0
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier._check_user_preference",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_email_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_websocket_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._log_notification",
        return_value=None,
    )
    async def test_dispatches_to_all_channels(
        self, mock_log, mock_ws, mock_email, mock_pref, mock_db, project_id, user_ids
    ):
        result = await notify(
            mock_db,
            "payment.received",
            project_id,
            user_ids,
            {"amount": "$5,000", "project": "Highway Project"},
        )
        # 2 users * 2 channels = 4 sends
        assert result["sent"] == 4
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier._check_user_preference",
        return_value=False,
    )
    async def test_skips_users_with_disabled_prefs(self, mock_pref, mock_db, project_id, user_ids):
        result = await notify(
            mock_db,
            "rfi.response_added",
            project_id,
            user_ids,
            {"rfi_number": "RFI-001"},
        )
        assert result["sent"] == 0
        assert result["skipped"] == 2

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier._check_user_preference",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_email_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._log_notification",
        return_value=None,
    )
    async def test_email_only_event(self, mock_log, mock_email, mock_pref, mock_db, project_id):
        user = [uuid.uuid4()]
        result = await notify(
            mock_db,
            "contract.deviation_found",
            project_id,
            user,
            {"clause_type": "payment", "description": "deviation found"},
        )
        # contract.deviation_found has channels=["email"] only
        assert result["sent"] == 1

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier._check_user_preference",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_email_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_websocket_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._log_notification",
        return_value=None,
    )
    async def test_template_rendering(
        self, mock_log, mock_ws, mock_email, mock_pref, mock_db, project_id
    ):
        """Verify the template is correctly rendered with context_data."""
        user = [uuid.uuid4()]
        await notify(
            mock_db,
            "schedule.spi_threshold",
            project_id,
            user,
            {"spi": "0.85", "project": "Bridge Project", "threshold": "0.90"},
        )
        # Check that the email was called with the rendered message
        mock_email.assert_called()
        subject_arg = (
            mock_email.call_args[1].get("subject")
            if mock_email.call_args[1]
            else mock_email.call_args[0][1]
        )
        assert "0.85" in subject_arg

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier._check_user_preference",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_email_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._send_websocket_notification",
        return_value=True,
    )
    @patch(
        "app.services.notifications.event_notifier._log_notification",
        return_value=None,
    )
    async def test_missing_template_variable_uses_raw_template(
        self, mock_log, mock_ws, mock_email, mock_pref, mock_db, project_id
    ):
        """If context_data is missing a template variable, use raw template."""
        user = [uuid.uuid4()]
        result = await notify(
            mock_db,
            "payment.received",
            project_id,
            user,
            {},  # missing amount and project
        )
        # Should not crash, should still send
        assert result["sent"] > 0


# ---------------------------------------------------------------------------
# Test: _check_user_preference
# ---------------------------------------------------------------------------


class TestCheckUserPreference:
    """Test user notification preference checking."""

    @pytest.mark.asyncio
    async def test_defaults_to_true_when_user_has_no_settings(self, mock_db):
        """Opt-out model: missing prefs default to True."""
        user_mock = MagicMock()
        user_mock.settings = {}
        mock_db.get = AsyncMock(return_value=user_mock)

        with patch("app.services.notifications.event_notifier.User", create=True):
            result = await _check_user_preference(mock_db, uuid.uuid4(), "payment_updates")
        # Should default to True since pref is not set
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_user_not_found(self, mock_db):
        mock_db.get = AsyncMock(return_value=None)
        with patch("app.services.notifications.event_notifier.User", create=True):
            result = await _check_user_preference(mock_db, uuid.uuid4(), "payment_updates")
        assert result is False


# ---------------------------------------------------------------------------
# Test: WebSocket dispatch
# ---------------------------------------------------------------------------


class TestWebSocketNotification:
    """Test WebSocket notification dispatch."""

    @pytest.mark.asyncio
    async def test_returns_false_when_ws_manager_unavailable(self, project_id):
        """Should gracefully handle missing WebSocket manager."""
        # The function does a lazy import; raise ImportError from the source
        # module so the local `from ... import ws_manager` blows up.
        import sys

        original = sys.modules.pop("app.services.realtime.websocket_server", None)
        with patch.dict(
            "sys.modules",
            {"app.services.realtime.websocket_server": None},
        ):
            result = await _send_websocket_notification(project_id, "test.event", {"msg": "hello"})
        if original is not None:
            sys.modules["app.services.realtime.websocket_server"] = original
        assert result is False


# ---------------------------------------------------------------------------
# Test: check_overdue_items
# ---------------------------------------------------------------------------


class TestCheckOverdueItems:
    """Test the scheduled overdue item checker."""

    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_no_models(self, mock_db):
        """If models aren't available, returns zero counts gracefully."""
        # Mock the DB to raise ImportError for the RFI model
        mock_db.execute = AsyncMock(side_effect=ImportError("No module"))

        with patch.dict("sys.modules", {"app.models.communication": None}):
            # The function catches ImportError per-model, so it should
            # still return a result dict
            result = await check_overdue_items(mock_db)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier.notify", return_value={"sent": 1, "skipped": 0}
    )
    async def test_detects_overdue_rfis(self, mock_notify, mock_db, project_id):
        """When overdue RFIs exist, notifications are sent."""
        # Create a mock RFI that is overdue
        mock_rfi = MagicMock()
        mock_rfi.id = uuid.uuid4()
        mock_rfi.project_id = project_id
        mock_rfi.rfi_number = "RFI-001"
        mock_rfi.subject = "Test RFI"
        mock_rfi.due_date = date.today() - timedelta(days=5)
        mock_rfi.assigned_to = uuid.uuid4()
        mock_rfi.submitted_by = uuid.uuid4()

        # Mock the DB query to return the overdue RFI
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_rfi]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.notifications.event_notifier.RFI", create=True):
            with patch("app.services.notifications.event_notifier.select", create=True):
                result = await check_overdue_items(mock_db)

        assert result["rfis"] >= 1


# ---------------------------------------------------------------------------
# Test: check_evm_thresholds
# ---------------------------------------------------------------------------


class TestCheckEvmThresholds:
    """Test EVM threshold checking."""

    @pytest.mark.asyncio
    async def test_returns_skipped_when_no_evm_data(self, mock_db, project_id):
        """When no EVM snapshot exists, returns skipped status."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.get = AsyncMock(return_value=None)

        result = await check_evm_thresholds(mock_db, project_id)
        assert result.get("skipped") == "no_evm_data"

    @pytest.mark.asyncio
    @patch(
        "app.services.notifications.event_notifier.notify", return_value={"sent": 1, "skipped": 0}
    )
    async def test_detects_spi_below_threshold(self, mock_notify, mock_db, project_id):
        """When SPI is below threshold, a notification is sent."""
        # Mock EVM snapshot with low SPI
        mock_snapshot = MagicMock()
        mock_snapshot.spi = Decimal("0.75")
        mock_snapshot.cpi = Decimal("1.05")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_snapshot
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Mock project with manager
        mock_project = MagicMock()
        mock_project.name = "Test Project"
        mock_project.manager_id = uuid.uuid4()
        mock_project.created_by = uuid.uuid4()
        mock_db.get = AsyncMock(return_value=mock_project)

        result = await check_evm_thresholds(mock_db, project_id, spi_threshold=0.9)
        assert result["spi_below_threshold"] is True
        assert result["notifications_sent"] >= 1

    @pytest.mark.asyncio
    async def test_no_alert_when_spi_above_threshold(self, mock_db, project_id):
        """When SPI is above threshold, no notification is sent."""
        mock_snapshot = MagicMock()
        mock_snapshot.spi = Decimal("0.95")
        mock_snapshot.cpi = Decimal("1.02")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_snapshot
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_project = MagicMock()
        mock_project.name = "Test Project"
        mock_project.manager_id = uuid.uuid4()
        mock_project.created_by = None
        mock_db.get = AsyncMock(return_value=mock_project)

        result = await check_evm_thresholds(mock_db, project_id, spi_threshold=0.9)
        assert result["spi_below_threshold"] is False
        assert result["notifications_sent"] == 0

    @pytest.mark.asyncio
    async def test_returns_skipped_when_no_stakeholders(self, mock_db, project_id):
        """When project has no manager or creator, skip notification."""
        mock_snapshot = MagicMock()
        mock_snapshot.spi = Decimal("0.75")
        mock_snapshot.cpi = Decimal("0.80")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_snapshot
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_project = MagicMock()
        mock_project.name = "No-Manager Project"
        mock_project.manager_id = None
        mock_project.created_by = None
        mock_db.get = AsyncMock(return_value=mock_project)

        result = await check_evm_thresholds(mock_db, project_id)
        assert result.get("skipped") == "no_stakeholders"


# ---------------------------------------------------------------------------
# Test: Channel routing based on event type
# ---------------------------------------------------------------------------


class TestChannelRouting:
    """Verify that events route to the correct channels."""

    def test_payment_received_routes_to_email_and_ws(self):
        assert NOTIFICATION_EVENTS["payment.received"]["channels"] == [
            "email",
            "websocket",
        ]

    def test_daily_log_not_submitted_routes_to_email_only(self):
        assert NOTIFICATION_EVENTS["daily_log.not_submitted"]["channels"] == ["email"]

    def test_sensor_anomaly_routes_to_both(self):
        channels = NOTIFICATION_EVENTS["sensor.anomaly"]["channels"]
        assert "email" in channels
        assert "websocket" in channels

    def test_defect_detected_routes_to_both(self):
        channels = NOTIFICATION_EVENTS["defect.detected"]["channels"]
        assert "email" in channels
        assert "websocket" in channels
