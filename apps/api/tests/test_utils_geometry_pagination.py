"""Tests for the geometry and pagination utility modules.

Both are pure helpers (geometry: point-in-polygon / distance / area /
bbox; pagination: cursor encode/decode + paginate skeleton). The
``paginate`` helper is exercised with mocks for the AsyncSession.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.utils.geometry import bbox_center, distance, point_in_polygon, polygon_area
from app.utils.pagination import decode_cursor, encode_cursor, paginate

# =========================================================================
# geometry.point_in_polygon
# =========================================================================


# Counter-clockwise unit square centred at origin
_SQUARE = [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]


def test_point_in_polygon_strict_interior():
    assert point_in_polygon(0.0, 0.0, _SQUARE) is True


def test_point_in_polygon_outside():
    assert point_in_polygon(2.0, 2.0, _SQUARE) is False
    assert point_in_polygon(-2.0, 0.0, _SQUARE) is False


def test_point_in_polygon_concave_shape():
    """Validate ray-casting on a non-convex polygon — a U-shape."""
    u_polygon = [
        [0.0, 0.0],
        [3.0, 0.0],
        [3.0, 3.0],
        [2.0, 3.0],
        [2.0, 1.0],
        [1.0, 1.0],
        [1.0, 3.0],
        [0.0, 3.0],
    ]
    # Point in the bottom slab (interior):
    assert point_in_polygon(1.5, 0.5, u_polygon) is True
    # Point in the gap (between the legs of the U): should be OUTSIDE.
    assert point_in_polygon(1.5, 2.0, u_polygon) is False
    # Inside one of the legs:
    assert point_in_polygon(0.5, 2.0, u_polygon) is True


# =========================================================================
# geometry.bbox_center / distance / polygon_area
# =========================================================================


def test_bbox_center_returns_midpoint():
    assert bbox_center((0, 0, 10, 20)) == (5.0, 10.0)


def test_bbox_center_handles_negative_origin():
    assert bbox_center((-10, -10, 10, 10)) == (0.0, 0.0)


def test_distance_pythagorean():
    assert distance((0.0, 0.0), (3.0, 4.0)) == 5.0


def test_distance_zero_when_same_point():
    assert distance((1.5, 2.5), (1.5, 2.5)) == 0.0


def test_polygon_area_unit_square():
    """1x1 unit square at origin has area 1."""
    sq = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    assert math.isclose(polygon_area(sq), 1.0)


def test_polygon_area_handles_clockwise_input():
    """Shoelace returns signed area; the function takes ``abs()`` so
    direction shouldn't matter."""
    cw = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0]]  # clockwise
    assert math.isclose(polygon_area(cw), 1.0)


def test_polygon_area_triangle():
    """3-4-5 right triangle: area = (3*4)/2 = 6."""
    tri = [[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]]
    assert math.isclose(polygon_area(tri), 6.0)


# =========================================================================
# pagination.encode_cursor / decode_cursor
# =========================================================================


class _Record:
    """Minimal stand-in for an ORM row with id + created_at."""

    def __init__(self, id_, created_at):
        self.id = id_
        self.created_at = created_at


def test_encode_decode_cursor_round_trip():
    record_id = uuid4()
    created_at = datetime(2026, 4, 26, 12, 30, 0, tzinfo=UTC)
    cursor = encode_cursor(_Record(record_id, created_at))
    decoded = decode_cursor(cursor)
    assert decoded["id"] == str(record_id)
    assert decoded["created_at"] == created_at.isoformat()


def test_decode_cursor_rejects_garbage():
    """Cursor is base64(json), so anything non-base64 / non-json should
    raise rather than silently parse to {}."""
    with pytest.raises(Exception):
        decode_cursor("not-base64!@#")


def test_decode_cursor_rejects_non_json_payload():
    import base64

    bad = base64.urlsafe_b64encode(b"<not-json>").decode()
    with pytest.raises(json.JSONDecodeError):
        decode_cursor(bad)


def test_cursor_format_is_url_safe():
    """The cursor will end up in URLs — base64 needs to be urlsafe
    (no '+'/'/' chars that would need percent-encoding)."""
    record = _Record(uuid4(), datetime.now(UTC))
    cursor = encode_cursor(record)
    # Standard base64 uses + and /; urlsafe uses - and _ instead.
    assert "+" not in cursor
    assert "/" not in cursor


# =========================================================================
# pagination.paginate
# =========================================================================


class _FakeModel:
    """Stand-in for an ORM class: only the attributes paginate touches
    (id and created_at) need to exist. SQLAlchemy operators are passed
    through to the mock query, so we don't need real columns either."""

    id = MagicMock(name="id")
    created_at = MagicMock(name="created_at")


def _build_query_chain():
    """The paginate helper does query.order_by(...).where(...).limit(...).
    Each call returns a new chainable mock."""
    chain = MagicMock()
    chain.order_by.return_value = chain
    chain.where.return_value = chain
    chain.limit.return_value = chain
    return chain


def _make_db_returning(rows: list):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


async def test_paginate_returns_data_and_meta_when_no_cursor():
    rows = [
        _Record(uuid4(), datetime.now(UTC)),
        _Record(uuid4(), datetime.now(UTC)),
    ]
    out = await paginate(
        _make_db_returning(rows),
        _build_query_chain(),
        cursor=None,
        limit=10,
        model=_FakeModel,
    )
    assert out["data"] == rows
    assert out["meta"]["has_more"] is False
    assert out["meta"]["cursor"] is None


async def test_paginate_signals_more_pages_when_extra_row_returned():
    """The helper requests ``limit + 1`` rows; if it gets that many
    back, there's another page and a cursor must be emitted."""
    rows = [
        _Record(uuid4(), datetime(2026, 4, 26, 12, i, 0, tzinfo=UTC))
        for i in range(11)  # 10 + 1 → has_more
    ]
    out = await paginate(
        _make_db_returning(rows),
        _build_query_chain(),
        cursor=None,
        limit=10,
        model=_FakeModel,
    )
    assert len(out["data"]) == 10
    assert out["meta"]["has_more"] is True
    assert out["meta"]["cursor"] is not None
    # Cursor must encode the last row of the data slice (the 10th).
    decoded = decode_cursor(out["meta"]["cursor"])
    assert decoded["id"] == str(rows[9].id)


async def test_paginate_clamps_limit_to_at_least_one():
    out = await paginate(
        _make_db_returning([_Record(uuid4(), datetime.now(UTC))]),
        _build_query_chain(),
        cursor=None,
        limit=0,
        model=_FakeModel,
    )
    # limit=0 normalised to 1 → no error, 1 row returned
    assert len(out["data"]) == 1


async def test_paginate_clamps_limit_to_max_100():
    """The helper caps user-supplied page size at 100 — defensive
    against attackers asking for a 1M-row dump."""
    chain = _build_query_chain()
    db = _make_db_returning([])
    await paginate(db, chain, cursor=None, limit=10_000, model=_FakeModel)
    # The clamp should produce limit+1 = 101 in the query.
    chain.limit.assert_called_once_with(101)


async def test_paginate_decodes_cursor_and_filters_query():
    """When a cursor is provided, the helper must decode it and add a
    keyset ``where`` clause so the page starts AFTER the cursor row.

    Use a model whose attributes are SQLAlchemy ``Column``-style stubs
    that support ``<`` and ``==`` against datetime/UUID values without
    raising — that's the surface ``paginate`` actually exercises."""
    from sqlalchemy.sql import column as sa_column

    class _ColumnModel:
        id = sa_column("id")
        created_at = sa_column("created_at")

    record_id = uuid4()
    created_at = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
    cursor = encode_cursor(_Record(record_id, created_at))
    chain = _build_query_chain()
    db = _make_db_returning([])

    await paginate(db, chain, cursor=cursor, limit=20, model=_ColumnModel)

    chain.where.assert_called_once()  # keyset filter applied
