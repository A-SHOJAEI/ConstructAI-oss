"""Tests for submittal workflow system.

Covers:
- Submittal number generation
- Status transition validation
- Overdue detection (date_required based)
- Ball-in-court tracking
- Submittal creation
- Review actions and chain advancement
- Full review chain lifecycle (GC -> Architect)
- Resubmission / revision tracking
- CSV export
- Stats aggregation
- Register view (spec_section x status matrix)
- Procore compatibility (protected fields)
- Pydantic schema validation
- API endpoints (mocked service/DB)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_submittal(**overrides):
    """Build a mock Submittal model object with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "submittal_number": "SUB-001",
        "title": "Test Submittal",
        "description": None,
        "spec_section": "03 30 00",
        "spec_section_name": "Cast-in-Place Concrete",
        "submittal_type": "shop_drawing",
        "status": "not_submitted",
        "priority": "normal",
        "revision_number": 0,
        "submitted_by": uuid.uuid4(),
        "reviewer_id": uuid.uuid4(),
        "current_reviewer": None,
        "ball_in_court": None,
        "document_urls": [],
        "review_comments": [],
        "due_date": None,
        "date_required": None,
        "date_submitted": None,
        "date_returned": None,
        "submitted_at": None,
        "reviewed_at": None,
        "lead_time_days": None,
        "distribution_list": [],
        "linked_rfi_ids": [],
        "review_chain": [],
        "data_source": "manual",
        "procore_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. Submittal Number Generation
# ---------------------------------------------------------------------------


class TestSubmittalNumberGeneration:
    """Tests for generate_submittal_number."""

    @pytest.mark.asyncio
    async def test_first_submittal_gets_001(self):
        from app.services.communication.submittal_service import generate_submittal_number

        db = AsyncMock()
        # No existing submittals matching pattern
        scalar_mock = MagicMock()
        scalar_mock.scalar.return_value = None
        db.execute.side_effect = [scalar_mock, MagicMock(scalar=MagicMock(return_value=0))]

        result = await generate_submittal_number(db, uuid.uuid4())
        assert result == "SUB-001"

    @pytest.mark.asyncio
    async def test_sequential_numbering(self):
        from app.services.communication.submittal_service import generate_submittal_number

        db = AsyncMock()
        scalar_mock = MagicMock()
        scalar_mock.scalar.return_value = 5
        db.execute.return_value = scalar_mock

        result = await generate_submittal_number(db, uuid.uuid4())
        assert result == "SUB-006"

    @pytest.mark.asyncio
    async def test_mixed_with_procore_numbers(self):
        from app.services.communication.submittal_service import generate_submittal_number

        db = AsyncMock()
        scalar_mock = MagicMock()
        scalar_mock.scalar.return_value = 12
        db.execute.return_value = scalar_mock

        result = await generate_submittal_number(db, uuid.uuid4())
        assert result == "SUB-013"


# ---------------------------------------------------------------------------
# 2. Status Transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Tests for VALID_TRANSITIONS enforcement."""

    def test_valid_transitions_defined(self):
        from app.services.communication.submittal_service import VALID_TRANSITIONS

        assert "not_submitted" in VALID_TRANSITIONS
        assert "pending_review" in VALID_TRANSITIONS
        assert "closed" in VALID_TRANSITIONS

    def test_closed_is_terminal(self):
        from app.services.communication.submittal_service import VALID_TRANSITIONS

        assert VALID_TRANSITIONS["closed"] == set()

    def test_not_submitted_can_only_go_to_pending_review(self):
        from app.services.communication.submittal_service import VALID_TRANSITIONS

        assert VALID_TRANSITIONS["not_submitted"] == {"pending_review"}

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self):
        from app.services.communication.submittal_service import update_submittal

        submittal = _make_submittal(status="not_submitted")
        db = AsyncMock()
        db.get.return_value = submittal

        with pytest.raises(ValueError, match="Cannot transition"):
            await update_submittal(
                db, submittal.id, submittal.project_id, {"status": "approved"}, uuid.uuid4()
            )

    @pytest.mark.asyncio
    async def test_valid_transition_succeeds(self):
        from app.services.communication.submittal_service import update_submittal

        submittal = _make_submittal(status="not_submitted")
        db = AsyncMock()
        db.get.return_value = submittal

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            result = await update_submittal(
                db, submittal.id, submittal.project_id, {"status": "pending_review"}, uuid.uuid4()
            )
        assert result.status == "pending_review"

    @pytest.mark.asyncio
    async def test_closed_cannot_transition(self):
        from app.services.communication.submittal_service import update_submittal

        submittal = _make_submittal(status="closed")
        db = AsyncMock()
        db.get.return_value = submittal

        with pytest.raises(ValueError, match="Cannot transition"):
            await update_submittal(
                db, submittal.id, submittal.project_id, {"status": "pending_review"}, uuid.uuid4()
            )


# ---------------------------------------------------------------------------
# 3. Overdue Detection
# ---------------------------------------------------------------------------


class TestOverdueDetection:
    """Tests for check_overdue based on date_required."""

    def test_pending_review_past_date_required_is_overdue(self):
        from app.services.communication.submittal_service import check_overdue

        sub = _make_submittal(
            status="pending_review",
            date_required=date.today() - timedelta(days=5),
        )
        assert check_overdue(sub) is True

    def test_pending_review_future_date_required_not_overdue(self):
        from app.services.communication.submittal_service import check_overdue

        sub = _make_submittal(
            status="pending_review",
            date_required=date.today() + timedelta(days=5),
        )
        assert check_overdue(sub) is False

    def test_not_submitted_never_overdue(self):
        from app.services.communication.submittal_service import check_overdue

        sub = _make_submittal(
            status="not_submitted",
            date_required=date.today() - timedelta(days=30),
        )
        assert check_overdue(sub) is False

    def test_closed_never_overdue(self):
        from app.services.communication.submittal_service import check_overdue

        sub = _make_submittal(
            status="closed",
            date_required=date.today() - timedelta(days=30),
        )
        assert check_overdue(sub) is False

    def test_no_date_required_not_overdue(self):
        from app.services.communication.submittal_service import check_overdue

        sub = _make_submittal(status="pending_review", date_required=None)
        assert check_overdue(sub) is False


# ---------------------------------------------------------------------------
# 4. Ball In Court
# ---------------------------------------------------------------------------


class TestBallInCourt:
    """Tests for ball-in-court tracking."""

    @pytest.mark.asyncio
    async def test_create_sets_ball_in_court_to_submitted_by(self):
        from app.services.communication.submittal_service import create_submittal

        db = AsyncMock()
        captured = []
        db.add = MagicMock(side_effect=lambda obj: captured.append(obj))

        user_id = uuid.uuid4()

        with (
            patch(
                "app.services.communication.submittal_service.generate_submittal_number",
                new_callable=AsyncMock,
                return_value="SUB-001",
            ),
            patch(
                "app.services.communication.submittal_service._publish_submittal_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_submittal(db, uuid.uuid4(), {"title": "Test"}, user_id)

        assert len(captured) == 1
        assert captured[0].ball_in_court == user_id

    @pytest.mark.asyncio
    async def test_review_advances_ball_in_court(self):
        from app.services.communication.submittal_service import review_submittal

        gc_pm = uuid.uuid4()
        architect = uuid.uuid4()
        submitter = uuid.uuid4()

        chain = [
            {"user_id": str(gc_pm), "role": "gc_pm"},
            {"user_id": str(architect), "role": "architect"},
        ]

        sub = _make_submittal(
            status="pending_review",
            review_chain=chain,
            current_reviewer=gc_pm,
            ball_in_court=gc_pm,
            submitted_by=submitter,
        )

        db = AsyncMock()
        db.get.return_value = sub
        db.add = MagicMock()

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await review_submittal(db, sub.id, sub.project_id, gc_pm, "approved", "LGTM")

        assert sub.ball_in_court == architect
        assert sub.current_reviewer == architect

    @pytest.mark.asyncio
    async def test_rejection_returns_ball_to_submitter(self):
        from app.services.communication.submittal_service import review_submittal

        gc_pm = uuid.uuid4()
        submitter = uuid.uuid4()

        sub = _make_submittal(
            status="pending_review",
            review_chain=[{"user_id": str(gc_pm), "role": "gc_pm"}],
            current_reviewer=gc_pm,
            ball_in_court=gc_pm,
            submitted_by=submitter,
        )

        db = AsyncMock()
        db.get.return_value = sub
        db.add = MagicMock()

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await review_submittal(db, sub.id, sub.project_id, gc_pm, "rejected", "Fix drawings")

        assert sub.ball_in_court == submitter
        assert sub.status == "rejected"


# ---------------------------------------------------------------------------
# 5. Submittal Creation
# ---------------------------------------------------------------------------


class TestSubmittalCreation:
    """Tests for create_submittal."""

    @pytest.mark.asyncio
    async def test_auto_number_assigned(self):
        from app.services.communication.submittal_service import create_submittal

        db = AsyncMock()
        captured = []
        db.add = MagicMock(side_effect=lambda obj: captured.append(obj))

        with (
            patch(
                "app.services.communication.submittal_service.generate_submittal_number",
                new_callable=AsyncMock,
                return_value="SUB-007",
            ),
            patch(
                "app.services.communication.submittal_service._publish_submittal_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_submittal(db, uuid.uuid4(), {"title": "Shop Drawings"}, uuid.uuid4())

        assert captured[0].submittal_number == "SUB-007"

    @pytest.mark.asyncio
    async def test_defaults_applied(self):
        from app.services.communication.submittal_service import create_submittal

        db = AsyncMock()
        captured = []
        db.add = MagicMock(side_effect=lambda obj: captured.append(obj))

        with (
            patch(
                "app.services.communication.submittal_service.generate_submittal_number",
                new_callable=AsyncMock,
                return_value="SUB-001",
            ),
            patch(
                "app.services.communication.submittal_service._publish_submittal_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_submittal(db, uuid.uuid4(), {"title": "Test"}, uuid.uuid4())

        sub = captured[0]
        assert sub.status == "not_submitted"
        assert sub.submittal_type == "shop_drawing"
        assert sub.priority == "normal"
        assert sub.revision_number == 0

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        from app.services.communication.submittal_service import create_submittal

        db = AsyncMock()

        with (
            patch(
                "app.services.communication.submittal_service.generate_submittal_number",
                new_callable=AsyncMock,
                return_value="SUB-001",
            ),
            pytest.raises(ValueError, match="Invalid submittal type"),
        ):
            await create_submittal(
                db, uuid.uuid4(), {"title": "Test", "submittal_type": "invalid_type"}, uuid.uuid4()
            )

    @pytest.mark.asyncio
    async def test_invalid_priority_raises(self):
        from app.services.communication.submittal_service import create_submittal

        db = AsyncMock()

        with (
            patch(
                "app.services.communication.submittal_service.generate_submittal_number",
                new_callable=AsyncMock,
                return_value="SUB-001",
            ),
            pytest.raises(ValueError, match="Invalid priority"),
        ):
            await create_submittal(
                db, uuid.uuid4(), {"title": "Test", "priority": "mega_urgent"}, uuid.uuid4()
            )


# ---------------------------------------------------------------------------
# 6. Review Actions
# ---------------------------------------------------------------------------


class TestSubmittalReview:
    """Tests for review_submittal."""

    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        from app.services.communication.submittal_service import review_submittal

        sub = _make_submittal(status="pending_review")
        db = AsyncMock()
        db.get.return_value = sub

        with pytest.raises(ValueError, match="Invalid review action"):
            await review_submittal(db, sub.id, sub.project_id, uuid.uuid4(), "invalid_action")

    @pytest.mark.asyncio
    async def test_cannot_review_not_submitted(self):
        from app.services.communication.submittal_service import review_submittal

        sub = _make_submittal(status="not_submitted")
        db = AsyncMock()
        db.get.return_value = sub

        with pytest.raises(ValueError, match="Cannot review"):
            await review_submittal(db, sub.id, sub.project_id, uuid.uuid4(), "approved")

    @pytest.mark.asyncio
    async def test_review_creates_record(self):
        from app.services.communication.submittal_service import review_submittal

        reviewer = uuid.uuid4()
        sub = _make_submittal(
            status="pending_review",
            review_chain=[],
            submitted_by=uuid.uuid4(),
        )

        db = AsyncMock()
        db.get.return_value = sub
        captured = []
        db.add = MagicMock(side_effect=lambda obj: captured.append(obj))

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await review_submittal(db, sub.id, sub.project_id, reviewer, "approved", "Looks good")

        assert len(captured) == 1
        review = captured[0]
        assert review.review_action == "approved"
        assert review.reviewer_id == reviewer
        assert review.comments == "Looks good"

    @pytest.mark.asyncio
    async def test_approved_sets_final_status(self):
        from app.services.communication.submittal_service import review_submittal

        reviewer = uuid.uuid4()
        sub = _make_submittal(
            status="pending_review",
            review_chain=[],
            submitted_by=uuid.uuid4(),
        )

        db = AsyncMock()
        db.get.return_value = sub
        db.add = MagicMock()

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await review_submittal(db, sub.id, sub.project_id, reviewer, "approved")

        assert sub.status == "approved"


# ---------------------------------------------------------------------------
# 7. Review Chain Lifecycle
# ---------------------------------------------------------------------------


class TestReviewChainLifecycle:
    """Full lifecycle: create -> submit -> review chain -> approve."""

    @pytest.mark.asyncio
    async def test_full_chain_gc_then_architect(self):
        from app.services.communication.submittal_service import review_submittal

        gc_pm = uuid.uuid4()
        architect = uuid.uuid4()
        submitter = uuid.uuid4()

        chain = [
            {"user_id": str(gc_pm), "role": "gc_pm"},
            {"user_id": str(architect), "role": "architect"},
        ]

        sub = _make_submittal(
            status="pending_review",
            review_chain=chain,
            current_reviewer=gc_pm,
            ball_in_court=gc_pm,
            submitted_by=submitter,
        )

        db = AsyncMock()
        db.get.return_value = sub
        db.add = MagicMock()

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            # GC PM approves — should advance to architect
            await review_submittal(db, sub.id, sub.project_id, gc_pm, "approved", "Looks good")

        assert sub.ball_in_court == architect
        assert sub.current_reviewer == architect
        assert sub.status == "pending_review"

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            # Architect approves — chain exhausted, final status
            await review_submittal(
                db, sub.id, sub.project_id, architect, "approved", "Approved by architect"
            )

        assert sub.status == "approved"
        assert sub.ball_in_court == submitter

    @pytest.mark.asyncio
    async def test_rejection_bypasses_chain(self):
        from app.services.communication.submittal_service import review_submittal

        gc_pm = uuid.uuid4()
        architect = uuid.uuid4()
        submitter = uuid.uuid4()

        chain = [
            {"user_id": str(gc_pm), "role": "gc_pm"},
            {"user_id": str(architect), "role": "architect"},
        ]

        sub = _make_submittal(
            status="pending_review",
            review_chain=chain,
            current_reviewer=gc_pm,
            ball_in_court=gc_pm,
            submitted_by=submitter,
        )

        db = AsyncMock()
        db.get.return_value = sub
        db.add = MagicMock()

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await review_submittal(
                db, sub.id, sub.project_id, gc_pm, "rejected", "Drawings incorrect"
            )

        # Should NOT advance to architect — rejected immediately
        assert sub.status == "rejected"
        assert sub.ball_in_court == submitter
        assert sub.current_reviewer is None

    def test_find_next_reviewer_in_chain(self):
        from app.services.communication.submittal_service import _find_next_reviewer_in_chain

        gc_pm = uuid.uuid4()
        architect = uuid.uuid4()

        chain = [
            {"user_id": str(gc_pm), "role": "gc_pm"},
            {"user_id": str(architect), "role": "architect"},
        ]

        assert _find_next_reviewer_in_chain(chain, gc_pm) == architect
        assert _find_next_reviewer_in_chain(chain, architect) is None
        assert _find_next_reviewer_in_chain(chain, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# 8. Resubmission
# ---------------------------------------------------------------------------


class TestResubmission:
    """Tests for resubmit_submittal."""

    @pytest.mark.asyncio
    async def test_revision_increments(self):
        from app.services.communication.submittal_service import resubmit_submittal

        sub = _make_submittal(
            status="revise_and_resubmit",
            revision_number=1,
            submitted_by=uuid.uuid4(),
        )

        db = AsyncMock()
        db.get.return_value = sub

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await resubmit_submittal(db, sub.id, sub.project_id, uuid.uuid4(), "Fixed drawings")

        assert sub.revision_number == 2
        assert sub.status == "pending_review"

    @pytest.mark.asyncio
    async def test_status_resets_to_pending_review(self):
        from app.services.communication.submittal_service import resubmit_submittal

        sub = _make_submittal(
            status="rejected",
            revision_number=0,
            submitted_by=uuid.uuid4(),
        )

        db = AsyncMock()
        db.get.return_value = sub

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await resubmit_submittal(db, sub.id, sub.project_id, uuid.uuid4())

        assert sub.status == "pending_review"
        assert sub.date_returned is None
        assert sub.reviewed_at is None

    @pytest.mark.asyncio
    async def test_resubmit_resets_to_first_reviewer(self):
        from app.services.communication.submittal_service import resubmit_submittal

        gc_pm = uuid.uuid4()
        architect = uuid.uuid4()
        submitter = uuid.uuid4()

        chain = [
            {"user_id": str(gc_pm), "role": "gc_pm"},
            {"user_id": str(architect), "role": "architect"},
        ]

        sub = _make_submittal(
            status="revise_and_resubmit",
            revision_number=0,
            review_chain=chain,
            current_reviewer=None,
            submitted_by=submitter,
        )

        db = AsyncMock()
        db.get.return_value = sub

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await resubmit_submittal(db, sub.id, sub.project_id, submitter)

        assert sub.current_reviewer == gc_pm
        assert sub.ball_in_court == gc_pm


# ---------------------------------------------------------------------------
# 9. CSV Export
# ---------------------------------------------------------------------------


class TestCSVExport:
    """Tests for export_submittals_csv."""

    @pytest.mark.asyncio
    async def test_export_produces_valid_csv(self):
        from app.services.communication.submittal_service import export_submittals_csv

        sub = _make_submittal(
            submittal_number="SUB-001",
            title="Shop Drawings",
            submittal_type="shop_drawing",
            status="approved",
            priority="normal",
        )

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sub]
        db.execute.return_value = mock_result

        csv_bytes = await export_submittals_csv(db, sub.project_id)

        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row
        assert rows[0][0] == "Submittal Number"
        assert rows[1][0] == "SUB-001"

    @pytest.mark.asyncio
    async def test_empty_project_returns_headers_only(self):
        from app.services.communication.submittal_service import export_submittals_csv

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        csv_bytes = await export_submittals_csv(db, uuid.uuid4())

        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0][0] == "Submittal Number"


# ---------------------------------------------------------------------------
# 10. Stats
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for get_submittal_stats."""

    @pytest.mark.asyncio
    async def test_status_counts(self):
        from app.services.communication.submittal_service import get_submittal_stats

        db = AsyncMock()

        # Status counts query
        status_result = MagicMock()
        status_result.all.return_value = [
            ("not_submitted", 3),
            ("pending_review", 2),
            ("approved", 1),
        ]

        # Avg days query
        avg_result = MagicMock()
        avg_result.scalar.return_value = 5.5

        # Overdue query (open submittals)
        overdue_result = MagicMock()
        overdue_result.scalars.return_value.all.return_value = []

        db.execute.side_effect = [status_result, avg_result, overdue_result]

        stats = await get_submittal_stats(db, uuid.uuid4())
        assert stats["total"] == 6
        assert stats["not_submitted"] == 3
        assert stats["pending_review"] == 2
        assert stats["approved"] == 1
        assert stats["avg_review_days"] == 5.5

    @pytest.mark.asyncio
    async def test_overdue_count(self):
        from app.services.communication.submittal_service import get_submittal_stats

        overdue_sub = _make_submittal(
            status="pending_review",
            date_required=date.today() - timedelta(days=10),
        )

        db = AsyncMock()

        status_result = MagicMock()
        status_result.all.return_value = [("pending_review", 2)]

        avg_result = MagicMock()
        avg_result.scalar.return_value = None

        overdue_result = MagicMock()
        overdue_result.scalars.return_value.all.return_value = [overdue_sub]

        db.execute.side_effect = [status_result, avg_result, overdue_result]

        stats = await get_submittal_stats(db, uuid.uuid4())
        assert stats["overdue"] == 1


# ---------------------------------------------------------------------------
# 11. Register View
# ---------------------------------------------------------------------------


class TestRegister:
    """Tests for get_submittal_register."""

    @pytest.mark.asyncio
    async def test_matrix_aggregation(self):
        from app.services.communication.submittal_service import get_submittal_register

        db = AsyncMock()

        # Counts query
        counts_result = MagicMock()
        counts_result.all.return_value = [
            ("03 30 00", "approved", 2),
            ("03 30 00", "pending_review", 1),
            ("05 12 00", "not_submitted", 3),
        ]

        # Names query
        names_result = MagicMock()
        names_result.all.return_value = [
            ("03 30 00", "Cast-in-Place Concrete"),
            ("05 12 00", "Structural Steel"),
        ]

        db.execute.side_effect = [counts_result, names_result]

        entries = await get_submittal_register(db, uuid.uuid4())
        assert len(entries) == 2
        assert entries[0]["spec_section"] == "03 30 00"
        assert entries[0]["approved"] == 2
        assert entries[0]["pending_review"] == 1
        assert entries[0]["total"] == 3
        assert entries[1]["spec_section"] == "05 12 00"
        assert entries[1]["not_submitted"] == 3

    @pytest.mark.asyncio
    async def test_empty_project(self):
        from app.services.communication.submittal_service import get_submittal_register

        db = AsyncMock()

        counts_result = MagicMock()
        counts_result.all.return_value = []

        names_result = MagicMock()
        names_result.all.return_value = []

        db.execute.side_effect = [counts_result, names_result]

        entries = await get_submittal_register(db, uuid.uuid4())
        assert entries == []


# ---------------------------------------------------------------------------
# 12. Procore Compatibility
# ---------------------------------------------------------------------------


class TestProcoreCompatibility:
    """Tests for Procore-owned field protection."""

    @pytest.mark.asyncio
    async def test_procore_owned_fields_blocked(self):
        from app.services.communication.submittal_service import update_submittal

        sub = _make_submittal(data_source="procore")
        db = AsyncMock()
        db.get.return_value = sub

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await update_submittal(
                db,
                sub.id,
                sub.project_id,
                {"title": "New Title", "description": "Updated desc"},
                uuid.uuid4(),
            )

        # title is Procore-owned, should not change; description should
        assert sub.title == "Test Submittal"
        assert sub.description == "Updated desc"

    @pytest.mark.asyncio
    async def test_procore_allows_priority_update(self):
        from app.services.communication.submittal_service import update_submittal

        sub = _make_submittal(data_source="procore", priority="normal")
        db = AsyncMock()
        db.get.return_value = sub

        with patch(
            "app.services.communication.submittal_service._publish_submittal_event",
            new_callable=AsyncMock,
        ):
            await update_submittal(db, sub.id, sub.project_id, {"priority": "high"}, uuid.uuid4())

        assert sub.priority == "high"


# ---------------------------------------------------------------------------
# 13. Pydantic Schema Validation
# ---------------------------------------------------------------------------


class TestSchemas:
    """Tests for Pydantic schema validation."""

    def test_submittal_create_v2_defaults(self):
        from app.schemas.communication import SubmittalCreateV2

        schema = SubmittalCreateV2(title="Test")
        assert schema.submittal_type == "shop_drawing"
        assert schema.priority == "normal"
        assert schema.review_chain == []
        assert schema.distribution_list == []

    def test_submittal_update_all_optional(self):
        from app.schemas.communication import SubmittalUpdate

        schema = SubmittalUpdate()
        assert schema.title is None
        assert schema.status is None
        assert schema.priority is None

    def test_submittal_review_create(self):
        from app.schemas.communication import SubmittalReviewCreate

        schema = SubmittalReviewCreate(review_action="approved")
        assert schema.review_action == "approved"
        assert schema.comments is None

        schema2 = SubmittalReviewCreate(review_action="rejected", comments="Fix it")
        assert schema2.comments == "Fix it"


# ---------------------------------------------------------------------------
# 14. API Endpoints (mocked service/DB)
# ---------------------------------------------------------------------------


class TestAPIEndpoints:
    """Integration tests for the submittals API router."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.submittals import router

        app = FastAPI()
        app.include_router(router, prefix="/projects")

        mock_user = SimpleNamespace(
            id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            role="org_admin",
            email_verified=True,
        )

        async def mock_get_current_user():
            return mock_user

        async def mock_get_db():
            yield AsyncMock()

        from app.database import get_db as db_dep
        from app.dependencies import get_current_user

        app.dependency_overrides[get_current_user] = mock_get_current_user
        app.dependency_overrides[db_dep] = mock_get_db

        with patch("app.api.v1.submittals.verify_project_access", new_callable=AsyncMock):
            yield TestClient(app)

    def test_create_returns_201(self, client):
        pid = uuid.uuid4()
        detail = {
            "id": str(uuid.uuid4()),
            "project_id": str(pid),
            "submittal_number": "SUB-001",
            "title": "Test",
            "status": "not_submitted",
            "priority": "normal",
            "submittal_type": "shop_drawing",
            "revision_number": 0,
            "reviews": [],
            "attachments": [],
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with (
            patch(
                "app.api.v1.submittals.create_submittal",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=uuid.uuid4()),
            ),
            patch(
                "app.api.v1.submittals.get_submittal_detail",
                new_callable=AsyncMock,
                return_value=detail,
            ),
        ):
            resp = client.post(
                f"/projects/{pid}/submittals",
                json={"title": "Test"},
            )

        assert resp.status_code == 201

    def test_list_returns_200(self, client):
        pid = uuid.uuid4()

        with patch(
            "app.api.v1.submittals.list_submittals",
            new_callable=AsyncMock,
            return_value={"data": [], "meta": {"cursor": None, "has_more": False}},
        ):
            resp = client.get(f"/projects/{pid}/submittals")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_stats_returns_200(self, client):
        pid = uuid.uuid4()

        with patch(
            "app.api.v1.submittals.get_submittal_stats",
            new_callable=AsyncMock,
            return_value={
                "total": 5,
                "not_submitted": 2,
                "pending_review": 1,
                "approved": 1,
                "approved_as_noted": 0,
                "revise_and_resubmit": 0,
                "rejected": 0,
                "closed": 1,
                "overdue": 0,
                "avg_review_days": None,
            },
        ):
            resp = client.get(f"/projects/{pid}/submittals/stats")

        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_register_returns_200(self, client):
        pid = uuid.uuid4()

        with patch(
            "app.api.v1.submittals.get_submittal_register",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(f"/projects/{pid}/submittals/register")

        assert resp.status_code == 200

    def test_export_returns_csv(self, client):
        pid = uuid.uuid4()

        with patch(
            "app.api.v1.submittals.export_submittals_csv",
            new_callable=AsyncMock,
            return_value=b"Submittal Number,Title\nSUB-001,Test\n",
        ):
            resp = client.get(f"/projects/{pid}/submittals/export")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_review_returns_201(self, client):
        pid = uuid.uuid4()
        sid = uuid.uuid4()

        review_data = {
            "id": str(uuid.uuid4()),
            "submittal_id": str(sid),
            "reviewer_id": str(uuid.uuid4()),
            "review_action": "approved",
            "comments": "Good",
            "revision_number": 0,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "created_at": datetime.now(UTC).isoformat(),
        }

        with patch(
            "app.api.v1.submittals.review_submittal",
            new_callable=AsyncMock,
            return_value=review_data,
        ):
            resp = client.post(
                f"/projects/{pid}/submittals/{sid}/review",
                json={"review_action": "approved", "comments": "Good"},
            )

        assert resp.status_code == 201

    def test_resubmit_returns_200(self, client):
        pid = uuid.uuid4()
        sid = uuid.uuid4()

        detail = {
            "id": str(sid),
            "project_id": str(pid),
            "submittal_number": "SUB-001",
            "title": "Test",
            "status": "pending_review",
            "priority": "normal",
            "submittal_type": "shop_drawing",
            "revision_number": 1,
            "reviews": [],
            "attachments": [],
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with (
            patch(
                "app.api.v1.submittals.resubmit_submittal",
                new_callable=AsyncMock,
            ),
            patch(
                "app.api.v1.submittals.get_submittal_detail",
                new_callable=AsyncMock,
                return_value=detail,
            ),
        ):
            resp = client.post(
                f"/projects/{pid}/submittals/{sid}/resubmit",
                json={"notes": "Fixed"},
            )

        assert resp.status_code == 200

    def test_update_returns_200(self, client):
        pid = uuid.uuid4()
        sid = uuid.uuid4()

        detail = {
            "id": str(sid),
            "project_id": str(pid),
            "submittal_number": "SUB-001",
            "title": "Updated",
            "status": "not_submitted",
            "priority": "high",
            "submittal_type": "shop_drawing",
            "revision_number": 0,
            "reviews": [],
            "attachments": [],
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with (
            patch(
                "app.api.v1.submittals.update_submittal",
                new_callable=AsyncMock,
            ),
            patch(
                "app.api.v1.submittals.get_submittal_detail",
                new_callable=AsyncMock,
                return_value=detail,
            ),
        ):
            resp = client.patch(
                f"/projects/{pid}/submittals/{sid}",
                json={"priority": "high"},
            )

        assert resp.status_code == 200
