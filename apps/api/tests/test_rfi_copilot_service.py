"""Tests for the RFI Copilot product service.

Pin the analytics dict shape (status/priority breakdowns +
overdue + open count) and the documented 'open' status set
(draft + submitted, NOT responded/closed/void).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.products.rfi_copilot.service import get_rfi_analytics


def _fake_result(rows):
    """Build a fake SQLAlchemy result object that returns ``rows`` for .all()."""
    fake = MagicMock()
    fake.all = MagicMock(return_value=rows)
    return fake


def _scalar_result(value):
    fake = MagicMock()
    fake.scalar = MagicMock(return_value=value)
    return fake


# =========================================================================
# get_rfi_analytics
# =========================================================================


@pytest.mark.asyncio
async def test_returns_canonical_keys():
    """[contract] Pin the 7 documented keys in the result dict.
    UI dashboard depends on this shape — refactor must NOT silently
    rename or drop a key."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([("draft", 2), ("submitted", 3), ("closed", 5)]),
            _fake_result([("high", 4), ("medium", 6)]),
            _scalar_result(1),
        ]
    )

    out = await get_rfi_analytics(db, uuid.uuid4())
    expected_keys = {
        "total",
        "open_count",
        "overdue_count",
        "responded_count",
        "closed_count",
        "by_status",
        "by_priority",
    }
    assert set(out) == expected_keys


@pytest.mark.asyncio
async def test_total_is_sum_of_by_status():
    """[contract] total = sum of by_status values."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([("draft", 2), ("submitted", 3), ("closed", 5)]),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["total"] == 10


@pytest.mark.asyncio
async def test_open_count_is_draft_plus_submitted():
    """[business invariant] 'Open' = draft + submitted only.
    Pin: refactor must NOT count 'in_progress' or 'pending' as open
    without explicit review (would change dashboard semantics)."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result(
                [
                    ("draft", 2),
                    ("submitted", 3),
                    ("responded", 4),
                    ("closed", 5),
                    ("void", 1),
                ]
            ),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    # draft (2) + submitted (3) = 5 (responded/closed/void NOT counted):
    assert out["open_count"] == 5


@pytest.mark.asyncio
async def test_responded_count_isolated_from_open():
    """responded_count is its own counter (not part of open_count)."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([("draft", 1), ("responded", 7)]),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["responded_count"] == 7
    assert out["open_count"] == 1  # only draft, not responded


@pytest.mark.asyncio
async def test_closed_count_isolated():
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([("closed", 12)]),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["closed_count"] == 12
    assert out["open_count"] == 0


@pytest.mark.asyncio
async def test_overdue_count_passed_through():
    """overdue_count returned as-is from the scalar query."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([]),
            _fake_result([]),
            _scalar_result(7),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["overdue_count"] == 7


@pytest.mark.asyncio
async def test_overdue_count_none_returns_zero():
    """[fallback] If scalar returns None (no matching rows), overdue
    defaults to 0 (not None — UI expects int)."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([]),
            _fake_result([]),
            _scalar_result(None),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["overdue_count"] == 0


@pytest.mark.asyncio
async def test_by_status_dict_preserves_status_keys():
    """[contract] by_status keys are status string values from DB
    (whatever the schema has)."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([("draft", 1), ("submitted", 2)]),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["by_status"] == {"draft": 1, "submitted": 2}


@pytest.mark.asyncio
async def test_by_priority_dict_preserves_priority_keys():
    """[contract] by_priority keys are priority values from DB."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([]),
            _fake_result([("low", 5), ("medium", 3), ("high", 1)]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["by_priority"] == {"low": 5, "medium": 3, "high": 1}


@pytest.mark.asyncio
async def test_empty_database_returns_zero_counts():
    """[edge case] Project with no RFIs -> all counts 0, empty
    dicts. Pin: refactor must NOT raise on empty result."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _fake_result([]),
            _fake_result([]),
            _scalar_result(0),
        ]
    )
    out = await get_rfi_analytics(db, uuid.uuid4())
    assert out["total"] == 0
    assert out["open_count"] == 0
    assert out["overdue_count"] == 0
    assert out["responded_count"] == 0
    assert out["closed_count"] == 0
    assert out["by_status"] == {}
    assert out["by_priority"] == {}
