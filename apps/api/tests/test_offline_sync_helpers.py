"""Tests for the pure helpers in services/sync/offline_sync_engine.

The full sync engine has DB-bound push/pull logic that needs an
integration harness; these tests pin the pure parts:

- ``_parse_timestamp`` — robust parsing of ISO strings, naive
  datetimes, None.
- ``_get_record_timestamp`` — extracts updated_at / created_at /
  fallback.
- ``_record_to_dict`` — type coercion (datetime → ISO, UUID → str,
  Decimal → float).
- ``_field_level_merge`` — the 4-way merge (no-conflict, client-only,
  server-only, both-changed-LWW).
- Module constants: SYNCABLE_ENTITY_TYPES, MAX_PUSH_ITEMS,
  MAX_PULL_ITEMS, _ENTITY_WRITABLE_FIELDS.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

from app.services.sync.offline_sync_engine import (
    _ENTITY_WRITABLE_FIELDS,
    MAX_PULL_ITEMS,
    MAX_PUSH_ITEMS,
    SYNCABLE_ENTITY_TYPES,
    _field_level_merge,
    _get_record_timestamp,
    _parse_timestamp,
    _record_to_dict,
)

# =========================================================================
# Module constants
# =========================================================================


def test_syncable_entity_types_canonical():
    """Pin the documented syncable types so a refactor doesn't quietly
    drop one (clients send the entity_type string and rely on the
    server accepting it)."""
    expected = {
        "daily_log",
        "punch_list_item",
        "safety_observation",
        "time_entry",
        "rfi",
        "inspection",
        "equipment_log",
    }
    assert set(SYNCABLE_ENTITY_TYPES.keys()) == expected


def test_writable_fields_one_per_entity_type():
    """Every syncable entity must have a writable-fields allowlist —
    otherwise the mass-assignment guard breaks."""
    for entity_type in SYNCABLE_ENTITY_TYPES:
        assert entity_type in _ENTITY_WRITABLE_FIELDS, (
            f"missing writable allowlist for {entity_type}"
        )
        assert _ENTITY_WRITABLE_FIELDS[entity_type], "empty allowlist would block all writes"


def test_writable_fields_does_not_include_server_managed_columns():
    """[security] No allowlist may permit clients to overwrite
    server-managed columns: created_by, data_source, procore_id, id,
    organization_id, project_id."""
    forbidden = {
        "created_by",
        "data_source",
        "procore_id",
        "id",
        "organization_id",
        "project_id",
        "tenant_id",
    }
    for entity_type, fields in _ENTITY_WRITABLE_FIELDS.items():
        leaked = forbidden & fields
        assert not leaked, f"{entity_type} allows writing forbidden cols: {leaked}"


def test_push_pull_caps_sane():
    """Caps must be present and reasonable to prevent unbounded sync
    payloads from exhausting memory."""
    assert 0 < MAX_PUSH_ITEMS <= 1000
    assert 0 < MAX_PULL_ITEMS <= 5000
    assert MAX_PULL_ITEMS >= MAX_PUSH_ITEMS  # pull >= push for catch-up


# =========================================================================
# _parse_timestamp
# =========================================================================


def test_parse_timestamp_none_returns_none():
    assert _parse_timestamp(None) is None


def test_parse_timestamp_iso_string_with_tz():
    out = _parse_timestamp("2026-04-25T12:00:00+00:00")
    assert out == datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def test_parse_timestamp_iso_string_naive_assumes_utc():
    """Naive ISO strings — no tz info — must be coerced to UTC, not
    interpreted in local time. Otherwise sync timestamps would drift
    by the device's offset."""
    out = _parse_timestamp("2026-04-25T12:00:00")
    assert out is not None
    assert out.tzinfo == UTC


def test_parse_timestamp_datetime_naive_coerced_to_utc():
    out = _parse_timestamp(datetime(2026, 4, 25, 12, 0))
    assert out == datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def test_parse_timestamp_datetime_with_tz_preserved():
    """Non-UTC tz must be preserved as-is — caller may have explicit
    intent."""
    pacific = timezone(timedelta(hours=-8))
    dt = datetime(2026, 4, 25, 12, 0, tzinfo=pacific)
    out = _parse_timestamp(dt)
    assert out == dt


def test_parse_timestamp_garbage_string_returns_none():
    assert _parse_timestamp("not-a-date") is None
    assert _parse_timestamp("2026-13-99T99:99:99") is None


def test_parse_timestamp_unsupported_type_returns_none():
    assert _parse_timestamp(12345) is None
    assert _parse_timestamp([2026, 4, 25]) is None


# =========================================================================
# _get_record_timestamp
# =========================================================================


def test_get_record_timestamp_prefers_updated_at():
    class Rec:
        updated_at = datetime(2026, 4, 25, tzinfo=UTC)
        created_at = datetime(2020, 1, 1, tzinfo=UTC)

    assert _get_record_timestamp(Rec()) == datetime(2026, 4, 25, tzinfo=UTC)


def test_get_record_timestamp_falls_back_to_created_at():
    class Rec:
        updated_at = None
        created_at = datetime(2020, 1, 1, tzinfo=UTC)

    assert _get_record_timestamp(Rec()) == datetime(2020, 1, 1, tzinfo=UTC)


def test_get_record_timestamp_returns_min_when_neither():
    class Rec:
        pass

    out = _get_record_timestamp(Rec())
    # Returns datetime.min in UTC — pin that the fallback is not None.
    assert out is not None
    assert out.tzinfo == UTC


# =========================================================================
# _record_to_dict — type coercion
# =========================================================================


class _FakeColumn:
    def __init__(self, name: str):
        self.name = name


class _FakeTable:
    def __init__(self, *cols: str):
        self.columns = [_FakeColumn(c) for c in cols]


class _FakeRecord:
    """ORM-shaped: has __table__.columns, attributes for each column."""

    def __init__(self, **values):
        self.__table__ = _FakeTable(*values.keys())
        for k, v in values.items():
            setattr(self, k, v)


def test_record_to_dict_coerces_datetime_to_iso():
    rec = _FakeRecord(id="x", updated_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC))
    out = _record_to_dict(rec)
    assert out["updated_at"] == "2026-04-25T12:00:00+00:00"


def test_record_to_dict_coerces_uuid_to_str():
    project_id = uuid.uuid4()
    rec = _FakeRecord(project_id=project_id)
    out = _record_to_dict(rec)
    assert out["project_id"] == str(project_id)
    assert isinstance(out["project_id"], str)


def test_record_to_dict_coerces_decimal_via_float():
    """SQLAlchemy Decimal must round-trip via float (JSON-safe)."""
    from decimal import Decimal

    rec = _FakeRecord(cost=Decimal("123.45"))
    out = _record_to_dict(rec)
    assert isinstance(out["cost"], float)
    assert out["cost"] == 123.45


def test_record_to_dict_preserves_none_and_strings():
    rec = _FakeRecord(name="alice", note=None, count=42)
    out = _record_to_dict(rec)
    assert out["name"] == "alice"
    assert out["note"] is None
    assert out["count"] == 42


# =========================================================================
# _field_level_merge — SV-41
# =========================================================================


def _ts_old() -> datetime:
    return datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _ts_new() -> datetime:
    return datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def test_field_level_merge_no_conflict_returns_consistent():
    """When client and server agree on every field, output equals
    either side."""
    client = {"name": "alice", "note": "x"}
    server = {"name": "alice", "note": "x"}
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is not None
    assert out["name"] == "alice"
    assert out["note"] == "x"
    # No merge_log entry (no conflicts) → key absent.
    assert "_merge_log" not in out


def test_field_level_merge_client_only_field():
    """Client added a new field that server doesn't have — keep client value."""
    client = {"name": "alice", "newly_added": "from-client"}
    server = {"name": "alice"}
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is not None
    assert out["newly_added"] == "from-client"
    assert any("client_added" in m for m in out["_merge_log"])


def test_field_level_merge_server_only_field():
    """Server added a field client doesn't know about — keep server value."""
    client = {"name": "alice"}
    server = {"name": "alice", "server_added": "from-server"}
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is not None
    assert out["server_added"] == "from-server"
    assert any("server_added" in m for m in out["_merge_log"])


def test_field_level_merge_both_changed_lww_client_wins_when_newer():
    """Both client and server modified the same field. Client timestamp
    is newer → client wins LWW."""
    client = {"name": "alice", "status": "in_progress"}
    server = {"name": "alice", "status": "open"}
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is not None
    assert out["status"] == "in_progress"
    assert any("client_wins_lww" in m for m in out["_merge_log"])


def test_field_level_merge_both_changed_lww_server_wins_when_newer():
    client = {"name": "alice", "status": "in_progress"}
    server = {"name": "alice", "status": "completed"}
    # Server timestamp is newer:
    out = _field_level_merge(client, server, _ts_old(), _ts_new())
    assert out is not None
    assert out["status"] == "completed"
    assert any("server_wins_lww" in m for m in out["_merge_log"])


def test_field_level_merge_skips_managed_fields():
    """Merge must NOT include id, project_id, created_at, updated_at,
    or _photo_refs — those are server-managed."""
    client = {
        "id": "client-id",
        "project_id": "client-pid",
        "created_at": "1970-01-01",
        "updated_at": "2026-04-25",
        "_photo_refs": ["client-photo"],
        "name": "alice",
    }
    server = {
        "id": "server-id",
        "project_id": "server-pid",
        "created_at": "1970-01-01",
        "updated_at": "2026-04-24",
        "_photo_refs": ["server-photo"],
        "name": "alice",
    }
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is not None
    for skip in ("id", "project_id", "created_at", "updated_at", "_photo_refs"):
        assert skip not in out


def test_field_level_merge_only_managed_fields_returns_none():
    """If both sides only differ in skipped fields, there's nothing
    meaningful to merge — return None."""
    client = {"id": "x", "project_id": "p", "updated_at": "2026-04-25"}
    server = {"id": "y", "project_id": "p", "updated_at": "2026-04-24"}
    out = _field_level_merge(client, server, _ts_new(), _ts_old())
    assert out is None


def test_field_level_merge_equal_timestamps_client_wins():
    """When timestamps are exactly equal (>= comparison favors client)."""
    client = {"x": "client-val"}
    server = {"x": "server-val"}
    same_ts = _ts_new()
    out = _field_level_merge(client, server, same_ts, same_ts)
    assert out is not None
    assert out["x"] == "client-val"
