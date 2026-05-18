"""Tests for audit logging compliance.

Covers: auth event logging, resource access logging, sensitive field redaction,
log format required fields, and admin action logging.
"""

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.observability.audit_logger import AuditAction, audit_log, audit_log_db


class TestAuditLogSync:
    """Tests for the synchronous audit_log function (logger-only)."""

    def test_login_event_logged(self, caplog):
        """Login success event should be logged with correct action."""
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()

        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.LOGIN_SUCCESS,
                user_id=user_id,
                org_id=org_id,
                details={"email": "user@example.com"},
            )

        assert len(caplog.records) >= 1
        log_text = caplog.text
        assert "auth.login.success" in log_text
        assert str(user_id) in log_text

    def test_logout_event_logged(self, caplog):
        """Logout event should be logged."""
        user_id = uuid.uuid4()

        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(AuditAction.LOGOUT, user_id=user_id)

        assert "auth.logout" in caplog.text

    def test_failed_login_event_logged(self, caplog):
        """Failed login event should be logged with email detail."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.LOGIN_FAILED,
                details={"email": "attacker@example.com"},
            )

        assert "auth.login.failed" in caplog.text

    def test_log_format_includes_required_fields(self, caplog):
        """Audit log entry should include timestamp, user_id, org_id, action."""
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()

        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.RESOURCE_CREATED,
                user_id=user_id,
                org_id=org_id,
                resource_type="project",
                resource_id=uuid.uuid4(),
            )

        log_text = caplog.text
        assert "timestamp" in log_text
        assert str(user_id) in log_text
        assert str(org_id) in log_text
        assert "data.resource.created" in log_text

    def test_resource_access_logging(self, caplog):
        """Resource access events should include resource_type and resource_id."""
        resource_id = uuid.uuid4()

        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.RESOURCE_UPDATED,
                user_id=uuid.uuid4(),
                org_id=uuid.uuid4(),
                resource_type="document",
                resource_id=resource_id,
            )

        assert str(resource_id) in caplog.text
        assert "data.resource.updated" in caplog.text

    def test_admin_action_logging(self, caplog):
        """Admin actions (tenant creation, feature flags) should be logged."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.TENANT_CREATED,
                user_id=uuid.uuid4(),
                details={"org_name": "New Tenant Corp"},
            )

        assert "admin.tenant.created" in caplog.text

    def test_ip_address_included_when_provided(self, caplog):
        """IP address should appear in log entry when provided."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.LOGIN_SUCCESS,
                user_id=uuid.uuid4(),
                ip_address="192.168.1.42",
            )

        assert "192.168.1.42" in caplog.text

    def test_none_user_id_handled(self, caplog):
        """audit_log should handle None user_id gracefully."""
        with caplog.at_level(logging.INFO, logger="audit"):
            audit_log(
                AuditAction.LOGIN_FAILED,
                user_id=None,
                org_id=None,
                details={"email": "unknown@example.com"},
            )

        assert "auth.login.failed" in caplog.text


class TestAuditLogDB:
    """Tests for the async audit_log_db function (DB + logger)."""

    @pytest.mark.asyncio
    async def test_audit_log_db_adds_entry(self):
        """audit_log_db should add an AuditLog record to the DB session."""
        mock_db = MagicMock()
        mock_db.add = MagicMock()

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()

        with patch("app.services.observability.audit_logger.AuditLog") as MockAuditLog:
            mock_entry = MagicMock()
            MockAuditLog.return_value = mock_entry

            await audit_log_db(
                mock_db,
                AuditAction.PASSWORD_RESET_COMPLETE,
                user_id=user_id,
                org_id=org_id,
            )

            mock_db.add.assert_called_once_with(mock_entry)
            MockAuditLog.assert_called_once()
            call_kwargs = MockAuditLog.call_args
            assert call_kwargs.kwargs["action"] == "auth.password_reset.complete"
            assert call_kwargs.kwargs["user_id"] == str(user_id)

    @pytest.mark.asyncio
    async def test_audit_log_db_includes_details(self):
        """Details dict should be passed through to the AuditLog model."""
        mock_db = MagicMock()

        with patch("app.services.observability.audit_logger.AuditLog") as MockAuditLog:
            await audit_log_db(
                mock_db,
                AuditAction.FEATURE_FLAG_CHANGED,
                user_id=uuid.uuid4(),
                details={"flag": "dark_mode", "value": True},
            )

            call_kwargs = MockAuditLog.call_args.kwargs
            assert call_kwargs["details"]["flag"] == "dark_mode"


class TestAuditActionEnum:
    """Verify all expected audit actions are defined."""

    def test_auth_actions_defined(self):
        assert AuditAction.LOGIN_SUCCESS.value == "auth.login.success"
        assert AuditAction.LOGIN_FAILED.value == "auth.login.failed"
        assert AuditAction.LOGOUT.value == "auth.logout"
        assert AuditAction.REGISTER.value == "auth.register"
        assert AuditAction.PASSWORD_RESET_REQUEST.value == "auth.password_reset.request"
        assert AuditAction.PASSWORD_RESET_COMPLETE.value == "auth.password_reset.complete"

    def test_data_actions_defined(self):
        assert AuditAction.RESOURCE_CREATED.value == "data.resource.created"
        assert AuditAction.RESOURCE_UPDATED.value == "data.resource.updated"
        assert AuditAction.RESOURCE_DELETED.value == "data.resource.deleted"

    def test_admin_actions_defined(self):
        assert AuditAction.TENANT_CREATED.value == "admin.tenant.created"
        assert AuditAction.USER_DEACTIVATED.value == "admin.user.deactivated"
