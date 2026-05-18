"""Tests for RFI workflow system.

Covers:
- RFI number generation
- Status transition validation
- Overdue detection (priority-aware)
- Ball-in-court tracking
- RFI creation with AI suggestion
- RFI response lifecycle
- Close workflow
- CSV export
- Stats aggregation
- Procore compatibility (protected fields)
- AI helpers (spec section, impact assessment)
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


def _make_rfi(**overrides):
    """Build a mock RFI model object with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "rfi_number": "RFI-001",
        "subject": "Test RFI",
        "question": "What is the spec for this?",
        "answer": None,
        "status": "open",
        "priority": "normal",
        "submitted_by": uuid.uuid4(),
        "assigned_to": uuid.uuid4(),
        "ball_in_court": None,
        "response": None,
        "ai_suggested_response": None,
        "due_date": None,
        "spec_section": None,
        "drawing_reference": None,
        "cost_impact": None,
        "schedule_impact": None,
        "cost_impact_amount": None,
        "schedule_impact_days": None,
        "distribution_list": [],
        "date_sent": None,
        "date_answered": None,
        "date_closed": None,
        "responded_at": None,
        "data_source": "manual",
        "procore_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. RFI Number Generation
# ---------------------------------------------------------------------------


class TestRFINumberGeneration:
    """Tests for generate_rfi_number."""

    @pytest.mark.asyncio
    async def test_first_rfi_is_001(self):
        from app.services.communication.rfi_service import generate_rfi_number

        db = AsyncMock()
        # SQL text query returns None (no existing RFI-NNN)
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = None
        db.execute.return_value = scalar_result

        # Fallback count query also returns 0
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute.side_effect = [scalar_result, count_result]

        result = await generate_rfi_number(db, uuid.uuid4())
        assert result == "RFI-001"

    @pytest.mark.asyncio
    async def test_sequential_numbering(self):
        from app.services.communication.rfi_service import generate_rfi_number

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 5  # max existing is RFI-005
        db.execute.return_value = scalar_result

        result = await generate_rfi_number(db, uuid.uuid4())
        assert result == "RFI-006"

    @pytest.mark.asyncio
    async def test_mixed_with_procore_numbers(self):
        from app.services.communication.rfi_service import generate_rfi_number

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 12  # max RFI-NNN is 12
        db.execute.return_value = scalar_result

        result = await generate_rfi_number(db, uuid.uuid4())
        assert result == "RFI-013"

    @pytest.mark.asyncio
    async def test_padding_to_three_digits(self):
        from app.services.communication.rfi_service import generate_rfi_number

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 1
        db.execute.return_value = scalar_result

        result = await generate_rfi_number(db, uuid.uuid4())
        assert result == "RFI-002"
        assert len(result.split("-")[1]) == 3


# ---------------------------------------------------------------------------
# 2. Status Transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Tests for VALID_TRANSITIONS."""

    def test_draft_to_open(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        assert "open" in VALID_TRANSITIONS["draft"]

    def test_draft_to_void(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        assert "void" in VALID_TRANSITIONS["draft"]

    def test_draft_to_closed_invalid(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        assert "closed" not in VALID_TRANSITIONS["draft"]

    def test_void_is_terminal(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        assert len(VALID_TRANSITIONS["void"]) == 0

    def test_closed_can_reopen(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        assert "open" in VALID_TRANSITIONS["closed"]

    def test_open_allows_all_forward(self):
        from app.services.communication.rfi_service import VALID_TRANSITIONS

        allowed = VALID_TRANSITIONS["open"]
        assert "pending_review" in allowed
        assert "answered" in allowed
        assert "closed" in allowed
        assert "void" in allowed


# ---------------------------------------------------------------------------
# 3. Overdue Detection
# ---------------------------------------------------------------------------


class TestOverdueDetection:
    """Tests for check_overdue (priority-aware)."""

    def test_normal_priority_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="open",
            priority="normal",
            due_date=None,
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        assert check_overdue(rfi) is True

    def test_normal_priority_not_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="open",
            priority="normal",
            due_date=None,
            created_at=datetime.now(UTC) - timedelta(days=5),
        )
        assert check_overdue(rfi) is False

    def test_urgent_priority_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="open",
            priority="urgent",
            due_date=None,
            created_at=datetime.now(UTC) - timedelta(days=5),
        )
        assert check_overdue(rfi) is True

    def test_closed_rfi_never_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="closed",
            priority="normal",
            due_date=date.today() - timedelta(days=30),
            created_at=datetime.now(UTC) - timedelta(days=60),
        )
        assert check_overdue(rfi) is False

    def test_explicit_due_date_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="open",
            priority="low",
            due_date=date.today() - timedelta(days=1),
        )
        assert check_overdue(rfi) is True

    def test_explicit_due_date_not_overdue(self):
        from app.services.communication.rfi_service import check_overdue

        rfi = _make_rfi(
            status="open",
            priority="low",
            due_date=date.today() + timedelta(days=5),
        )
        assert check_overdue(rfi) is False


# ---------------------------------------------------------------------------
# 4. Ball-in-Court Tracking
# ---------------------------------------------------------------------------


class TestBallInCourt:
    """Tests for ball-in-court logic."""

    @pytest.mark.asyncio
    async def test_create_sets_ball_to_assignee(self):
        from app.services.communication.rfi_service import create_rfi

        assigned = uuid.uuid4()
        submitter = uuid.uuid4()

        db = AsyncMock()
        # generate_rfi_number
        num_result = MagicMock()
        num_result.scalar.return_value = None
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute.side_effect = [num_result, count_result]

        # Make db.add capture the RFI (sync call, use MagicMock)
        created_rfi = None

        def capture_add(obj):
            nonlocal created_rfi
            created_rfi = obj

        db.add = MagicMock(side_effect=capture_add)

        with (
            patch(
                "app.services.communication.rfi_helper.suggest_rfi_response",
                new_callable=AsyncMock,
                return_value={"suggested_response": "test", "references": [], "confidence": 0.3},
            ),
            patch(
                "app.services.communication.rfi_service._publish_rfi_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "Test", "question": "Q?", "assigned_to": assigned},
                submitter,
            )

        assert created_rfi is not None
        assert created_rfi.ball_in_court == assigned

    @pytest.mark.asyncio
    async def test_respond_returns_ball_to_submitter(self):
        from app.services.communication.rfi_service import respond_to_rfi

        submitter = uuid.uuid4()
        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(
            id=rfi_id,
            project_id=project_id,
            status="open",
            submitted_by=submitter,
            ball_in_court=uuid.uuid4(),
        )

        db = AsyncMock()
        db.get.return_value = rfi

        response_obj = None

        def capture_add(obj):
            nonlocal response_obj
            response_obj = obj

        db.add = MagicMock(side_effect=capture_add)

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            await respond_to_rfi(db, rfi_id, project_id, uuid.uuid4(), "My response")

        assert rfi.ball_in_court == submitter


# ---------------------------------------------------------------------------
# 5. RFI Creation
# ---------------------------------------------------------------------------


class TestRFICreation:
    """Tests for create_rfi."""

    @pytest.mark.asyncio
    async def test_auto_number_assigned(self):
        from app.services.communication.rfi_service import create_rfi

        db = AsyncMock()
        num_result = MagicMock()
        num_result.scalar.return_value = 3
        db.execute.return_value = num_result

        created = None

        def capture(obj):
            nonlocal created
            created = obj

        db.add = MagicMock(side_effect=capture)

        with (
            patch(
                "app.services.communication.rfi_helper.suggest_rfi_response",
                new_callable=AsyncMock,
                return_value={
                    "suggested_response": "AI says...",
                    "references": [],
                    "confidence": 0.5,
                },
            ),
            patch(
                "app.services.communication.rfi_service._publish_rfi_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "New RFI", "question": "Details?"},
                uuid.uuid4(),
            )

        assert created.rfi_number == "RFI-004"

    @pytest.mark.asyncio
    async def test_ai_suggestion_populated(self):
        from app.services.communication.rfi_service import create_rfi

        db = AsyncMock()
        num_result = MagicMock()
        num_result.scalar.return_value = None
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute.side_effect = [num_result, count_result]

        created = None

        def capture(obj):
            nonlocal created
            created = obj

        db.add = MagicMock(side_effect=capture)

        with (
            patch(
                "app.services.communication.rfi_helper.suggest_rfi_response",
                new_callable=AsyncMock,
                return_value={
                    "suggested_response": "Suggested answer",
                    "references": ["Spec: A"],
                    "confidence": 0.7,
                },
            ),
            patch(
                "app.services.communication.rfi_service._publish_rfi_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "Test", "question": "Q?"},
                uuid.uuid4(),
            )

        assert created.ai_suggested_response == "Suggested answer"

    @pytest.mark.asyncio
    async def test_default_due_date_from_priority(self):
        from app.services.communication.rfi_service import OVERDUE_DAYS, create_rfi

        db = AsyncMock()
        num_result = MagicMock()
        num_result.scalar.return_value = 0
        db.execute.return_value = num_result

        created = None

        def capture(obj):
            nonlocal created
            created = obj

        db.add = MagicMock(side_effect=capture)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch(
                "app.services.communication.rfi_helper.suggest_rfi_response",
                new_callable=AsyncMock,
                return_value={"suggested_response": "", "references": [], "confidence": 0.3},
            ),
            patch(
                "app.services.communication.rfi_service._publish_rfi_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "T", "question": "Q?", "priority": "urgent"},
                uuid.uuid4(),
            )

        expected = date.today() + timedelta(days=OVERDUE_DAYS["urgent"])
        assert created.due_date == expected

    @pytest.mark.asyncio
    async def test_invalid_initial_status_rejected(self):
        from app.services.communication.rfi_service import create_rfi

        db = AsyncMock()
        num_result = MagicMock()
        num_result.scalar.return_value = 0
        db.execute.return_value = num_result

        with pytest.raises(ValueError, match="Initial RFI status"):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "T", "question": "Q?", "status": "closed"},
                uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_draft_status_allowed(self):
        from app.services.communication.rfi_service import create_rfi

        db = AsyncMock()
        num_result = MagicMock()
        num_result.scalar.return_value = 0
        db.execute.return_value = num_result

        created = None

        def capture(obj):
            nonlocal created
            created = obj

        db.add = MagicMock(side_effect=capture)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch(
                "app.services.communication.rfi_helper.suggest_rfi_response",
                new_callable=AsyncMock,
                return_value={"suggested_response": "", "references": [], "confidence": 0.3},
            ),
            patch(
                "app.services.communication.rfi_service._publish_rfi_event",
                new_callable=AsyncMock,
            ),
        ):
            await create_rfi(
                db,
                uuid.uuid4(),
                {"subject": "T", "question": "Q?", "status": "draft"},
                uuid.uuid4(),
            )

        assert created.status == "draft"


# ---------------------------------------------------------------------------
# 6. RFI Response
# ---------------------------------------------------------------------------


class TestRFIResponse:
    """Tests for respond_to_rfi."""

    @pytest.mark.asyncio
    async def test_response_created_with_pending_status(self):
        from app.services.communication.rfi_service import respond_to_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="open")

        db = AsyncMock()
        db.get.return_value = rfi

        added = None

        def capture(obj):
            nonlocal added
            added = obj

        db.add = MagicMock(side_effect=capture)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            await respond_to_rfi(db, rfi_id, project_id, uuid.uuid4(), "My answer")

        assert added.status == "pending"
        assert added.response_text == "My answer"

    @pytest.mark.asyncio
    async def test_rfi_transitions_to_pending_review(self):
        from app.services.communication.rfi_service import respond_to_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="open")

        db = AsyncMock()
        db.get.return_value = rfi
        db.add = MagicMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            await respond_to_rfi(db, rfi_id, project_id, uuid.uuid4(), "Answer")

        assert rfi.status == "pending_review"

    @pytest.mark.asyncio
    async def test_cannot_respond_to_void_rfi(self):
        from app.services.communication.rfi_service import respond_to_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="void")

        db = AsyncMock()
        db.get.return_value = rfi

        with pytest.raises(ValueError, match="voided"):
            await respond_to_rfi(db, rfi_id, project_id, uuid.uuid4(), "Answer")


# ---------------------------------------------------------------------------
# 7. Close RFI
# ---------------------------------------------------------------------------


class TestCloseRFI:
    """Tests for close_rfi."""

    @pytest.mark.asyncio
    async def test_close_sets_answer_and_date(self):
        from app.services.communication.rfi_service import close_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="open")

        db = AsyncMock()
        db.get.return_value = rfi
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await close_rfi(db, rfi_id, project_id, uuid.uuid4(), "Final answer")

        assert result.status == "closed"
        assert result.answer == "Final answer"
        assert result.date_closed is not None

    @pytest.mark.asyncio
    async def test_close_sets_date_answered_if_missing(self):
        from app.services.communication.rfi_service import close_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(
            id=rfi_id,
            project_id=project_id,
            status="open",
            date_answered=None,
        )

        db = AsyncMock()
        db.get.return_value = rfi
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await close_rfi(db, rfi_id, project_id, uuid.uuid4())

        assert result.date_answered is not None

    @pytest.mark.asyncio
    async def test_cannot_close_void_rfi(self):
        from app.services.communication.rfi_service import close_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="void")

        db = AsyncMock()
        db.get.return_value = rfi

        with pytest.raises(ValueError, match="Cannot close"):
            await close_rfi(db, rfi_id, project_id, uuid.uuid4())


# ---------------------------------------------------------------------------
# 8. Days Open / Helpers
# ---------------------------------------------------------------------------


class TestDaysOpen:
    """Tests for _compute_days_open."""

    def test_open_rfi_days(self):
        from app.services.communication.rfi_service import _compute_days_open

        rfi = _make_rfi(
            created_at=datetime.now(UTC) - timedelta(days=10),
            date_closed=None,
        )
        days = _compute_days_open(rfi)
        # Allow for timezone boundary (UTC vs local)
        assert days in (9, 10, 11)

    def test_closed_rfi_days(self):
        from app.services.communication.rfi_service import _compute_days_open

        created = datetime.now(UTC) - timedelta(days=15)
        closed = created + timedelta(days=5)
        rfi = _make_rfi(created_at=created, date_closed=closed)
        assert _compute_days_open(rfi) == 5

    def test_none_created_at(self):
        from app.services.communication.rfi_service import _compute_days_open

        rfi = _make_rfi(created_at=None)
        assert _compute_days_open(rfi) is None


# ---------------------------------------------------------------------------
# 9. Procore Compatibility
# ---------------------------------------------------------------------------


class TestProcoreCompatibility:
    """Tests for Procore-sourced RFI protections."""

    @pytest.mark.asyncio
    async def test_procore_blocks_owned_fields(self):
        from app.services.communication.rfi_service import update_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(
            id=rfi_id,
            project_id=project_id,
            data_source="procore",
            subject="Original Subject",
            question="Original Question",
        )

        db = AsyncMock()
        db.get.return_value = rfi
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await update_rfi(
                db,
                rfi_id,
                project_id,
                {"subject": "Changed", "priority": "urgent"},
                uuid.uuid4(),
            )

        # Subject should NOT change (Procore-owned)
        assert result.subject == "Original Subject"
        # Priority SHOULD change (not Procore-owned)
        assert result.priority == "urgent"

    @pytest.mark.asyncio
    async def test_procore_allows_impact_updates(self):
        from app.services.communication.rfi_service import update_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(
            id=rfi_id,
            project_id=project_id,
            data_source="procore",
        )

        db = AsyncMock()
        db.get.return_value = rfi
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await update_rfi(
                db,
                rfi_id,
                project_id,
                {"cost_impact": True, "schedule_impact": True},
                uuid.uuid4(),
            )

        assert result.cost_impact is True
        assert result.schedule_impact is True


# ---------------------------------------------------------------------------
# 10. AI Helpers
# ---------------------------------------------------------------------------


class TestSuggestSpecSection:
    """Tests for suggest_spec_section in rfi_helper."""

    @pytest.mark.asyncio
    async def test_concrete_keywords(self):
        from app.services.communication.rfi_helper import suggest_spec_section

        result = await suggest_spec_section(
            "Concrete mix design", "What is the rebar spacing for the footing?"
        )
        assert result["spec_section"] is not None
        assert "03" in result["spec_section"]  # Division 03 - Concrete
        assert result["confidence"] > 0

    @pytest.mark.asyncio
    async def test_electrical_keywords(self):
        from app.services.communication.rfi_helper import suggest_spec_section

        result = await suggest_spec_section(
            "Electrical panel upgrade", "Need conduit size for wiring run"
        )
        assert "26" in result["spec_section"]  # Division 26 - Electrical

    @pytest.mark.asyncio
    async def test_no_match(self):
        from app.services.communication.rfi_helper import suggest_spec_section

        result = await suggest_spec_section("Project status", "When is the next milestone?")
        assert result["spec_section"] is None
        assert result["confidence"] == 0.0


class TestAssessImpact:
    """Tests for assess_impact in rfi_helper."""

    @pytest.mark.asyncio
    async def test_cost_keywords(self):
        from app.services.communication.rfi_helper import assess_impact

        result = await assess_impact(
            "Material substitution", "Need alternate material, price impact unclear"
        )
        assert result["cost_impact"] is True

    @pytest.mark.asyncio
    async def test_schedule_keywords(self):
        from app.services.communication.rfi_helper import assess_impact

        result = await assess_impact(
            "Long lead item", "Procurement delay on critical path equipment"
        )
        assert result["schedule_impact"] is True

    @pytest.mark.asyncio
    async def test_no_impact(self):
        from app.services.communication.rfi_helper import assess_impact

        result = await assess_impact("Color selection", "What is the exact paint code?")
        assert result["cost_impact"] is False
        assert result["schedule_impact"] is False


# ---------------------------------------------------------------------------
# 11. Schema Validation
# ---------------------------------------------------------------------------


class TestSchemas:
    """Test that RFI Pydantic schemas validate correctly."""

    def test_rfi_create_v2_defaults(self):
        from app.schemas.communication import RFICreateV2

        rfi = RFICreateV2(subject="Test", question="What?")
        assert rfi.priority == "normal"
        assert rfi.status == "open"
        assert rfi.distribution_list == []

    def test_rfi_update_partial(self):
        from app.schemas.communication import RFIUpdate

        update = RFIUpdate(priority="urgent")
        dumped = update.model_dump(exclude_unset=True)
        assert dumped == {"priority": "urgent"}
        assert "subject" not in dumped

    def test_rfi_detail_response_from_attributes(self):
        from app.schemas.communication import RFIDetailResponse

        now = datetime.now(UTC)
        data = {
            "id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "rfi_number": "RFI-001",
            "subject": "Test",
            "question": "Q?",
            "status": "open",
            "priority": "normal",
            "created_at": now,
            "updated_at": now,
        }
        resp = RFIDetailResponse(**data)
        assert resp.rfi_number == "RFI-001"
        assert resp.responses == []
        assert resp.attachments == []
        assert resp.is_overdue is False

    def test_rfi_stats_response(self):
        from app.schemas.communication import RFIStatsResponse

        stats = RFIStatsResponse(total=10, open=5, closed=3)
        assert stats.draft == 0
        assert stats.overdue == 0

    def test_rfi_response_item(self):
        from app.schemas.communication import RFIResponseItem

        now = datetime.now(UTC)
        item = RFIResponseItem(
            id=uuid.uuid4(),
            rfi_id=uuid.uuid4(),
            response_text="Answer",
            status="pending",
            responded_at=now,
            created_at=now,
        )
        assert item.responder_id is None

    def test_rfi_attachment_item(self):
        from app.schemas.communication import RFIAttachmentItem

        now = datetime.now(UTC)
        item = RFIAttachmentItem(
            id=uuid.uuid4(),
            rfi_id=uuid.uuid4(),
            file_path="rfis/proj/rfi/file.pdf",
            file_name="file.pdf",
            uploaded_at=now,
        )
        assert item.download_url is None
        assert item.file_type is None

    def test_rfi_close_request_optional_answer(self):
        from app.schemas.communication import RFICloseRequest

        req = RFICloseRequest()
        assert req.answer is None

        req2 = RFICloseRequest(answer="Done")
        assert req2.answer == "Done"


# ---------------------------------------------------------------------------
# 12. Update RFI
# ---------------------------------------------------------------------------


class TestUpdateRFI:
    """Tests for update_rfi status transitions and field updates."""

    @pytest.mark.asyncio
    async def test_valid_status_transition(self):
        from app.services.communication.rfi_service import update_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="open")

        db = AsyncMock()
        db.get.return_value = rfi
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await update_rfi(
                db,
                rfi_id,
                project_id,
                {"status": "answered"},
                uuid.uuid4(),
            )

        assert result.status == "answered"
        assert result.date_answered is not None

    @pytest.mark.asyncio
    async def test_invalid_status_transition_raises(self):
        from app.services.communication.rfi_service import update_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(id=rfi_id, project_id=project_id, status="draft")

        db = AsyncMock()
        db.get.return_value = rfi

        with pytest.raises(ValueError, match="Cannot transition"):
            await update_rfi(
                db,
                rfi_id,
                project_id,
                {"status": "closed"},
                uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_reopen_clears_date_closed(self):
        from app.services.communication.rfi_service import update_rfi

        rfi_id = uuid.uuid4()
        project_id = uuid.uuid4()
        rfi = _make_rfi(
            id=rfi_id,
            project_id=project_id,
            status="closed",
            date_closed=datetime.now(UTC),
        )

        db = AsyncMock()
        db.get.return_value = rfi
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.services.communication.rfi_service._publish_rfi_event",
            new_callable=AsyncMock,
        ):
            result = await update_rfi(
                db,
                rfi_id,
                project_id,
                {"status": "open"},
                uuid.uuid4(),
            )

        assert result.status == "open"
        assert result.date_closed is None


# ---------------------------------------------------------------------------
# 13. CSV Export
# ---------------------------------------------------------------------------


class TestCSVExport:
    """Tests for export_rfis_csv."""

    @pytest.mark.asyncio
    async def test_export_produces_valid_csv(self):
        from app.services.communication.rfi_service import export_rfis_csv

        rfis = [
            _make_rfi(rfi_number="RFI-001", subject="Test 1", status="open"),
            _make_rfi(rfi_number="RFI-002", subject="Test 2", status="closed"),
        ]

        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = rfis
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        csv_bytes = await export_rfis_csv(db, uuid.uuid4())
        text = csv_bytes.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        assert rows[0][0] == "RFI Number"  # Header
        assert len(rows) == 3  # Header + 2 data rows
        assert rows[1][0] == "RFI-001"
        assert rows[2][0] == "RFI-002"

    @pytest.mark.asyncio
    async def test_empty_project_csv_has_headers(self):
        from app.services.communication.rfi_service import export_rfis_csv

        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        csv_bytes = await export_rfis_csv(db, uuid.uuid4())
        text = csv_bytes.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        assert len(rows) == 1  # Header only
        assert "RFI Number" in rows[0]


# ---------------------------------------------------------------------------
# 14. Stats
# ---------------------------------------------------------------------------


class TestRFIStats:
    """Tests for get_rfi_stats."""

    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        from app.services.communication.rfi_service import get_rfi_stats

        db = AsyncMock()

        # Status counts query
        status_result = MagicMock()
        status_result.all.return_value = [
            ("open", 5),
            ("closed", 3),
            ("draft", 2),
        ]

        # Avg days query
        avg_result = MagicMock()
        avg_result.scalar.return_value = 4.5

        # Open RFIs for overdue check
        open_result = MagicMock()
        open_scalars = MagicMock()
        open_scalars.all.return_value = []
        open_result.scalars.return_value = open_scalars

        db.execute.side_effect = [status_result, avg_result, open_result]

        stats = await get_rfi_stats(db, uuid.uuid4())

        assert stats["total"] == 10
        assert stats["open"] == 5
        assert stats["closed"] == 3
        assert stats["draft"] == 2
        assert stats["avg_response_days"] == 4.5

    @pytest.mark.asyncio
    async def test_overdue_count(self):
        from app.services.communication.rfi_service import get_rfi_stats

        db = AsyncMock()

        status_result = MagicMock()
        status_result.all.return_value = [("open", 3)]

        avg_result = MagicMock()
        avg_result.scalar.return_value = None

        # Two open RFIs - one overdue, one not
        overdue_rfi = _make_rfi(
            status="open",
            priority="normal",
            created_at=datetime.now(UTC) - timedelta(days=15),
        )
        fresh_rfi = _make_rfi(
            status="open",
            priority="normal",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )

        open_result = MagicMock()
        open_scalars = MagicMock()
        open_scalars.all.return_value = [overdue_rfi, fresh_rfi]
        open_result.scalars.return_value = open_scalars

        db.execute.side_effect = [status_result, avg_result, open_result]

        stats = await get_rfi_stats(db, uuid.uuid4())
        assert stats["overdue"] == 1


# ---------------------------------------------------------------------------
# 15. API Endpoints (mocked service layer)
# ---------------------------------------------------------------------------


class TestAPIEndpoints:
    """Tests for RFI API endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client with mocked dependencies."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.database import get_db
        from app.dependencies import get_current_user

        mock_user = SimpleNamespace(
            id=uuid.uuid4(), role="org_admin", org_id=uuid.uuid4(), email_verified=True
        )
        self._project_id = uuid.uuid4()
        self._mock_user = mock_user

        async def mock_db():
            yield AsyncMock()

        async def mock_current_user():
            return mock_user

        # Patch verify_project_access before importing router
        with patch(
            "app.api.v1.rfis.verify_project_access",
            new_callable=AsyncMock,
        ):
            from app.api.v1.rfis import router

            app = FastAPI()
            app.include_router(router, prefix="/projects")
            app.dependency_overrides[get_db] = mock_db
            app.dependency_overrides[get_current_user] = mock_current_user

            yield TestClient(app)

    def test_create_rfi_returns_201(self, client):
        now = datetime.now(UTC).isoformat()
        detail = {
            "id": str(uuid.uuid4()),
            "project_id": str(self._project_id),
            "rfi_number": "RFI-001",
            "subject": "Test",
            "question": "Q?",
            "status": "open",
            "priority": "normal",
            "created_at": now,
            "updated_at": now,
            "responses": [],
            "attachments": [],
            "distribution_list": [],
            "is_overdue": False,
            "days_open": 0,
        }

        with (
            patch(
                "app.api.v1.rfis.create_rfi",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=uuid.uuid4()),
            ),
            patch(
                "app.api.v1.rfis.get_rfi_detail",
                new_callable=AsyncMock,
                return_value=detail,
            ),
        ):
            resp = client.post(
                f"/projects/{self._project_id}/rfis",
                json={"subject": "Test", "question": "Q?"},
            )

        assert resp.status_code == 201

    def test_list_rfis_with_status_filter(self, client):
        now = datetime.now(UTC).isoformat()
        list_result = {
            "data": [
                {
                    "id": str(uuid.uuid4()),
                    "project_id": str(self._project_id),
                    "rfi_number": "RFI-001",
                    "subject": "Test",
                    "question": "Q?",
                    "status": "open",
                    "priority": "normal",
                    "created_at": now,
                    "updated_at": now,
                    "responses": [],
                    "attachments": [],
                    "distribution_list": [],
                    "is_overdue": False,
                    "days_open": 0,
                }
            ],
            "meta": {"cursor": None, "has_more": False},
        }

        with patch(
            "app.api.v1.rfis.list_rfis",
            new_callable=AsyncMock,
            return_value=list_result,
        ):
            resp = client.get(f"/projects/{self._project_id}/rfis?status=open")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1

    def test_get_stats(self, client):
        with patch(
            "app.api.v1.rfis.get_rfi_stats",
            new_callable=AsyncMock,
            return_value={
                "total": 10,
                "open": 5,
                "closed": 3,
                "draft": 2,
                "pending_review": 0,
                "answered": 0,
                "void": 0,
                "overdue": 1,
                "avg_response_days": 4.5,
            },
        ):
            resp = client.get(f"/projects/{self._project_id}/rfis/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10

    def test_get_export_csv(self, client):
        csv_content = b"RFI Number,Subject\nRFI-001,Test\n"

        with patch(
            "app.api.v1.rfis.export_rfis_csv",
            new_callable=AsyncMock,
            return_value=csv_content,
        ):
            resp = client.get(f"/projects/{self._project_id}/rfis/export")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_close_rfi_endpoint(self, client):
        rfi_id = uuid.uuid4()
        now = datetime.now(UTC).isoformat()
        detail = {
            "id": str(rfi_id),
            "project_id": str(self._project_id),
            "rfi_number": "RFI-001",
            "subject": "Test",
            "question": "Q?",
            "status": "closed",
            "priority": "normal",
            "answer": "Final",
            "created_at": now,
            "updated_at": now,
            "responses": [],
            "attachments": [],
            "distribution_list": [],
            "is_overdue": False,
            "days_open": 5,
        }

        with (
            patch(
                "app.api.v1.rfis.close_rfi",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(id=rfi_id),
            ),
            patch(
                "app.api.v1.rfis.get_rfi_detail",
                new_callable=AsyncMock,
                return_value=detail,
            ),
        ):
            resp = client.post(
                f"/projects/{self._project_id}/rfis/{rfi_id}/close",
                json={"answer": "Final"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    def test_respond_to_rfi_returns_201(self, client):
        rfi_id = uuid.uuid4()
        now = datetime.now(UTC).isoformat()
        response_item = {
            "id": str(uuid.uuid4()),
            "rfi_id": str(rfi_id),
            "responder_id": str(self._mock_user.id),
            "response_text": "Here is my answer",
            "status": "pending",
            "responded_at": now,
            "created_at": now,
        }

        with patch(
            "app.api.v1.rfis.respond_to_rfi",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(**response_item),
        ):
            resp = client.post(
                f"/projects/{self._project_id}/rfis/{rfi_id}/respond",
                json={"response_text": "Here is my answer"},
            )

        assert resp.status_code == 201

    def test_update_rfi_validation_error(self, client):
        rfi_id = uuid.uuid4()

        with patch(
            "app.api.v1.rfis.update_rfi",
            new_callable=AsyncMock,
            side_effect=ValueError("Cannot transition from 'void' to 'open'"),
        ):
            resp = client.patch(
                f"/projects/{self._project_id}/rfis/{rfi_id}",
                json={"status": "open"},
            )

        assert resp.status_code == 422

    def test_create_rfi_validation_error(self, client):
        with patch(
            "app.api.v1.rfis.create_rfi",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid priority"),
        ):
            resp = client.post(
                f"/projects/{self._project_id}/rfis",
                json={"subject": "Test", "question": "Q?", "priority": "bogus"},
            )

        assert resp.status_code == 422
