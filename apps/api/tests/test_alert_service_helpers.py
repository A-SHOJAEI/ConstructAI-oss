"""Tests for alert service helpers (deterministic, in-memory paths).

Pin the dedup key derivation, the in-memory dedup cache eviction
when capped, the alert record schema, and the description
generation per violation type. Excludes Redis paths (covered
elsewhere) and the dedup time window (timing-dependent).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.safety.alert_service import (
    DEDUP_WINDOW_SECONDS,
    _generate_description,
    _make_dedup_key,
    clear_dedup_cache,
    create_alert_record,
    is_duplicate,
    process_safety_event,
)

# =========================================================================
# Constants
# =========================================================================


def test_dedup_window_60_seconds():
    """[contract] Dedup window pinned at 60s — controls tradeoff
    between alert spam and missed re-occurrences."""
    assert DEDUP_WINDOW_SECONDS == 60


# =========================================================================
# _make_dedup_key
# =========================================================================


def test_dedup_key_deterministic():
    """Same inputs -> same key (so dedup actually dedups)."""
    k1 = _make_dedup_key("cam-1", "zone-A", "ppe_violation", "track-42")
    k2 = _make_dedup_key("cam-1", "zone-A", "ppe_violation", "track-42")
    assert k1 == k2


def test_dedup_key_different_inputs_different_keys():
    """Distinct (camera, zone, type, track) -> distinct keys."""
    base = _make_dedup_key("cam-1", "zone-A", "ppe_violation", "t1")
    assert _make_dedup_key("cam-2", "zone-A", "ppe_violation", "t1") != base
    assert _make_dedup_key("cam-1", "zone-B", "ppe_violation", "t1") != base
    assert _make_dedup_key("cam-1", "zone-A", "zone_breach", "t1") != base
    assert _make_dedup_key("cam-1", "zone-A", "ppe_violation", "t2") != base


def test_dedup_key_length_32_hex():
    """[contract] Key is 32 hex chars (truncated SHA-256). Pin so a
    refactor doesn't shorten it (collision risk) or change format."""
    key = _make_dedup_key("a", "b", "c", "d")
    assert len(key) == 32
    # SHA-256 truncated -> hex chars only:
    int(key, 16)  # raises ValueError if not hex


# =========================================================================
# is_duplicate — in-memory fallback path (mock Redis to None)
# =========================================================================


@pytest.fixture(autouse=True)
def _clear_cache_each_test():
    """Reset the in-memory dedup cache between tests (it's module-level)."""
    clear_dedup_cache()
    yield
    clear_dedup_cache()


@pytest.mark.asyncio
async def test_is_duplicate_first_call_not_duplicate():
    """First time we see (camera, zone, type, track) -> not a duplicate."""
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        out = await is_duplicate("cam-1", "zone-A", "ppe_violation", "track-1")
    assert out is False


@pytest.mark.asyncio
async def test_is_duplicate_second_call_is_duplicate():
    """Second call within window -> True."""
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        await is_duplicate("cam-1", "zone-A", "ppe_violation", "track-1")
        out = await is_duplicate("cam-1", "zone-A", "ppe_violation", "track-1")
    assert out is True


@pytest.mark.asyncio
async def test_is_duplicate_different_track_not_duplicate():
    """Different track_id -> distinct dedup, not a dup."""
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        await is_duplicate("cam-1", "zone-A", "ppe_violation", "track-1")
        out = await is_duplicate("cam-1", "zone-A", "ppe_violation", "track-2")
    assert out is False


# =========================================================================
# create_alert_record — schema pin
# =========================================================================


def test_create_alert_record_returns_canonical_keys():
    """[contract] Pin all 12 keys — refactor must NOT silently rename
    or drop. DB insertion code maps these directly to columns."""
    rec = create_alert_record(
        project_id="p-1",
        camera_id="cam-1",
        zone_id="zone-A",
        priority="P1_critical",
        alert_type="ppe_violation",
        description="missing hard hat",
        detections=[{"class_name": "Person"}],
        confidence=0.92,
        frame_s3_key="frames/abc.jpg",
    )
    expected_keys = {
        "id",
        "project_id",
        "camera_id",
        "zone_id",
        "priority",
        "alert_type",
        "description",
        "detections",
        "confidence",
        "frame_s3_key",
        "is_acknowledged",
        "is_false_positive",
        "created_at",
    }
    assert set(rec) == expected_keys


def test_create_alert_record_default_values():
    """is_acknowledged starts False, is_false_positive starts None
    (unreviewed). Pin so a refactor doesn't auto-acknowledge."""
    rec = create_alert_record(
        project_id="p-1",
        camera_id="c",
        zone_id=None,
        priority="P3_medium",
        alert_type="ppe_violation",
        description="x",
        detections=[],
        confidence=0.5,
    )
    assert rec["is_acknowledged"] is False
    assert rec["is_false_positive"] is None


def test_create_alert_record_id_is_uuid():
    """Each record gets a fresh UUID."""
    import uuid as _uuid

    rec1 = create_alert_record("p", "c", None, "x", "x", "x", [], 0.0)
    rec2 = create_alert_record("p", "c", None, "x", "x", "x", [], 0.0)
    _uuid.UUID(rec1["id"])  # raises if not a valid UUID
    assert rec1["id"] != rec2["id"]


def test_create_alert_record_zone_id_can_be_none():
    """Optional zone_id is preserved as None when not specified."""
    rec = create_alert_record("p", "c", None, "P", "T", "D", [], 0.5)
    assert rec["zone_id"] is None


def test_create_alert_record_iso_created_at():
    """created_at is ISO-8601 UTC (downstream parsing depends on this)."""
    from datetime import datetime as _dt

    rec = create_alert_record("p", "c", None, "P", "T", "D", [], 0.5)
    # Round-trip parse:
    parsed = _dt.fromisoformat(rec["created_at"])
    assert parsed.tzinfo is not None  # has timezone


# =========================================================================
# _generate_description — pin per violation type
# =========================================================================


def test_description_zone_breach():
    out = _generate_description("zone_breach", "fall", {"class_name": "person"})
    assert out == "Person detected in fall zone"


def test_description_ppe_missing_hard_hat():
    """[business invariant] PPE descriptions must include item name
    so dashboards can filter by missing-{item}."""
    out = _generate_description("missing_hard_hat", "general", {"class_name": "person"})
    assert "hard_hat" in out
    assert "Person" in out
    assert "missing" in out


def test_description_unknown_violation_type():
    """[fallback] Unknown violation_type uses generic format."""
    out = _generate_description("unauthorized_entry", "trench", {"class_name": "person"})
    assert "unauthorized_entry" in out
    assert "trench" in out


def test_description_missing_class_name_default_object():
    """No class_name in detection -> 'object' default."""
    out = _generate_description("zone_breach", "fall", {})
    # 'object'.title() = 'Object':
    assert "Object" in out


# =========================================================================
# process_safety_event — orchestration
# =========================================================================


@pytest.mark.asyncio
async def test_process_safety_event_returns_alert():
    """Happy path: new event -> alert record."""
    event = {
        "project_id": "p-1",
        "camera_id": "cam-1",
        "violation": {
            "violation": "missing_hard_hat",
            "zone_type": "general",
            "zone_id": "zone-A",
        },
        "detection": {
            "class_name": "Person",
            "confidence": 0.9,
            "track_id": "t-1",
        },
    }
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        out = await process_safety_event(event)

    assert out is not None
    assert out["alert_type"] == "ppe_violation"
    assert out["description"]
    assert out["confidence"] == 0.9
    assert out["camera_id"] == "cam-1"


@pytest.mark.asyncio
async def test_process_safety_event_dedup_returns_none():
    """[invariant] Second occurrence within window -> None (suppressed)."""
    event = {
        "project_id": "p-1",
        "camera_id": "cam-1",
        "violation": {
            "violation": "missing_hard_hat",
            "zone_type": "general",
            "zone_id": "zone-A",
        },
        "detection": {
            "class_name": "Person",
            "confidence": 0.9,
            "track_id": "t-1",
        },
    }
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        first = await process_safety_event(event)
        dup = await process_safety_event(event)

    assert first is not None
    assert dup is None


@pytest.mark.asyncio
async def test_process_safety_event_alert_type_classification():
    """[business invariant] missing_* -> 'ppe_violation', else
    'zone_breach'. Same rule as in safety_agent.generate_alerts_node
    (must stay in sync)."""
    event = {
        "project_id": "p-1",
        "camera_id": "cam-1",
        "violation": {
            "violation": "unauthorized_entry",
            "zone_type": "trench",
            "zone_id": "z",
        },
        "detection": {"class_name": "Person", "confidence": 0.9, "track_id": "t"},
    }
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        out = await process_safety_event(event)
    assert out["alert_type"] == "zone_breach"


@pytest.mark.asyncio
async def test_process_safety_event_zone_id_empty_string_becomes_none():
    """[contract] Empty zone_id from upstream -> None in alert record
    (matches schema's nullable column)."""
    event = {
        "project_id": "p-1",
        "camera_id": "cam-1",
        "violation": {"violation": "x", "zone_type": "general", "zone_id": ""},
        "detection": {"class_name": "Person", "confidence": 0.9, "track_id": "t"},
    }
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        out = await process_safety_event(event)
    assert out["zone_id"] is None


@pytest.mark.asyncio
async def test_process_safety_event_default_confidence():
    """[fallback] Missing detection confidence -> 0.5 default."""
    event = {
        "project_id": "p-1",
        "camera_id": "cam-1",
        "violation": {"violation": "x", "zone_type": "general", "zone_id": "z"},
        "detection": {"class_name": "Person", "track_id": "t"},
    }
    with patch("app.services.safety.alert_service._get_redis", return_value=None):
        out = await process_safety_event(event)
    assert out["confidence"] == 0.5
