"""Tests for the PCO/COR status-transition validation matrix.

[business invariant] PCO and COR each have a documented state
machine. Pin every allowed transition + every disallowed transition
so a refactor can't accidentally let a draft jump straight to
approved (skipping review) or void → anything (resurrecting dead
records).
"""

from __future__ import annotations

import pytest

from app.services.controls.change_order_lifecycle import (
    COR_TRANSITIONS,
    PCO_TRANSITIONS,
    _validate_transition,
)

# =========================================================================
# PCO transitions
# =========================================================================


def test_pco_states_canonical():
    """Pin the documented PCO states — refactor must not silently
    drop one."""
    expected = {"draft", "pending_review", "approved", "rejected", "void"}
    assert set(PCO_TRANSITIONS.keys()) == expected


def test_pco_draft_can_go_to_pending_review():
    _validate_transition("draft", "pending_review", PCO_TRANSITIONS)


def test_pco_draft_can_go_to_void():
    _validate_transition("draft", "void", PCO_TRANSITIONS)


def test_pco_draft_cannot_skip_review():
    """Draft → approved must NOT bypass the pending_review state."""
    with pytest.raises(ValueError, match="Cannot transition"):
        _validate_transition("draft", "approved", PCO_TRANSITIONS)


def test_pco_pending_review_can_be_approved():
    _validate_transition("pending_review", "approved", PCO_TRANSITIONS)


def test_pco_pending_review_can_be_rejected():
    _validate_transition("pending_review", "rejected", PCO_TRANSITIONS)


def test_pco_approved_only_to_void():
    """Once approved, only void is allowed (no editing approved
    records)."""
    _validate_transition("approved", "void", PCO_TRANSITIONS)
    with pytest.raises(ValueError):
        _validate_transition("approved", "draft", PCO_TRANSITIONS)
    with pytest.raises(ValueError):
        _validate_transition("approved", "pending_review", PCO_TRANSITIONS)
    with pytest.raises(ValueError):
        _validate_transition("approved", "rejected", PCO_TRANSITIONS)


def test_pco_rejected_can_be_resubmitted_via_draft():
    """Rejected → draft is the canonical "fix and resubmit" path."""
    _validate_transition("rejected", "draft", PCO_TRANSITIONS)


def test_pco_void_terminal():
    """Void is terminal — no transitions out."""
    for new_state in ("draft", "pending_review", "approved", "rejected"):
        with pytest.raises(ValueError, match="terminal state"):
            _validate_transition("void", new_state, PCO_TRANSITIONS)


def test_pco_unknown_current_state_rejected():
    """An invalid current state should also raise."""
    with pytest.raises(ValueError, match="Cannot transition"):
        _validate_transition("unknown", "approved", PCO_TRANSITIONS)


# =========================================================================
# COR transitions
# =========================================================================


def test_cor_states_canonical():
    """Pin the documented COR states — refactor must not silently
    drop one."""
    expected = {"draft", "submitted", "under_review", "approved", "rejected", "void"}
    assert set(COR_TRANSITIONS.keys()) == expected


def test_cor_draft_can_be_submitted():
    _validate_transition("draft", "submitted", COR_TRANSITIONS)


def test_cor_draft_cannot_skip_to_under_review():
    """COR has an explicit submit step before review begins."""
    with pytest.raises(ValueError):
        _validate_transition("draft", "under_review", COR_TRANSITIONS)


def test_cor_submitted_can_go_under_review():
    _validate_transition("submitted", "under_review", COR_TRANSITIONS)


def test_cor_under_review_can_be_approved():
    _validate_transition("under_review", "approved", COR_TRANSITIONS)


def test_cor_under_review_can_be_rejected():
    _validate_transition("under_review", "rejected", COR_TRANSITIONS)


def test_cor_under_review_cannot_skip_to_void_only():
    """Under-review can also be voided (cancel during review)."""
    _validate_transition("under_review", "void", COR_TRANSITIONS)


def test_cor_approved_is_terminal_no_void():
    """[business invariant] Approved COR is TERMINAL — once approved,
    a CO is created from it. The COR record cannot be voided after
    that. Pin this strict semantics."""
    for new_state in ("draft", "submitted", "under_review", "rejected", "void"):
        with pytest.raises(ValueError, match="terminal state"):
            _validate_transition("approved", new_state, COR_TRANSITIONS)


def test_cor_rejected_can_be_resubmitted_via_draft():
    _validate_transition("rejected", "draft", COR_TRANSITIONS)


def test_cor_void_terminal():
    for new_state in ("draft", "submitted", "under_review", "approved", "rejected"):
        with pytest.raises(ValueError, match="terminal state"):
            _validate_transition("void", new_state, COR_TRANSITIONS)


# =========================================================================
# _validate_transition — error-message shape
# =========================================================================


def test_validate_transition_error_lists_allowed_states():
    """The error message should tell the caller which transitions ARE
    allowed — actionable feedback."""
    with pytest.raises(ValueError) as exc_info:
        _validate_transition("draft", "approved", PCO_TRANSITIONS)
    msg = str(exc_info.value)
    # The allowed transitions for "draft" are {"pending_review", "void"}
    assert "pending_review" in msg
    assert "void" in msg


def test_validate_transition_terminal_state_message():
    """Terminal-state errors use a distinct phrasing."""
    with pytest.raises(ValueError, match="terminal state"):
        _validate_transition("approved", "draft", COR_TRANSITIONS)
