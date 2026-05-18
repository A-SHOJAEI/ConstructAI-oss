"""Tests for submittal service helpers (status state machine, overdue, review chain).

Pin the documented VALID_TRANSITIONS state machine, the
LEGACY_STATUS_MAP backward-compat ('pending' -> 'pending_review'),
the REVIEW_ACTION_TO_STATUS dispatch (no_exception_taken -> approved),
and the linear-walk reviewer chain lookup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from app.services.communication.submittal_service import (
    _PROCORE_OWNED_FIELDS,
    LEGACY_STATUS_MAP,
    REVIEW_ACTION_TO_STATUS,
    VALID_PRIORITIES,
    VALID_REVIEW_ACTIONS,
    VALID_STATUSES,
    VALID_TRANSITIONS,
    VALID_TYPES,
    _compute_days_open,
    _find_next_reviewer_in_chain,
    _normalize_status,
    check_overdue,
)

# =========================================================================
# State machine — VALID_TRANSITIONS
# =========================================================================


def test_valid_transitions_canonical_states():
    """[contract] Pin all 7 documented submittal states. Refactor must
    NOT silently add or rename a state — DB CHECK constraints + UI
    filters depend on this set."""
    expected = {
        "not_submitted",
        "pending_review",
        "approved",
        "approved_as_noted",
        "revise_and_resubmit",
        "rejected",
        "closed",
    }
    assert set(VALID_TRANSITIONS.keys()) == expected
    assert expected == VALID_STATUSES


def test_not_submitted_only_to_pending_review():
    """[invariant] not_submitted can ONLY transition to pending_review."""
    assert VALID_TRANSITIONS["not_submitted"] == {"pending_review"}


def test_pending_review_4_outcomes():
    """[business invariant] Reviewer has 4 documented outcomes:
    approved / approved_as_noted / revise_and_resubmit / rejected.
    Pin: refactor must NOT add a 5th without explicit review (changes
    audit trail expectations)."""
    assert VALID_TRANSITIONS["pending_review"] == {
        "approved",
        "approved_as_noted",
        "revise_and_resubmit",
        "rejected",
    }


def test_approved_states_only_close():
    """[invariant] Approved/approved_as_noted are terminal-ish — only
    transition is to 'closed' (no reverting)."""
    assert VALID_TRANSITIONS["approved"] == {"closed"}
    assert VALID_TRANSITIONS["approved_as_noted"] == {"closed"}


def test_revise_and_resubmit_loops_to_pending_review():
    """[contract] revise_and_resubmit goes back to pending_review
    (continues the workflow, not a dead end)."""
    assert VALID_TRANSITIONS["revise_and_resubmit"] == {"pending_review"}


def test_rejected_can_resubmit_or_close():
    """[contract] Rejected has 2 paths: pending_review (resubmit) or
    closed (give up). Pin: refactor must NOT remove the resubmit path."""
    assert VALID_TRANSITIONS["rejected"] == {"pending_review", "closed"}


def test_closed_is_terminal():
    """[invariant] Closed is terminal — empty transition set. Pin so
    a refactor doesn't accidentally allow reopening (audit confusion)."""
    assert VALID_TRANSITIONS["closed"] == set()


# =========================================================================
# Other constants
# =========================================================================


def test_legacy_status_map_pending_to_pending_review():
    """[backward-compat] Old 'pending' status -> 'pending_review'.
    Pin: refactor must NOT drop this mapping (would break old data)."""
    assert LEGACY_STATUS_MAP == {"pending": "pending_review"}


def test_normalize_status_pending_legacy():
    assert _normalize_status("pending") == "pending_review"


def test_normalize_status_unknown_passes_through():
    """Unknown status -> returned unchanged (DB check will reject if
    truly invalid)."""
    assert _normalize_status("approved") == "approved"
    assert _normalize_status("xyz_made_up") == "xyz_made_up"


def test_valid_priorities_canonical():
    assert {"urgent", "high", "normal", "low"} == VALID_PRIORITIES


def test_valid_types_canonical_7():
    """[contract] 7 documented submittal types (refactor must NOT
    silently add — DB CHECK constraint + UI dropdown depend)."""
    expected = {
        "shop_drawing",
        "product_data",
        "sample",
        "mock_up",
        "test_report",
        "certificate",
        "other",
    }
    assert expected == VALID_TYPES


def test_review_action_to_status_canonical_5():
    """[contract] 5 documented review actions, with
    no_exception_taken -> approved alias for AIA standard terminology."""
    assert REVIEW_ACTION_TO_STATUS == {
        "approved": "approved",
        "approved_as_noted": "approved_as_noted",
        "revise_and_resubmit": "revise_and_resubmit",
        "rejected": "rejected",
        "no_exception_taken": "approved",
    }


def test_valid_review_actions_derived_from_dispatch():
    """[invariant] VALID_REVIEW_ACTIONS == REVIEW_ACTION_TO_STATUS.keys()
    (single source of truth)."""
    assert set(REVIEW_ACTION_TO_STATUS.keys()) == VALID_REVIEW_ACTIONS


def test_no_exception_taken_aliases_approved():
    """[business invariant] AIA standard 'no exception taken' = approved.
    Pin so the AIA term doesn't accidentally route to a different
    end state."""
    assert REVIEW_ACTION_TO_STATUS["no_exception_taken"] == "approved"


def test_procore_owned_fields_canonical():
    """[business invariant] Procore-of-record fields cannot be locally
    edited."""
    assert {"title", "submittal_number", "spec_section"} == _PROCORE_OWNED_FIELDS


# =========================================================================
# check_overdue
# =========================================================================


def _submittal(status="pending_review", date_required=None, created_at=None):
    return SimpleNamespace(
        status=status,
        date_required=date_required,
        created_at=created_at,
    )


def test_overdue_only_pending_review():
    """[contract] Only pending_review can be overdue (others are
    terminal or pre-submission)."""
    past = date(2020, 1, 1)
    for status in (
        "not_submitted",
        "approved",
        "approved_as_noted",
        "rejected",
        "closed",
        "revise_and_resubmit",
    ):
        assert check_overdue(_submittal(status=status, date_required=past)) is False, (
            f"{status} should not be overdue"
        )


def test_overdue_pending_review_with_past_date():
    assert (
        check_overdue(_submittal(status="pending_review", date_required=date(2020, 1, 1))) is True
    )


def test_overdue_legacy_pending_normalized():
    """[backward-compat] Legacy 'pending' status -> normalized to
    pending_review for overdue check."""
    assert check_overdue(_submittal(status="pending", date_required=date(2020, 1, 1))) is True


def test_overdue_no_date_required_returns_false():
    """[edge case] No date_required -> can't be overdue (don't fabricate)."""
    assert check_overdue(_submittal(status="pending_review", date_required=None)) is False


def test_overdue_future_date_returns_false():
    future = date.today() + timedelta(days=30)
    assert check_overdue(_submittal(status="pending_review", date_required=future)) is False


# =========================================================================
# _compute_days_open
# =========================================================================


def test_days_open_no_created_at_returns_none():
    s = SimpleNamespace(created_at=None, date_returned=None)
    assert _compute_days_open(s) is None


def test_days_open_uses_today_when_not_returned():
    created = datetime.now(UTC) - timedelta(days=7)
    s = SimpleNamespace(created_at=created, date_returned=None)
    assert _compute_days_open(s) == 7


def test_days_open_uses_date_returned_when_set():
    """Returned submittal -> stops accumulating days at date_returned."""
    created = datetime(2026, 1, 1, tzinfo=UTC)
    returned = datetime(2026, 1, 11, tzinfo=UTC)
    s = SimpleNamespace(created_at=created, date_returned=returned)
    assert _compute_days_open(s) == 10


# =========================================================================
# _find_next_reviewer_in_chain — linear walk
# =========================================================================


def test_next_reviewer_finds_next_after_current():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()
    chain = [
        {"user_id": str(a)},
        {"user_id": str(b)},
        {"user_id": str(c)},
    ]
    assert _find_next_reviewer_in_chain(chain, a) == b
    assert _find_next_reviewer_in_chain(chain, b) == c


def test_next_reviewer_returns_none_at_end_of_chain():
    """[contract] Last reviewer -> None (workflow complete)."""
    a = uuid.uuid4()
    b = uuid.uuid4()
    chain = [{"user_id": str(a)}, {"user_id": str(b)}]
    assert _find_next_reviewer_in_chain(chain, b) is None


def test_next_reviewer_returns_none_for_unknown_current():
    """[edge case] Current reviewer not in chain -> None."""
    a = uuid.uuid4()
    chain = [{"user_id": str(a)}]
    assert _find_next_reviewer_in_chain(chain, uuid.uuid4()) is None


def test_next_reviewer_skips_steps_without_user_id():
    """[robustness] Chain steps without user_id are skipped (not crash)."""
    a = uuid.uuid4()
    b = uuid.uuid4()
    chain = [
        {"user_id": str(a)},
        {"user_id": ""},  # blank — skipped
        {"role": "reviewer"},  # no user_id at all — skipped
        {"user_id": str(b)},
    ]
    assert _find_next_reviewer_in_chain(chain, a) == b


def test_next_reviewer_empty_chain():
    assert _find_next_reviewer_in_chain([], uuid.uuid4()) is None
