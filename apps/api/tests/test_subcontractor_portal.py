"""Comprehensive tests for the subcontractor portal (Feature 2.5).

Covers:
- Profile CRUD (create, get, duplicate prevention)
- Manpower submission (validation, data shape)
- Delivery receipt (validation, document_url)
- Filtered SOV (scope isolation is CRITICAL)
- Sub pay application (sov_item_ids enforcement, negative amounts)
- Payment status (pending, approved, paid, retainage)
- Translated safety briefing (language validation, LLM call)
- Submission listing and filtering
- Submission review (status transitions)
- RBAC enforcement (non-sub denied, sub allowed)
- API endpoint integration (schemas, status codes)
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "true")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _make_profile(
    user_id=None,
    project_id=None,
    sov_item_ids=None,
    status="active",
    trade="electrical",
    company_name="Acme Electric",
):
    """Create a mock SubcontractorProfile."""
    from app.models.subcontractor import SubcontractorProfile

    return SubcontractorProfile(
        id=_uuid(),
        user_id=user_id or _uuid(),
        project_id=project_id or _uuid(),
        company_name=company_name,
        trade=trade,
        sov_item_ids=sov_item_ids or [],
        contact_info={"email": "sub@acme.com"},
        status=status,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def _make_submission(
    profile_id=None,
    submission_type="manpower",
    status="pending",
    data=None,
):
    """Create a mock SubcontractorSubmission."""
    from app.models.subcontractor import SubcontractorSubmission

    return SubcontractorSubmission(
        id=_uuid(),
        profile_id=profile_id or _uuid(),
        submission_type=submission_type,
        submission_date=date.today(),
        data=data or {},
        document_url=None,
        status=status,
        reviewed_by=None,
        review_notes=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def _make_sov_item(item_id=None, project_id=None, item_number="1", description="Foundation"):
    """Create a mock ScheduleOfValues object."""
    from app.models.pay_application import ScheduleOfValues

    return ScheduleOfValues(
        id=item_id or _uuid(),
        project_id=project_id or _uuid(),
        item_number=item_number,
        description=description,
        scheduled_value=Decimal("50000.00"),
        csi_code="03 30 00",
        sort_order=0,
        is_change_order_line=False,
        change_order_id=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# TestProfile
# ---------------------------------------------------------------------------


class TestProfile:
    """Tests for subcontractor profile management."""

    @pytest.mark.asyncio
    async def test_get_profile_returns_none_when_missing(self):
        """get_subcontractor_profile returns None when no profile exists."""
        from app.services.productivity.subcontractor_service import get_subcontractor_profile

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        result = await get_subcontractor_profile(db, _uuid(), _uuid())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_profile_returns_profile(self):
        """get_subcontractor_profile returns existing profile."""
        from app.services.productivity.subcontractor_service import get_subcontractor_profile

        profile = _make_profile()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = profile
        db.execute.return_value = mock_result

        result = await get_subcontractor_profile(db, profile.user_id, profile.project_id)
        assert result is not None
        assert result.company_name == "Acme Electric"

    @pytest.mark.asyncio
    async def test_create_profile_validates_sov_ids(self):
        """create_subcontractor_profile raises ValueError for invalid SOV IDs."""
        from app.services.productivity.subcontractor_service import create_subcontractor_profile

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0  # found 0 of 2 SOV items
        db.execute.return_value = mock_result

        sov_ids = [str(_uuid()), str(_uuid())]
        with pytest.raises(ValueError, match="Some SOV item IDs are invalid"):
            await create_subcontractor_profile(
                db,
                user_id=_uuid(),
                project_id=_uuid(),
                company_name="Acme",
                trade="electrical",
                sov_item_ids=sov_ids,
            )

    @pytest.mark.asyncio
    async def test_create_profile_success_with_empty_sov_ids(self):
        """create_subcontractor_profile succeeds with no SOV items initially."""
        from app.services.productivity.subcontractor_service import create_subcontractor_profile

        db = AsyncMock()

        await create_subcontractor_profile(
            db,
            user_id=_uuid(),
            project_id=_uuid(),
            company_name="Acme Electric",
            trade="electrical",
            sov_item_ids=[],
        )
        # Should have called db.add and db.flush
        assert db.add.called
        assert db.flush.called


# ---------------------------------------------------------------------------
# TestManpower
# ---------------------------------------------------------------------------


class TestManpower:
    """Tests for manpower submission."""

    @pytest.mark.asyncio
    async def test_submit_manpower_validates_profile(self):
        """submit_manpower raises ValueError if profile not found."""
        from app.services.productivity.subcontractor_service import submit_manpower

        db = AsyncMock()
        db.get.return_value = None

        with pytest.raises(ValueError, match="Subcontractor profile not found"):
            await submit_manpower(
                db,
                profile_id=_uuid(),
                date_=date.today(),
                workers_by_trade={"electrician": 3},
                total_hours=24.0,
            )

    @pytest.mark.asyncio
    async def test_submit_manpower_rejects_inactive_profile(self):
        """submit_manpower raises ValueError for inactive profiles."""
        from app.services.productivity.subcontractor_service import submit_manpower

        profile = _make_profile(status="inactive")
        db = AsyncMock()
        db.get.return_value = profile

        with pytest.raises(ValueError, match="not 'active'"):
            await submit_manpower(
                db,
                profile_id=profile.id,
                date_=date.today(),
                workers_by_trade={"electrician": 3},
                total_hours=24.0,
            )

    @pytest.mark.asyncio
    async def test_submit_manpower_stores_data(self):
        """submit_manpower creates submission with correct data shape."""
        from app.services.productivity.subcontractor_service import submit_manpower

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        await submit_manpower(
            db,
            profile_id=profile.id,
            date_=date(2026, 3, 10),
            workers_by_trade={"electrician": 3, "helper": 2},
            total_hours=40.0,
            notes="Panel installation",
        )

        # Verify db.add was called with a SubcontractorSubmission
        assert db.add.called
        submission = db.add.call_args[0][0]
        assert submission.submission_type == "manpower"
        assert submission.data["workers_by_trade"] == {"electrician": 3, "helper": 2}
        assert submission.data["total_hours"] == 40.0
        assert submission.data["total_workers"] == 5
        assert submission.data["notes"] == "Panel installation"

    @pytest.mark.asyncio
    async def test_submit_manpower_sets_correct_date(self):
        """submit_manpower uses the provided date, not today."""
        from app.services.productivity.subcontractor_service import submit_manpower

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        target_date = date(2026, 1, 15)
        await submit_manpower(
            db,
            profile_id=profile.id,
            date_=target_date,
            workers_by_trade={"plumber": 4},
            total_hours=32.0,
        )

        submission = db.add.call_args[0][0]
        assert submission.submission_date == target_date


# ---------------------------------------------------------------------------
# TestDeliveryReceipt
# ---------------------------------------------------------------------------


class TestDeliveryReceipt:
    """Tests for delivery receipt submission."""

    @pytest.mark.asyncio
    async def test_upload_delivery_receipt_stores_data(self):
        """upload_delivery_receipt creates submission with material details."""
        from app.services.productivity.subcontractor_service import upload_delivery_receipt

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        await upload_delivery_receipt(
            db,
            profile_id=profile.id,
            material_description="Ready-mix concrete 4000 PSI",
            quantity=10.0,
            unit="cy",
            supplier="ABC Concrete",
            delivery_date=date(2026, 3, 10),
            document_url="https://s3.example.com/receipts/001.pdf",
        )

        submission = db.add.call_args[0][0]
        assert submission.submission_type == "delivery_receipt"
        assert submission.data["material_description"] == "Ready-mix concrete 4000 PSI"
        assert submission.data["quantity"] == 10.0
        assert submission.data["unit"] == "cy"
        assert submission.data["supplier"] == "ABC Concrete"
        assert submission.document_url == "https://s3.example.com/receipts/001.pdf"

    @pytest.mark.asyncio
    async def test_upload_delivery_receipt_without_document_url(self):
        """upload_delivery_receipt allows None document_url."""
        from app.services.productivity.subcontractor_service import upload_delivery_receipt

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        await upload_delivery_receipt(
            db,
            profile_id=profile.id,
            material_description="#4 rebar",
            quantity=500.0,
            unit="lf",
            supplier="Steel Supply Co",
            delivery_date=date(2026, 3, 11),
        )

        submission = db.add.call_args[0][0]
        assert submission.document_url is None

    @pytest.mark.asyncio
    async def test_upload_delivery_receipt_validates_profile(self):
        """upload_delivery_receipt raises ValueError for missing profile."""
        from app.services.productivity.subcontractor_service import upload_delivery_receipt

        db = AsyncMock()
        db.get.return_value = None

        with pytest.raises(ValueError, match="Subcontractor profile not found"):
            await upload_delivery_receipt(
                db,
                profile_id=_uuid(),
                material_description="Concrete",
                quantity=5.0,
                unit="cy",
                supplier="Supplier",
                delivery_date=date.today(),
            )


# ---------------------------------------------------------------------------
# TestFilteredSOV — CRITICAL scope isolation tests
# ---------------------------------------------------------------------------


class TestFilteredSOV:
    """Tests for filtered SOV scope isolation."""

    @pytest.mark.asyncio
    async def test_filtered_sov_returns_only_assigned_items(self):
        """get_filtered_sov returns only items in profile.sov_item_ids."""
        from app.services.productivity.subcontractor_service import get_filtered_sov

        sov_id_1 = _uuid()
        sov_id_2 = _uuid()
        sov_id_outside = _uuid()

        profile = _make_profile(sov_item_ids=[str(sov_id_1), str(sov_id_2)])
        db = AsyncMock()
        db.get.return_value = profile

        sov1 = _make_sov_item(item_id=sov_id_1, item_number="1", description="Foundation")
        sov2 = _make_sov_item(item_id=sov_id_2, item_number="2", description="Electrical")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sov1, sov2]
        db.execute.return_value = mock_result

        items = await get_filtered_sov(db, profile.project_id, profile.id)

        assert len(items) == 2
        item_ids = {item["id"] for item in items}
        assert str(sov_id_1) in item_ids
        assert str(sov_id_2) in item_ids
        assert str(sov_id_outside) not in item_ids

    @pytest.mark.asyncio
    async def test_filtered_sov_empty_when_no_items_assigned(self):
        """get_filtered_sov returns empty list when sov_item_ids is empty."""
        from app.services.productivity.subcontractor_service import get_filtered_sov

        profile = _make_profile(sov_item_ids=[])
        db = AsyncMock()
        db.get.return_value = profile

        items = await get_filtered_sov(db, profile.project_id, profile.id)
        assert items == []
        # Should NOT hit DB at all if no items assigned
        assert not db.execute.called

    @pytest.mark.asyncio
    async def test_filtered_sov_item_data_shape(self):
        """get_filtered_sov returns correctly shaped dicts."""
        from app.services.productivity.subcontractor_service import get_filtered_sov

        sov_id = _uuid()
        profile = _make_profile(sov_item_ids=[str(sov_id)])
        db = AsyncMock()
        db.get.return_value = profile

        sov = _make_sov_item(item_id=sov_id)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sov]
        db.execute.return_value = mock_result

        items = await get_filtered_sov(db, profile.project_id, profile.id)
        assert len(items) == 1
        item = items[0]
        assert "id" in item
        assert "item_number" in item
        assert "description" in item
        assert "scheduled_value" in item
        assert "csi_code" in item
        assert "sort_order" in item

    @pytest.mark.asyncio
    async def test_filtered_sov_rejects_inactive_profile(self):
        """get_filtered_sov raises ValueError for inactive profiles."""
        from app.services.productivity.subcontractor_service import get_filtered_sov

        profile = _make_profile(status="inactive")
        db = AsyncMock()
        db.get.return_value = profile

        with pytest.raises(ValueError, match="not 'active'"):
            await get_filtered_sov(db, profile.project_id, profile.id)


# ---------------------------------------------------------------------------
# TestSubPayApp — sov_item_ids enforcement
# ---------------------------------------------------------------------------


class TestSubPayApp:
    """Tests for subcontractor pay application submission."""

    @pytest.mark.asyncio
    async def test_submit_pay_app_enforces_scope(self):
        """submit_sub_pay_application rejects items outside sub's scope."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        allowed_id = str(_uuid())
        outside_id = str(_uuid())

        profile = _make_profile(sov_item_ids=[allowed_id])
        db = AsyncMock()
        db.get.return_value = profile

        line_items = [
            {
                "item_id": outside_id,
                "work_completed_this_period": "5000.00",
            }
        ]

        with pytest.raises(ValueError, match="not in subcontractor's scope"):
            await submit_sub_pay_application(
                db,
                profile_id=profile.id,
                line_items=line_items,
                period_to=date(2026, 3, 31),
            )

    @pytest.mark.asyncio
    async def test_submit_pay_app_allows_in_scope(self):
        """submit_sub_pay_application accepts items within scope."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        allowed_id = str(_uuid())
        profile = _make_profile(sov_item_ids=[allowed_id])
        db = AsyncMock()
        db.get.return_value = profile

        line_items = [
            {
                "item_id": allowed_id,
                "work_completed_this_period": "5000.00",
                "materials_presently_stored": "2000.00",
            }
        ]

        await submit_sub_pay_application(
            db,
            profile_id=profile.id,
            line_items=line_items,
            period_to=date(2026, 3, 31),
        )

        submission = db.add.call_args[0][0]
        assert submission.submission_type == "pay_application"
        assert submission.data["total_billed"] == "7000.00"

    @pytest.mark.asyncio
    async def test_submit_pay_app_rejects_negative_amounts(self):
        """submit_sub_pay_application rejects negative work amounts."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        allowed_id = str(_uuid())
        profile = _make_profile(sov_item_ids=[allowed_id])
        db = AsyncMock()
        db.get.return_value = profile

        line_items = [
            {
                "item_id": allowed_id,
                "work_completed_this_period": "-1000.00",
            }
        ]

        with pytest.raises(ValueError, match="cannot be negative"):
            await submit_sub_pay_application(
                db,
                profile_id=profile.id,
                line_items=line_items,
                period_to=date(2026, 3, 31),
            )

    @pytest.mark.asyncio
    async def test_submit_pay_app_rejects_empty_sov_items(self):
        """submit_sub_pay_application raises if sub has no SOV items."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        profile = _make_profile(sov_item_ids=[])
        db = AsyncMock()
        db.get.return_value = profile

        with pytest.raises(ValueError, match="no SOV items assigned"):
            await submit_sub_pay_application(
                db,
                profile_id=profile.id,
                line_items=[{"item_id": str(_uuid()), "work_completed_this_period": "100"}],
                period_to=date(2026, 3, 31),
            )

    @pytest.mark.asyncio
    async def test_submit_pay_app_multi_items_all_valid(self):
        """submit_sub_pay_application accepts multiple in-scope items."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        id1 = str(_uuid())
        id2 = str(_uuid())
        profile = _make_profile(sov_item_ids=[id1, id2])
        db = AsyncMock()
        db.get.return_value = profile

        line_items = [
            {"item_id": id1, "work_completed_this_period": "3000.00"},
            {"item_id": id2, "work_completed_this_period": "4000.00"},
        ]

        await submit_sub_pay_application(
            db,
            profile_id=profile.id,
            line_items=line_items,
            period_to=date(2026, 3, 31),
        )

        submission = db.add.call_args[0][0]
        assert submission.data["total_billed"] == "7000.00"
        assert len(submission.data["line_items"]) == 2

    @pytest.mark.asyncio
    async def test_submit_pay_app_one_in_scope_one_out(self):
        """submit_sub_pay_application rejects if any item is out of scope."""
        from app.services.productivity.subcontractor_service import submit_sub_pay_application

        id_in = str(_uuid())
        id_out = str(_uuid())
        profile = _make_profile(sov_item_ids=[id_in])
        db = AsyncMock()
        db.get.return_value = profile

        line_items = [
            {"item_id": id_in, "work_completed_this_period": "3000.00"},
            {"item_id": id_out, "work_completed_this_period": "4000.00"},
        ]

        with pytest.raises(ValueError, match="not in subcontractor's scope"):
            await submit_sub_pay_application(
                db,
                profile_id=profile.id,
                line_items=line_items,
                period_to=date(2026, 3, 31),
            )


# ---------------------------------------------------------------------------
# TestPaymentStatus
# ---------------------------------------------------------------------------


class TestPaymentStatus:
    """Tests for payment status retrieval."""

    @pytest.mark.asyncio
    async def test_payment_status_empty(self):
        """get_payment_status returns empty list when no submissions."""
        from app.services.productivity.subcontractor_service import get_payment_status

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        entries = await get_payment_status(db, profile.id)
        assert entries == []

    @pytest.mark.asyncio
    async def test_payment_status_pending_amounts(self):
        """Pending submissions show submitted_amount but zero approved/paid."""
        from app.services.productivity.subcontractor_service import get_payment_status

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        submission = _make_submission(
            profile_id=profile.id,
            submission_type="pay_application",
            status="pending",
            data={"total_billed": "15000.00"},
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [submission]
        db.execute.return_value = mock_result

        entries = await get_payment_status(db, profile.id)
        assert len(entries) == 1
        assert entries[0].submitted_amount == Decimal("15000.00")
        assert entries[0].approved_amount == Decimal("0")
        assert entries[0].paid_amount == Decimal("0")
        assert entries[0].status == "pending"

    @pytest.mark.asyncio
    async def test_payment_status_approved_with_retainage(self):
        """Approved submissions show approved_amount and retainage."""
        from app.services.productivity.subcontractor_service import get_payment_status

        profile = _make_profile()
        db = AsyncMock()
        db.get.return_value = profile

        submission = _make_submission(
            profile_id=profile.id,
            submission_type="pay_application",
            status="approved",
            data={"total_billed": "10000.00"},
        )

        # Mock DB: first call returns submissions, second call returns parent pay app
        mock_submissions_result = MagicMock()
        mock_submissions_result.scalars.return_value.all.return_value = [submission]

        # No parent pay app found
        mock_payapp_result = MagicMock()
        mock_payapp_result.scalars.return_value.first.return_value = None

        db.execute.side_effect = [mock_submissions_result, mock_payapp_result]

        entries = await get_payment_status(db, profile.id)
        assert len(entries) == 1
        assert entries[0].approved_amount == Decimal("10000.00")
        assert entries[0].status == "approved"


# ---------------------------------------------------------------------------
# TestSafetyTranslation
# ---------------------------------------------------------------------------


class TestSafetyTranslation:
    """Tests for translated safety briefing."""

    @pytest.mark.asyncio
    async def test_translation_calls_service(self):
        """get_translated_safety_briefing calls translation service with safety_alert context."""
        from app.services.productivity.subcontractor_service import (
            get_translated_safety_briefing,
        )

        mock_result = MagicMock()
        mock_result.translated_text = "ADVERTENCIA: Usar casco siempre."

        with patch(
            "app.services.communication.translation_service.get_translation_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.translate.return_value = mock_result
            mock_get_service.return_value = mock_service

            result = await get_translated_safety_briefing("WARNING: Always wear hard hat.", "es")

            assert result == "ADVERTENCIA: Usar casco siempre."
            mock_service.translate.assert_called_once_with(
                text="WARNING: Always wear hard hat.",
                target_lang="es",
                context="safety_alert",
            )

    @pytest.mark.asyncio
    async def test_translation_unsupported_language_raises(self):
        """get_translated_safety_briefing propagates ValueError for unsupported langs."""
        from app.services.productivity.subcontractor_service import (
            get_translated_safety_briefing,
        )

        with patch(
            "app.services.communication.translation_service.get_translation_service"
        ) as mock_get_service:
            mock_service = AsyncMock()
            mock_service.translate.side_effect = ValueError("Unsupported target language 'xx'")
            mock_get_service.return_value = mock_service

            with pytest.raises(ValueError, match="Unsupported target language"):
                await get_translated_safety_briefing("Warning text", "xx")


# ---------------------------------------------------------------------------
# TestSubmissionList
# ---------------------------------------------------------------------------


class TestSubmissionList:
    """Tests for listing submissions."""

    @pytest.mark.asyncio
    async def test_list_submissions_returns_tuple(self):
        """list_submissions returns (items, total_count)."""
        from app.services.productivity.subcontractor_service import list_submissions

        db = AsyncMock()

        # Count query
        mock_count = MagicMock()
        mock_count.scalar_one.return_value = 2

        # Data query
        sub1 = _make_submission()
        sub2 = _make_submission()
        mock_data = MagicMock()
        mock_data.scalars.return_value.all.return_value = [sub1, sub2]

        db.execute.side_effect = [mock_count, mock_data]

        items, total = await list_submissions(db, profile_id=_uuid())
        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_list_submissions_filters_by_type(self):
        """list_submissions filters by submission_type when provided."""
        from app.services.productivity.subcontractor_service import list_submissions

        db = AsyncMock()

        mock_count = MagicMock()
        mock_count.scalar_one.return_value = 1

        sub = _make_submission(submission_type="delivery_receipt")
        mock_data = MagicMock()
        mock_data.scalars.return_value.all.return_value = [sub]

        db.execute.side_effect = [mock_count, mock_data]

        _items, total = await list_submissions(
            db, profile_id=_uuid(), submission_type="delivery_receipt"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_submissions_rejects_invalid_type(self):
        """list_submissions raises ValueError for invalid submission_type."""
        from app.services.productivity.subcontractor_service import list_submissions

        db = AsyncMock()

        with pytest.raises(ValueError, match="Invalid submission_type"):
            await list_submissions(db, profile_id=_uuid(), submission_type="invalid")


# ---------------------------------------------------------------------------
# TestReviewSubmission
# ---------------------------------------------------------------------------


class TestReviewSubmission:
    """Tests for submission review workflow."""

    @pytest.mark.asyncio
    async def test_review_submission_approve(self):
        """review_submission transitions pending -> approved."""
        from app.services.productivity.subcontractor_service import review_submission

        submission = _make_submission(status="pending")
        db = AsyncMock()
        db.get.return_value = submission

        reviewer_id = _uuid()
        await review_submission(
            db,
            submission_id=submission.id,
            reviewed_by=reviewer_id,
            status="approved",
            notes="Looks good",
        )

        assert submission.status == "approved"
        assert submission.reviewed_by == reviewer_id
        assert submission.review_notes == "Looks good"

    @pytest.mark.asyncio
    async def test_review_submission_reject(self):
        """review_submission transitions pending -> rejected."""
        from app.services.productivity.subcontractor_service import review_submission

        submission = _make_submission(status="pending")
        db = AsyncMock()
        db.get.return_value = submission

        await review_submission(
            db,
            submission_id=submission.id,
            reviewed_by=_uuid(),
            status="rejected",
            notes="Missing receipts",
        )

        assert submission.status == "rejected"

    @pytest.mark.asyncio
    async def test_review_submission_not_found(self):
        """review_submission raises ValueError when submission not found."""
        from app.services.productivity.subcontractor_service import review_submission

        db = AsyncMock()
        db.get.return_value = None

        with pytest.raises(ValueError, match="Submission not found"):
            await review_submission(
                db,
                submission_id=_uuid(),
                reviewed_by=_uuid(),
                status="approved",
            )

    @pytest.mark.asyncio
    async def test_review_submission_already_approved(self):
        """review_submission raises ValueError for already approved submissions."""
        from app.services.productivity.subcontractor_service import review_submission

        submission = _make_submission(status="approved")
        db = AsyncMock()
        db.get.return_value = submission

        with pytest.raises(ValueError, match="Cannot review"):
            await review_submission(
                db,
                submission_id=submission.id,
                reviewed_by=_uuid(),
                status="rejected",
            )

    @pytest.mark.asyncio
    async def test_review_submission_invalid_status(self):
        """review_submission raises ValueError for invalid target status."""
        from app.services.productivity.subcontractor_service import review_submission

        db = AsyncMock()

        with pytest.raises(ValueError, match="Invalid status"):
            await review_submission(
                db,
                submission_id=_uuid(),
                reviewed_by=_uuid(),
                status="invalid_status",
            )


# ---------------------------------------------------------------------------
# TestRBACEnforcement
# ---------------------------------------------------------------------------


class TestRBACEnforcement:
    """Tests for RBAC sub_portal permission enforcement."""

    def test_subcontractor_has_sub_portal_read(self):
        """SUBCONTRACTOR role has sub_portal:read permission."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("subcontractor", "sub_portal:read") is True

    def test_subcontractor_has_sub_portal_create(self):
        """SUBCONTRACTOR role has sub_portal:create permission."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("subcontractor", "sub_portal:create") is True

    def test_subcontractor_cannot_approve(self):
        """SUBCONTRACTOR role does NOT have sub_portal:approve."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("subcontractor", "sub_portal:approve") is False

    def test_project_admin_has_sub_portal_all(self):
        """PROJECT_ADMIN has sub_portal:* (all operations)."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("project_admin", "sub_portal:read") is True
        assert enforcer.check_permission("project_admin", "sub_portal:create") is True
        assert enforcer.check_permission("project_admin", "sub_portal:approve") is True

    def test_project_manager_can_approve(self):
        """PROJECT_MANAGER has sub_portal:approve."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("project_manager", "sub_portal:approve") is True

    def test_readonly_cannot_access_sub_portal(self):
        """READONLY role cannot access sub_portal at all."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("readonly", "sub_portal:read") is False
        assert enforcer.check_permission("readonly", "sub_portal:create") is False

    def test_owner_rep_cannot_access_sub_portal(self):
        """OWNER_REP role does not have sub_portal permissions."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("owner_rep", "sub_portal:read") is False

    def test_org_admin_has_global_wildcard(self):
        """ORG_ADMIN has global wildcard, grants everything including sub_portal."""
        from app.services.security.rbac import RBACEnforcer

        enforcer = RBACEnforcer()
        assert enforcer.check_permission("org_admin", "sub_portal:read") is True
        assert enforcer.check_permission("org_admin", "sub_portal:approve") is True


# ---------------------------------------------------------------------------
# TestSchemaValidation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Tests for Pydantic schema validation."""

    def test_manpower_request_requires_workers(self):
        """ManpowerSubmissionRequest rejects empty workers_by_trade."""
        from app.schemas.subcontractor import ManpowerSubmissionRequest

        with pytest.raises(Exception):  # ValidationError
            ManpowerSubmissionRequest(
                date=date.today(),
                workers_by_trade={},
                total_hours=8.0,
            )

    def test_manpower_request_rejects_zero_hours(self):
        """ManpowerSubmissionRequest rejects total_hours <= 0."""
        from app.schemas.subcontractor import ManpowerSubmissionRequest

        with pytest.raises(Exception):  # ValidationError
            ManpowerSubmissionRequest(
                date=date.today(),
                workers_by_trade={"electrician": 1},
                total_hours=0,
            )

    def test_manpower_request_valid(self):
        """ManpowerSubmissionRequest accepts valid input."""
        from app.schemas.subcontractor import ManpowerSubmissionRequest

        req = ManpowerSubmissionRequest(
            date=date.today(),
            workers_by_trade={"electrician": 3, "helper": 2},
            total_hours=40.0,
        )
        assert req.total_hours == 40.0
        assert req.workers_by_trade["electrician"] == 3

    def test_delivery_receipt_request_valid(self):
        """DeliveryReceiptRequest accepts valid input."""
        from app.schemas.subcontractor import DeliveryReceiptRequest

        req = DeliveryReceiptRequest(
            material_description="Concrete 4000 PSI",
            quantity=10.0,
            unit="cy",
            supplier="ABC Concrete",
            delivery_date=date.today(),
        )
        assert req.quantity == 10.0

    def test_sub_pay_app_request_requires_items(self):
        """SubPayApplicationRequest requires at least one line item."""
        from app.schemas.subcontractor import SubPayApplicationRequest

        with pytest.raises(Exception):  # ValidationError
            SubPayApplicationRequest(
                line_items=[],
                period_to=date.today(),
            )

    def test_review_request_validates_status(self):
        """ReviewSubmissionRequest rejects invalid status values."""
        from app.schemas.subcontractor import ReviewSubmissionRequest

        with pytest.raises(Exception):  # ValidationError
            ReviewSubmissionRequest(status="invalid_status")

    def test_review_request_valid(self):
        """ReviewSubmissionRequest accepts valid status."""
        from app.schemas.subcontractor import ReviewSubmissionRequest

        req = ReviewSubmissionRequest(status="approved", notes="Looks good")
        assert req.status == "approved"

    def test_translated_briefing_request_validates_lang_length(self):
        """TranslatedBriefingRequest requires 2-char language code."""
        from app.schemas.subcontractor import TranslatedBriefingRequest

        with pytest.raises(Exception):  # ValidationError
            TranslatedBriefingRequest(
                briefing_text="Safety alert",
                target_language="english",  # too long
            )


# ---------------------------------------------------------------------------
# TestEndpoints (route wiring / import checks)
# ---------------------------------------------------------------------------


class TestEndpoints:
    """Tests that API endpoints are properly wired."""

    def test_router_has_profile_endpoint(self):
        """Subcontractor portal router has profile GET endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/profile" in routes

    def test_router_has_manpower_endpoint(self):
        """Subcontractor portal router has manpower POST endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/manpower" in routes

    def test_router_has_deliveries_endpoint(self):
        """Subcontractor portal router has deliveries POST endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/deliveries" in routes

    def test_router_has_scope_endpoint(self):
        """Subcontractor portal router has scope GET endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/scope" in routes

    def test_router_has_pay_app_endpoint(self):
        """Subcontractor portal router has pay-app POST endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/pay-app" in routes

    def test_router_has_payment_status_endpoint(self):
        """Subcontractor portal router has payment-status GET endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/payment-status" in routes

    def test_router_has_safety_briefing_endpoint(self):
        """Subcontractor portal router has safety-briefing POST endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/safety-briefing" in routes

    def test_router_has_submissions_endpoint(self):
        """Subcontractor portal router has submissions GET endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/submissions" in routes

    def test_router_has_review_endpoint(self):
        """Subcontractor portal router has review POST endpoint."""
        from app.api.v1.subcontractor_portal import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/sub-portal/submissions/{submission_id}/review" in routes

    def test_router_total_endpoint_count(self):
        """Subcontractor portal should have 10 routes (profile GET + POST,
        manpower, deliveries, scope, pay-app, payment-status,
        safety-briefing, submissions, review)."""
        from app.api.v1.subcontractor_portal import router

        # Filter out non-route entries (e.g. APIRoute vs Mount)
        actual_routes = [r for r in router.routes if hasattr(r, "methods")]
        assert len(actual_routes) == 10


# ---------------------------------------------------------------------------
# TestModelDefinitions
# ---------------------------------------------------------------------------


class TestModelDefinitions:
    """Tests for model class definitions and constraints."""

    def test_subcontractor_profile_tablename(self):
        """SubcontractorProfile uses correct tablename."""
        from app.models.subcontractor import SubcontractorProfile

        assert SubcontractorProfile.__tablename__ == "subcontractor_profiles"

    def test_subcontractor_submission_tablename(self):
        """SubcontractorSubmission uses correct tablename."""
        from app.models.subcontractor import SubcontractorSubmission

        assert SubcontractorSubmission.__tablename__ == "subcontractor_submissions"

    def test_profile_has_unique_constraint(self):
        """SubcontractorProfile has unique constraint on (user_id, project_id)."""
        from app.models.subcontractor import SubcontractorProfile

        constraints = SubcontractorProfile.__table_args__
        assert any(
            hasattr(c, "name") and c.name == "uq_sub_user_project"
            for c in (constraints if isinstance(constraints, tuple) else [constraints])
        )

    def test_profile_sov_item_ids_default(self):
        """SubcontractorProfile.sov_item_ids defaults to empty list."""
        from app.models.subcontractor import SubcontractorProfile

        col = SubcontractorProfile.__table__.columns["sov_item_ids"]
        assert col.server_default is not None

    def test_submission_status_default_pending(self):
        """SubcontractorSubmission.status defaults to 'pending'."""
        from app.models.subcontractor import SubcontractorSubmission

        col = SubcontractorSubmission.__table__.columns["status"]
        assert "pending" in str(col.server_default.arg)


# ---------------------------------------------------------------------------
# TestMigration029
# ---------------------------------------------------------------------------


class TestMigration029:
    """Tests for migration 029 structure."""

    def _load_migration(self):
        import importlib.util
        import os

        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "029_phase2_features.py",
        )
        spec = importlib.util.spec_from_file_location("m029", os.path.abspath(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_migration_revision_id(self):
        """Migration 029 has correct revision and down_revision."""
        m029 = self._load_migration()
        assert m029.revision == "029"
        assert m029.down_revision == "028"

    def test_migration_has_upgrade_and_downgrade(self):
        """Migration 029 defines both upgrade() and downgrade()."""
        m029 = self._load_migration()
        assert callable(getattr(m029, "upgrade", None))
        assert callable(getattr(m029, "downgrade", None))
