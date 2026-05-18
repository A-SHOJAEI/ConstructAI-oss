"""Tests for Instant Pay (Feature 4.3).

40+ tests covering progress-to-SOV mapping, auto pay app generation,
payment submission, webhook handling, lien waivers, retainage computation,
config management, and payment status.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unit under test imports
# ---------------------------------------------------------------------------
from app.services.controls.instant_pay_service import (
    _map_progress_to_sov,
    compute_retainage,
)

# ===========================================================================
# TestProgressToSOV — 7 tests
# ===========================================================================


class TestProgressToSOV:
    """Tests for mapping progress snapshot data to SOV line items."""

    def test_wbs_prefix_matching(self):
        """Activities with WBS codes matching SOV item_number should map."""
        progress = {"act-1": 50.0, "act-2": 30.0}
        sov_items = [
            {
                "id": "sov-1",
                "item_number": "03.01",
                "description": "Concrete Foundation",
                "scheduled_value": 100000,
                "csi_code": "03 30 00",
            },
            {
                "id": "sov-2",
                "item_number": "09.01",
                "description": "Interior Finishes",
                "scheduled_value": 50000,
                "csi_code": "09 29 00",
            },
        ]
        activities = [
            {
                "id": "act-1",
                "name": "Pour Foundation",
                "wbs_code": "03.01",
                "activity_code": "A100",
                "pct_complete": 0,
            },
            {
                "id": "act-2",
                "name": "Drywall Install",
                "wbs_code": "09.01",
                "activity_code": "A200",
                "pct_complete": 0,
            },
        ]

        result = _map_progress_to_sov(progress, sov_items, activities)

        # Both SOV items should be matched
        matched = [r for r in result if r["work_completed_this_period"] > Decimal("0")]
        assert len(matched) == 2

        # Check amounts: 50% of $100k = $50k, 30% of $50k = $15k
        sov1 = next(r for r in matched if r["sov_id"] == "sov-1")
        assert sov1["work_completed_this_period"] == Decimal("50000.00")

        sov2 = next(r for r in matched if r["sov_id"] == "sov-2")
        assert sov2["work_completed_this_period"] == Decimal("15000.00")

    def test_csi_code_matching(self):
        """Activities with matching CSI code prefixes should map to SOV items."""
        progress = {"act-1": 25.0}
        sov_items = [
            {
                "id": "sov-1",
                "item_number": "1",
                "description": "Concrete Work",
                "scheduled_value": 200000,
                "csi_code": "03 30 00",
            },
        ]
        activities = [
            {
                "id": "act-1",
                "name": "Concrete Pour",
                "wbs_code": "",
                "activity_code": "0330",
                "pct_complete": 0,
            },
        ]

        result = _map_progress_to_sov(progress, sov_items, activities)
        matched = [r for r in result if r["work_completed_this_period"] > Decimal("0")]
        assert len(matched) == 1
        assert matched[0]["work_completed_this_period"] == Decimal("50000.00")

    def test_keyword_matching(self):
        """Activities should match SOV items by keyword overlap in descriptions."""
        progress = {"act-1": 40.0}
        sov_items = [
            {
                "id": "sov-1",
                "item_number": "1",
                "description": "structural steel erection beams",
                "scheduled_value": 300000,
                "csi_code": None,
            },
        ]
        activities = [
            {
                "id": "act-1",
                "name": "steel erection phase 1",
                "wbs_code": "",
                "activity_code": "",
                "pct_complete": 0,
            },
        ]

        result = _map_progress_to_sov(progress, sov_items, activities)
        matched = [r for r in result if r["work_completed_this_period"] > Decimal("0")]
        assert len(matched) == 1
        assert matched[0]["work_completed_this_period"] == Decimal("120000.00")

    def test_unmatched_sov_items_included_with_zero(self):
        """SOV items without matching activities should be included with zero billing."""
        progress = {"act-1": 50.0}
        sov_items = [
            {
                "id": "sov-1",
                "item_number": "03.01",
                "description": "Concrete",
                "scheduled_value": 100000,
                "csi_code": None,
            },
            {
                "id": "sov-2",
                "item_number": "99.99",
                "description": "Miscellaneous",
                "scheduled_value": 25000,
                "csi_code": None,
            },
        ]
        activities = [
            {
                "id": "act-1",
                "name": "concrete work",
                "wbs_code": "03.01",
                "activity_code": "",
                "pct_complete": 0,
            },
        ]

        result = _map_progress_to_sov(progress, sov_items, activities)
        assert len(result) == 2

        misc_item = next(r for r in result if r["sov_id"] == "sov-2")
        assert misc_item["work_completed_this_period"] == Decimal("0")

    def test_empty_progress_returns_empty(self):
        result = _map_progress_to_sov({}, [{"id": "1", "scheduled_value": 100}], [])
        assert result == []

    def test_empty_sov_returns_empty(self):
        result = _map_progress_to_sov({"act-1": 50}, [], [])
        assert result == []

    def test_zero_scheduled_value_skipped(self):
        """SOV items with zero scheduled value should be skipped."""
        progress = {"act-1": 50.0}
        sov_items = [
            {
                "id": "sov-1",
                "item_number": "1",
                "description": "Nothing",
                "scheduled_value": 0,
                "csi_code": None,
            },
        ]
        activities = [
            {
                "id": "act-1",
                "name": "concrete work",
                "wbs_code": "1",
                "activity_code": "",
                "pct_complete": 0,
            },
        ]

        result = _map_progress_to_sov(progress, sov_items, activities)
        assert len(result) == 0


# ===========================================================================
# TestAutoPayApp — 6 tests
# ===========================================================================


class TestAutoPayApp:
    """Tests for auto-generating pay applications from progress snapshots."""

    @pytest.mark.asyncio
    async def test_auto_generate_calls_create_pay_app(self):
        """Should map progress to SOV and call create_pay_application."""
        project_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()

        mock_snapshot = MagicMock()
        mock_snapshot.project_id = project_id
        mock_snapshot.activities_progress = {"act-1": 50.0}

        mock_sov = MagicMock()
        mock_sov.id = uuid.uuid4()
        mock_sov.item_number = "1"
        mock_sov.description = "Concrete"
        mock_sov.scheduled_value = Decimal("100000")
        mock_sov.csi_code = "03 30 00"
        mock_sov.sort_order = 0

        mock_activity = MagicMock()
        mock_activity.id = uuid.uuid4()
        mock_activity.name = "concrete work"
        mock_activity.wbs_code = "1"
        mock_activity.activity_code = "A100"
        mock_activity.pct_complete = Decimal("0")

        mock_sov_result = MagicMock()
        mock_sov_result.scalars.return_value.all.return_value = [mock_sov]

        mock_act_result = MagicMock()
        mock_act_result.scalars.return_value.all.return_value = [mock_activity]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_snapshot)
        mock_db.execute = AsyncMock(side_effect=[mock_sov_result, mock_act_result])

        mock_pay_app = MagicMock()
        mock_pay_app.application_number = 1

        with (
            patch(
                "app.services.controls.pay_application_service.create_pay_application",
                new_callable=AsyncMock,
                return_value=mock_pay_app,
            ) as mock_create,
            patch(
                "app.services.controls.instant_pay_service._get_payment_config",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            from app.services.controls.instant_pay_service import (
                auto_generate_pay_app_from_progress,
            )

            result = await auto_generate_pay_app_from_progress(
                db=mock_db,
                project_id=project_id,
                snapshot_id=snapshot_id,
                period_to=date(2026, 3, 15),
            )

        mock_create.assert_called_once()
        assert result == mock_pay_app

    @pytest.mark.asyncio
    async def test_auto_generate_snapshot_not_found(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        from app.services.controls.instant_pay_service import auto_generate_pay_app_from_progress

        with pytest.raises(ValueError, match="snapshot not found"):
            await auto_generate_pay_app_from_progress(
                mock_db, uuid.uuid4(), uuid.uuid4(), date.today()
            )

    @pytest.mark.asyncio
    async def test_auto_generate_wrong_project(self):
        mock_snapshot = MagicMock()
        mock_snapshot.project_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_snapshot)

        from app.services.controls.instant_pay_service import auto_generate_pay_app_from_progress

        with pytest.raises(ValueError, match="does not belong"):
            await auto_generate_pay_app_from_progress(
                mock_db, uuid.uuid4(), uuid.uuid4(), date.today()
            )

    @pytest.mark.asyncio
    async def test_auto_generate_empty_progress(self):
        project_id = uuid.uuid4()
        mock_snapshot = MagicMock()
        mock_snapshot.project_id = project_id
        mock_snapshot.activities_progress = {}

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_snapshot)

        from app.services.controls.instant_pay_service import auto_generate_pay_app_from_progress

        with pytest.raises(ValueError, match="no activity data"):
            await auto_generate_pay_app_from_progress(
                mock_db, project_id, uuid.uuid4(), date.today()
            )

    @pytest.mark.asyncio
    async def test_auto_generate_no_sov(self):
        project_id = uuid.uuid4()
        mock_snapshot = MagicMock()
        mock_snapshot.project_id = project_id
        mock_snapshot.activities_progress = {"act-1": 50.0}

        mock_sov_result = MagicMock()
        mock_sov_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_snapshot)
        mock_db.execute = AsyncMock(return_value=mock_sov_result)

        from app.services.controls.instant_pay_service import auto_generate_pay_app_from_progress

        with pytest.raises(ValueError, match="No Schedule of Values"):
            await auto_generate_pay_app_from_progress(
                mock_db, project_id, uuid.uuid4(), date.today()
            )

    @pytest.mark.asyncio
    async def test_auto_generate_uses_config_retainage(self):
        """Should use retainage from payment config when available."""
        project_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()

        mock_snapshot = MagicMock()
        mock_snapshot.project_id = project_id
        mock_snapshot.activities_progress = {"act-1": 50.0}

        mock_sov = MagicMock()
        mock_sov.id = uuid.uuid4()
        mock_sov.item_number = "1"
        mock_sov.description = "Work"
        mock_sov.scheduled_value = Decimal("10000")
        mock_sov.csi_code = None
        mock_sov.sort_order = 0

        mock_activity = MagicMock()
        mock_activity.id = uuid.uuid4()
        mock_activity.name = "work item"
        mock_activity.wbs_code = "1"
        mock_activity.activity_code = ""
        mock_activity.pct_complete = Decimal("0")

        mock_sov_result = MagicMock()
        mock_sov_result.scalars.return_value.all.return_value = [mock_sov]
        mock_act_result = MagicMock()
        mock_act_result.scalars.return_value.all.return_value = [mock_activity]

        mock_config = MagicMock()
        mock_config.retainage_pct = Decimal("5.00")

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_snapshot)
        mock_db.execute = AsyncMock(side_effect=[mock_sov_result, mock_act_result])

        with (
            patch(
                "app.services.controls.pay_application_service.create_pay_application",
                new_callable=AsyncMock,
                return_value=MagicMock(application_number=1),
            ) as mock_create,
            patch(
                "app.services.controls.instant_pay_service._get_payment_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
        ):
            from app.services.controls.instant_pay_service import (
                auto_generate_pay_app_from_progress,
            )

            await auto_generate_pay_app_from_progress(
                mock_db, project_id, snapshot_id, date.today()
            )

        # Verify retainage_pct=5.00 was passed
        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs.get("retainage_pct") == Decimal("5.00")


# ===========================================================================
# TestPaymentSubmission — 5 tests
# ===========================================================================


class TestPaymentSubmission:
    """Tests for submitting payments."""

    @pytest.mark.asyncio
    async def test_submit_payment_creates_transaction(self):
        pay_app_id = uuid.uuid4()
        project_id = uuid.uuid4()

        mock_pay_app = MagicMock()
        mock_pay_app.id = pay_app_id
        mock_pay_app.project_id = project_id
        mock_pay_app.status = "certified"
        mock_pay_app.current_payment_due = Decimal("50000.00")
        mock_pay_app.retainage_pct = Decimal("10.00")
        mock_pay_app.application_number = 1
        mock_pay_app.period_to = date(2026, 3, 15)
        mock_pay_app.contractor_info = {"name": "Builder Co"}
        mock_pay_app.architect_info = {"name": "Architect LLC"}

        mock_existing = MagicMock()
        mock_existing.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        mock_db.execute = AsyncMock(return_value=mock_existing)
        added_objects = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "app.services.controls.instant_pay_service._get_payment_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.services.controls.instant_pay_service import submit_payment

            await submit_payment(mock_db, pay_app_id, "ach")

        assert len(added_objects) == 1
        txn = added_objects[0]
        assert txn.status == "submitted"
        assert txn.net_amount == Decimal("45000.00")  # 50k - 10% retainage
        assert txn.retainage_amount == Decimal("5000.00")

    @pytest.mark.asyncio
    async def test_submit_payment_not_certified(self):
        mock_pay_app = MagicMock()
        mock_pay_app.status = "draft"

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)

        from app.services.controls.instant_pay_service import submit_payment

        with pytest.raises(ValueError, match="must be certified"):
            await submit_payment(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_submit_payment_not_found(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        from app.services.controls.instant_pay_service import submit_payment

        with pytest.raises(ValueError, match="not found"):
            await submit_payment(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_submit_payment_duplicate_blocked(self):
        mock_pay_app = MagicMock()
        mock_pay_app.status = "certified"

        mock_existing = MagicMock()
        mock_existing.scalars.return_value.first.return_value = MagicMock()  # existing transaction

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        mock_db.execute = AsyncMock(return_value=mock_existing)

        from app.services.controls.instant_pay_service import submit_payment

        with pytest.raises(ValueError, match="already exists"):
            await submit_payment(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_submit_payment_with_method(self):
        mock_pay_app = MagicMock()
        mock_pay_app.status = "certified"
        mock_pay_app.current_payment_due = Decimal("10000")
        mock_pay_app.retainage_pct = Decimal("10")
        mock_pay_app.project_id = uuid.uuid4()
        mock_pay_app.application_number = 1
        mock_pay_app.period_to = date.today()
        mock_pay_app.contractor_info = {}
        mock_pay_app.architect_info = {}

        mock_existing = MagicMock()
        mock_existing.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        mock_db.execute = AsyncMock(return_value=mock_existing)
        added = []
        mock_db.add = lambda obj: added.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "app.services.controls.instant_pay_service._get_payment_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.services.controls.instant_pay_service import submit_payment

            await submit_payment(mock_db, uuid.uuid4(), "wire")

        assert added[0].payment_method == "wire"


# ===========================================================================
# TestWebhook — 6 tests
# ===========================================================================


class TestWebhook:
    """Tests for payment webhook handling."""

    def _make_signature(self, payload: dict, secret: str) -> str:
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    @pytest.mark.asyncio
    async def test_webhook_valid_signature_updates_status(self):
        project_id = uuid.uuid4()
        secret = "test-webhook-secret"
        payload = {
            "transaction_id": "proc-txn-123",
            "event_type": "payment.paid",
            "status": "paid",
        }
        sig = self._make_signature(payload, secret)

        mock_txn = MagicMock()
        mock_txn.id = uuid.uuid4()
        mock_txn.status = "processing"
        mock_txn.approved_at = datetime.now(UTC)
        mock_txn.paid_at = None
        mock_txn.failed_at = None
        mock_txn.pay_application_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_txn

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "app.services.controls.instant_pay_service._get_payment_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.services.controls.instant_pay_service import handle_payment_webhook

            await handle_payment_webhook(mock_db, project_id, payload, sig, secret)

        assert mock_txn.status == "paid"
        assert mock_txn.paid_at is not None

    @pytest.mark.asyncio
    async def test_webhook_invalid_signature_raises(self):
        payload = {"transaction_id": "123", "event_type": "payment.paid"}

        mock_db = AsyncMock()

        from app.services.controls.instant_pay_service import handle_payment_webhook

        with pytest.raises(ValueError, match="Invalid webhook signature"):
            await handle_payment_webhook(mock_db, uuid.uuid4(), payload, "bad-signature", "secret")

    @pytest.mark.asyncio
    async def test_webhook_missing_transaction_id(self):
        secret = "secret"
        payload = {"event_type": "payment.paid"}
        sig = self._make_signature(payload, secret)

        mock_db = AsyncMock()

        from app.services.controls.instant_pay_service import handle_payment_webhook

        with pytest.raises(ValueError, match="missing transaction_id"):
            await handle_payment_webhook(mock_db, uuid.uuid4(), payload, sig, secret)

    @pytest.mark.asyncio
    async def test_webhook_transaction_not_found(self):
        secret = "secret"
        payload = {"transaction_id": "unknown", "event_type": "payment.paid"}
        sig = self._make_signature(payload, secret)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import handle_payment_webhook

        with pytest.raises(ValueError, match="not found"):
            await handle_payment_webhook(mock_db, uuid.uuid4(), payload, sig, secret)

    @pytest.mark.asyncio
    async def test_webhook_failed_event(self):
        secret = "secret"
        payload = {
            "transaction_id": "proc-123",
            "event_type": "payment.failed",
            "failure_reason": "Insufficient funds",
        }
        sig = self._make_signature(payload, secret)

        mock_txn = MagicMock()
        mock_txn.id = uuid.uuid4()
        mock_txn.status = "processing"
        mock_txn.paid_at = None
        mock_txn.failed_at = None
        mock_txn.pay_application_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_txn

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "app.services.controls.instant_pay_service._get_payment_config",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from app.services.controls.instant_pay_service import handle_payment_webhook

            await handle_payment_webhook(mock_db, uuid.uuid4(), payload, sig, secret)

        assert mock_txn.status == "failed"
        assert mock_txn.failure_reason == "Insufficient funds"

    @pytest.mark.asyncio
    async def test_webhook_auto_generates_lien_waiver(self):
        """On paid event with auto_generate_lien_waivers config, waivers should be created."""
        project_id = uuid.uuid4()
        secret = "secret"
        payload = {"transaction_id": "proc-456", "event_type": "payment.paid"}
        sig = self._make_signature(payload, secret)

        mock_txn = MagicMock()
        mock_txn.id = uuid.uuid4()
        mock_txn.status = "processing"
        mock_txn.paid_at = None
        mock_txn.failed_at = None
        mock_txn.approved_at = datetime.now(UTC)
        mock_txn.pay_application_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_txn

        mock_config = MagicMock()
        mock_config.auto_generate_lien_waivers = True

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch(
                "app.services.controls.instant_pay_service._get_payment_config",
                new_callable=AsyncMock,
                return_value=mock_config,
            ),
            patch(
                "app.services.controls.instant_pay_service.generate_lien_waiver_package",
                new_callable=AsyncMock,
            ) as mock_gen_lw,
        ):
            from app.services.controls.instant_pay_service import handle_payment_webhook

            await handle_payment_webhook(mock_db, project_id, payload, sig, secret)

        mock_gen_lw.assert_called_once_with(
            mock_db,
            mock_txn.pay_application_id,
            package_type="unconditional",
        )


# ===========================================================================
# TestLienWaivers — 5 tests
# ===========================================================================


class TestLienWaivers:
    """Tests for lien waiver package generation."""

    @pytest.mark.asyncio
    async def test_generate_conditional_package(self):
        pay_app_id = uuid.uuid4()
        project_id = uuid.uuid4()

        mock_li = MagicMock()
        mock_li.item_number = "1"
        mock_li.description_of_work = "Concrete"
        mock_li.work_completed_this_period = Decimal("25000")

        mock_pay_app = MagicMock()
        mock_pay_app.id = pay_app_id
        mock_pay_app.project_id = project_id
        mock_pay_app.current_payment_due = Decimal("25000")
        mock_pay_app.application_number = 1
        mock_pay_app.period_to = date(2026, 3, 15)
        mock_pay_app.contractor_info = {"name": "Builder Co"}
        mock_pay_app.line_items = [mock_li]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        added = []
        mock_db.add = lambda obj: added.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.controls.instant_pay_service import generate_lien_waiver_package

        await generate_lien_waiver_package(mock_db, pay_app_id, "conditional")

        # Should create LienWaiver + LienWaiverPackage
        assert len(added) == 2

    @pytest.mark.asyncio
    async def test_generate_unconditional_package(self):
        pay_app_id = uuid.uuid4()

        mock_li = MagicMock()
        mock_li.item_number = "1"
        mock_li.description_of_work = "Work"
        mock_li.work_completed_this_period = Decimal("10000")

        mock_pay_app = MagicMock()
        mock_pay_app.id = pay_app_id
        mock_pay_app.project_id = uuid.uuid4()
        mock_pay_app.current_payment_due = Decimal("10000")
        mock_pay_app.application_number = 1
        mock_pay_app.period_to = date.today()
        mock_pay_app.contractor_info = {"name": "Sub Co"}
        mock_pay_app.line_items = [mock_li]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        added = []
        mock_db.add = lambda obj: added.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.controls.instant_pay_service import generate_lien_waiver_package

        await generate_lien_waiver_package(mock_db, pay_app_id, "unconditional")

        pkg = [a for a in added if hasattr(a, "package_type")]
        assert len(pkg) == 1
        assert pkg[0].package_type == "unconditional"

    @pytest.mark.asyncio
    async def test_generate_invalid_type_raises(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=MagicMock())

        from app.services.controls.instant_pay_service import generate_lien_waiver_package

        with pytest.raises(ValueError, match="must be"):
            await generate_lien_waiver_package(mock_db, uuid.uuid4(), "invalid")

    @pytest.mark.asyncio
    async def test_generate_pay_app_not_found(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        from app.services.controls.instant_pay_service import generate_lien_waiver_package

        with pytest.raises(ValueError, match="not found"):
            await generate_lien_waiver_package(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_generate_uses_total_when_no_line_items(self):
        """When no line items have billing, use current_payment_due total."""
        pay_app_id = uuid.uuid4()

        mock_li = MagicMock()
        mock_li.work_completed_this_period = Decimal("0")

        mock_pay_app = MagicMock()
        mock_pay_app.id = pay_app_id
        mock_pay_app.project_id = uuid.uuid4()
        mock_pay_app.current_payment_due = Decimal("50000")
        mock_pay_app.application_number = 2
        mock_pay_app.period_to = date.today()
        mock_pay_app.contractor_info = {"name": "GC"}
        mock_pay_app.line_items = [mock_li]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_pay_app)
        added = []
        mock_db.add = lambda obj: added.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.controls.instant_pay_service import generate_lien_waiver_package

        await generate_lien_waiver_package(mock_db, pay_app_id, "conditional")

        pkg = [a for a in added if hasattr(a, "total_amount")]
        assert len(pkg) == 1
        assert pkg[0].total_amount == Decimal("50000.00")


# ===========================================================================
# TestRetainage — 4 tests
# ===========================================================================


class TestRetainage:
    """Tests for retainage computation (pure function)."""

    def test_standard_retainage(self):
        result = compute_retainage(Decimal("100000"), Decimal("10"))
        assert result["retainage_amount"] == Decimal("10000.00")
        assert result["net_amount"] == Decimal("90000.00")

    def test_zero_retainage(self):
        result = compute_retainage(Decimal("50000"), Decimal("0"))
        assert result["retainage_amount"] == Decimal("0.00")
        assert result["net_amount"] == Decimal("50000.00")

    def test_substantial_completion_releases_retainage(self):
        result = compute_retainage(Decimal("100000"), Decimal("10"), is_substantial_completion=True)
        assert result["retainage_amount"] == Decimal("0")
        assert result["net_amount"] == Decimal("100000.00")

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError, match="non-negative"):
            compute_retainage(Decimal("-1"), Decimal("10"))
        with pytest.raises(ValueError, match="between 0 and 100"):
            compute_retainage(Decimal("100"), Decimal("101"))


# ===========================================================================
# TestConfig — 4 tests
# ===========================================================================


class TestConfig:
    """Tests for payment integration configuration."""

    @pytest.mark.asyncio
    async def test_create_new_config(self):
        project_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        added = []
        mock_db.add = lambda obj: added.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.controls.instant_pay_service import configure_payment_integration

        await configure_payment_integration(
            mock_db, project_id, {"processor_name": "stripe", "retainage_pct": 5}
        )

        assert len(added) == 1
        assert added[0].processor_name == "stripe"

    @pytest.mark.asyncio
    async def test_update_existing_config(self):
        existing = MagicMock()
        existing.processor_name = "stripe"
        existing.retainage_pct = Decimal("10")

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = existing

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.controls.instant_pay_service import configure_payment_integration

        await configure_payment_integration(mock_db, uuid.uuid4(), {"retainage_pct": 5})

        assert existing.retainage_pct == 5

    @pytest.mark.asyncio
    async def test_create_config_missing_processor_raises(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import configure_payment_integration

        with pytest.raises(ValueError, match="processor_name is required"):
            await configure_payment_integration(mock_db, uuid.uuid4(), {})

    @pytest.mark.asyncio
    async def test_get_payment_config_returns_none(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import _get_payment_config

        result = await _get_payment_config(mock_db, uuid.uuid4())
        assert result is None


# ===========================================================================
# TestPaymentStatus — 4 tests
# ===========================================================================


class TestPaymentStatus:
    """Tests for payment status retrieval."""

    @pytest.mark.asyncio
    async def test_get_status_returns_transactions(self):
        mock_txn = MagicMock()
        mock_txn.id = uuid.uuid4()
        mock_txn.pay_application_id = uuid.uuid4()
        mock_txn.transaction_type = "owner_to_gc"
        mock_txn.amount = Decimal("50000")
        mock_txn.net_amount = Decimal("45000")
        mock_txn.retainage_amount = Decimal("5000")
        mock_txn.currency = "USD"
        mock_txn.status = "paid"
        mock_txn.payment_method = "ach"
        mock_txn.processor_name = "stripe"
        mock_txn.processor_transaction_id = "pi_123"
        mock_txn.submitted_at = datetime(2026, 3, 1, tzinfo=UTC)
        mock_txn.approved_at = datetime(2026, 3, 2, tzinfo=UTC)
        mock_txn.paid_at = datetime(2026, 3, 5, tzinfo=UTC)
        mock_txn.failed_at = None
        mock_txn.failure_reason = None
        mock_txn.created_at = datetime(2026, 3, 1, tzinfo=UTC)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_txn]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import get_payment_status

        result = await get_payment_status(mock_db, uuid.uuid4())

        assert len(result) == 1
        assert result[0]["status"] == "paid"
        assert result[0]["amount"] == 50000.0
        assert result[0]["waterfall"]["total_duration_hours"] is not None

    @pytest.mark.asyncio
    async def test_get_status_empty(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import get_payment_status

        result = await get_payment_status(mock_db, uuid.uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_get_status_filtered_by_pay_app(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.controls.instant_pay_service import get_payment_status

        result = await get_payment_status(mock_db, uuid.uuid4(), pay_application_id=uuid.uuid4())

        # Verify that execute was called (query was built)
        mock_db.execute.assert_called_once()
        assert result == []

    @pytest.mark.asyncio
    async def test_waterfall_timing_no_timestamps(self):
        """Waterfall dict should be empty when no timestamps are set."""
        from app.services.controls.instant_pay_service import _compute_waterfall_timing

        mock_txn = MagicMock()
        mock_txn.submitted_at = None
        mock_txn.approved_at = None
        mock_txn.paid_at = None

        timing = _compute_waterfall_timing(mock_txn)
        assert timing == {}
