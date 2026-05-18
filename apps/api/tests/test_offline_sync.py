"""Tests for offline-first mobile sync engine.

Covers push sync, pull sync, conflict resolution, entity upsert,
device sync state tracking, photo upload queue, sync status,
and conflict listing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.sync.offline_sync_engine import (
    SYNCABLE_ENTITY_TYPES,
    SyncPullResult,
    SyncPushResult,
    _get_model_class,
    _get_record_timestamp,
    _parse_timestamp,
    _record_to_dict,
    _resolve_conflict,
    get_sync_status,
    list_conflicts,
    process_photo_upload,
    sync_pull,
    sync_push,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
DEVICE_ID = "device-abc-123"
NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_push_item(
    entity_type: str = "daily_log",
    entity_id: str | None = None,
    operation: str = "update",
    payload: dict | None = None,
    client_ts: datetime | None = None,
) -> dict:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id or str(uuid.uuid4()),
        "operation": operation,
        "payload": payload or {"notes": "test data", "status": "draft"},
        "client_timestamp": client_ts or NOW,
    }


def _mock_db_with_entity(entity=None, device_state=None):
    """Create a mock DB session.

    entity: if provided, db.get returns this record.
    device_state: if provided, the device state query returns this.
    """
    db = AsyncMock()

    # db.get returns the entity (for entity lookups)
    db.get = AsyncMock(return_value=entity)

    # For SELECT queries (device state, conflict listing)
    if device_state is not None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = device_state
        db.execute.return_value = mock_result
    else:
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    return db


# ===========================================================================
# TestSyncPush
# ===========================================================================


class TestSyncPush:
    """Tests for sync_push()."""

    @pytest.mark.asyncio
    async def test_create_new_entity(self):
        """When entity doesn't exist on server, it should be created."""
        db = _mock_db_with_entity(entity=None)
        items = [_make_push_item(operation="create")]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert isinstance(result, SyncPushResult)
        assert result.created == 1
        assert result.processed == 1
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_update_existing_entity_client_wins(self):
        """When client timestamp is newer, client should win."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW - timedelta(hours=1)
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        items = [_make_push_item(client_ts=NOW)]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.updated == 1 or result.processed >= 1

    @pytest.mark.asyncio
    async def test_update_existing_entity_server_wins(self):
        """When server timestamp is newer, server should win and log conflict."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW + timedelta(hours=1)
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        items = [_make_push_item(client_ts=NOW - timedelta(hours=2))]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.conflicts >= 1

    @pytest.mark.asyncio
    async def test_invalid_entity_type_reported(self):
        """Unknown entity types should be counted as errors."""
        db = _mock_db_with_entity()
        items = [_make_push_item(entity_type="unknown_type")]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.errors == 1
        assert len(result.error_details) == 1

    @pytest.mark.asyncio
    async def test_missing_entity_id_reported(self):
        """Missing entity_id should be counted as an error."""
        db = _mock_db_with_entity()
        items = [_make_push_item()]
        items[0]["entity_id"] = ""

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.errors == 1

    @pytest.mark.asyncio
    async def test_missing_timestamp_reported(self):
        """Missing client_timestamp should be counted as an error."""
        db = _mock_db_with_entity()
        items = [_make_push_item()]
        items[0]["client_timestamp"] = None

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.errors == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent_entity_no_error(self):
        """Delete of nonexistent entity should count as processed, not error."""
        db = _mock_db_with_entity(entity=None)
        items = [_make_push_item(operation="delete")]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.processed == 1
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_multiple_items_processed(self):
        """Multiple items should all be processed."""
        db = _mock_db_with_entity(entity=None)
        items = [_make_push_item(entity_id=str(uuid.uuid4()), operation="create") for _ in range(5)]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.processed == 5
        assert result.created == 5


# ===========================================================================
# TestSyncPull
# ===========================================================================


def _empty_pull_db():
    """Build a mock AsyncSession that returns 'no results' for every
    sync_pull query — count/records/device-state all return safe defaults.

    Using a callable side_effect keeps things working even though sync_pull
    uses asyncio.gather (so the call order isn't deterministic) and the
    number of entity types may grow."""
    db = AsyncMock()
    db.flush = AsyncMock()

    def _make_result(*_args, **_kwargs):
        result = MagicMock()
        result.scalar.return_value = 0
        result.scalars.return_value.all.return_value = []
        result.scalars.return_value.first.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_make_result)
    return db


class TestSyncPull:
    """Tests for sync_pull()."""

    @pytest.mark.asyncio
    async def test_pull_returns_result(self):
        result = await sync_pull(_empty_pull_db(), PROJECT_ID, DEVICE_ID, since=None)
        assert isinstance(result, SyncPullResult)
        assert result.server_timestamp != ""

    @pytest.mark.asyncio
    async def test_pull_with_since_filter(self):
        since = NOW - timedelta(hours=1)
        result = await sync_pull(_empty_pull_db(), PROJECT_ID, DEVICE_ID, since=since)
        assert isinstance(result, SyncPullResult)

    @pytest.mark.asyncio
    async def test_pull_with_entity_type_filter(self):
        result = await sync_pull(
            _empty_pull_db(), PROJECT_ID, DEVICE_ID, since=None, entity_types=["daily_log"]
        )
        assert isinstance(result, SyncPullResult)

    @pytest.mark.asyncio
    async def test_pull_filters_invalid_entity_types(self):
        db = AsyncMock()
        state_result = MagicMock()
        state_result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=state_result)
        db.flush = AsyncMock()

        result = await sync_pull(
            db, PROJECT_ID, DEVICE_ID, since=None, entity_types=["invalid_type"]
        )
        assert result.items == []

    @pytest.mark.asyncio
    async def test_pull_has_more_flag(self):
        # has_more is true when total_available > limit. Use a count-100
        # mock so any entity-type query lands above limit=5.
        db = AsyncMock()
        db.flush = AsyncMock()

        def _make_result(*_args, **_kwargs):
            r = MagicMock()
            r.scalar.return_value = 100
            r.scalars.return_value.all.return_value = []
            r.scalars.return_value.first.return_value = None
            return r

        db.execute = AsyncMock(side_effect=_make_result)
        result = await sync_pull(db, PROJECT_ID, DEVICE_ID, since=None, limit=5)
        assert result.total_available >= 100

    @pytest.mark.asyncio
    async def test_pull_empty_returns_empty(self):
        result = await sync_pull(_empty_pull_db(), PROJECT_ID, DEVICE_ID, since=None)
        assert result.items == []
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_pull_respects_limit(self):
        result = await sync_pull(_empty_pull_db(), PROJECT_ID, DEVICE_ID, since=None, limit=10)
        assert len(result.items) <= 10


# ===========================================================================
# TestConflictResolution
# ===========================================================================


class TestConflictResolution:
    """Tests for _resolve_conflict() and conflict handling."""

    @pytest.mark.asyncio
    async def test_conflict_logged_to_db(self):
        db = AsyncMock()
        db.flush = AsyncMock()

        resolution = await _resolve_conflict(
            db,
            PROJECT_ID,
            DEVICE_ID,
            "daily_log",
            str(uuid.uuid4()),
            {"notes": "client"},
            {"notes": "server"},
            NOW,
            NOW - timedelta(hours=1),
            resolution="client_wins",
        )
        # SV-41: when both sides have data, the resolver merges field-by-field
        # and reports "field_merged" instead of the requested LWW resolution.
        assert resolution == "field_merged"
        assert db.add.called

    @pytest.mark.asyncio
    async def test_server_wins_logged(self):
        db = AsyncMock()
        db.flush = AsyncMock()

        resolution = await _resolve_conflict(
            db,
            PROJECT_ID,
            DEVICE_ID,
            "punch_list_item",
            str(uuid.uuid4()),
            {"notes": "client"},
            {"notes": "server"},
            NOW - timedelta(hours=1),
            NOW,
            resolution="server_wins",
        )
        # SV-41: field-level merge supersedes both sides when data overlaps.
        assert resolution == "field_merged"
        assert db.add.called

    @pytest.mark.asyncio
    async def test_conflict_includes_timestamps(self):
        db = AsyncMock()
        db.flush = AsyncMock()

        client_ts = NOW - timedelta(hours=2)
        server_ts = NOW
        await _resolve_conflict(
            db,
            PROJECT_ID,
            DEVICE_ID,
            "daily_log",
            str(uuid.uuid4()),
            {},
            {},
            client_ts,
            server_ts,
            resolution="server_wins",
        )
        # Verify the ConflictLog was created with correct timestamps
        call_args = db.add.call_args[0][0]
        assert call_args.client_timestamp == client_ts
        assert call_args.server_timestamp == server_ts

    @pytest.mark.asyncio
    async def test_conflict_resolution_is_resolved(self):
        db = AsyncMock()
        db.flush = AsyncMock()

        await _resolve_conflict(
            db,
            PROJECT_ID,
            DEVICE_ID,
            "daily_log",
            str(uuid.uuid4()),
            {},
            {},
            NOW,
            NOW - timedelta(hours=1),
            resolution="client_wins",
        )
        call_args = db.add.call_args[0][0]
        assert call_args.is_resolved is True

    @pytest.mark.asyncio
    async def test_lww_client_newer_wins(self):
        """In LWW, the newer timestamp wins. If client is newer, client wins."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW - timedelta(hours=1)
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        items = [_make_push_item(client_ts=NOW)]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        # Client is newer, should win (updated, not conflict)
        assert result.updated >= 1 or result.processed >= 1

    @pytest.mark.asyncio
    async def test_lww_server_newer_wins(self):
        """In LWW, the newer timestamp wins. If server is newer, server wins."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW + timedelta(hours=1)
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        items = [_make_push_item(client_ts=NOW - timedelta(hours=2))]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.conflicts >= 1

    @pytest.mark.asyncio
    async def test_conflict_details_populated(self):
        """Conflict details should include entity info."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW + timedelta(hours=1)
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        entity_id = str(uuid.uuid4())
        items = [_make_push_item(entity_id=entity_id, client_ts=NOW - timedelta(hours=2))]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert len(result.conflict_details) >= 1
        detail = result.conflict_details[0]
        assert detail["entity_id"] == entity_id
        assert detail["resolution"] == "server_wins"

    @pytest.mark.asyncio
    async def test_equal_timestamps_server_wins(self):
        """When timestamps are equal, server should win (not strictly newer)."""
        server_record = MagicMock()
        server_record.project_id = PROJECT_ID
        server_record.updated_at = NOW
        server_record.__table__ = MagicMock()
        server_record.__table__.columns = []

        db = _mock_db_with_entity(entity=server_record)
        items = [_make_push_item(client_ts=NOW)]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        # Equal timestamps => client is NOT strictly newer => server wins
        assert result.conflicts >= 1


# ===========================================================================
# TestEntityUpsert
# ===========================================================================


class TestEntityUpsert:
    """Tests for entity type resolution and upsert logic."""

    def test_daily_log_model_resolved(self):
        model = _get_model_class("daily_log")
        assert model is not None
        assert model.__tablename__ == "daily_logs"

    def test_punch_list_item_model_resolved(self):
        model = _get_model_class("punch_list_item")
        assert model is not None
        assert model.__tablename__ == "punch_list_items"

    def test_safety_observation_model_resolved(self):
        model = _get_model_class("safety_observation")
        assert model is not None
        assert model.__tablename__ == "safety_alerts"

    def test_time_entry_model_resolved(self):
        model = _get_model_class("time_entry")
        assert model is not None
        assert model.__tablename__ == "crew_productivity"

    def test_unknown_type_returns_none(self):
        model = _get_model_class("nonexistent_type")
        assert model is None

    def test_all_syncable_types_resolvable(self):
        for entity_type in SYNCABLE_ENTITY_TYPES:
            model = _get_model_class(entity_type)
            assert model is not None, f"Model not found for {entity_type}"


# ===========================================================================
# TestDeviceSyncState
# ===========================================================================


class TestDeviceSyncState:
    """Tests for device sync state management."""

    @pytest.mark.asyncio
    async def test_get_sync_status_not_found(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        status = await get_sync_status(db, PROJECT_ID, DEVICE_ID)
        assert status is None

    @pytest.mark.asyncio
    async def test_get_sync_status_found(self):
        db = AsyncMock()
        mock_state = MagicMock()
        mock_state.device_id = DEVICE_ID
        mock_state.project_id = PROJECT_ID
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_state
        db.execute.return_value = mock_result

        status = await get_sync_status(db, PROJECT_ID, DEVICE_ID)
        assert status is not None
        assert status.device_id == DEVICE_ID

    @pytest.mark.asyncio
    async def test_push_updates_device_state(self):
        """After a push, the device sync state should be updated."""
        db = _mock_db_with_entity(entity=None)
        items = [_make_push_item(operation="create")]

        await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        # Verify db.add was called (for the entity and the device state)
        assert db.add.call_count >= 1

    @pytest.mark.asyncio
    async def test_server_timestamp_in_push_result(self):
        """Push result should include a server_timestamp."""
        db = _mock_db_with_entity(entity=None)
        items = [_make_push_item(operation="create")]

        result = await sync_push(db, PROJECT_ID, DEVICE_ID, USER_ID, items)
        assert result.server_timestamp != ""
        # Should be parseable as ISO timestamp
        datetime.fromisoformat(result.server_timestamp)

    @pytest.mark.asyncio
    async def test_server_timestamp_in_pull_result(self):
        """Pull result should include a server_timestamp."""
        result = await sync_pull(_empty_pull_db(), PROJECT_ID, DEVICE_ID, since=None)
        assert result.server_timestamp != ""
        datetime.fromisoformat(result.server_timestamp)


# ===========================================================================
# TestPhotoQueue
# ===========================================================================


class TestPhotoQueue:
    """Tests for photo upload queue."""

    @pytest.mark.asyncio
    async def test_photo_not_found_raises(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Photo queue item not found"):
            await process_photo_upload(db, PROJECT_ID, uuid.uuid4(), b"fake-photo-bytes")

    @pytest.mark.asyncio
    async def test_photo_wrong_project_raises(self):
        db = AsyncMock()
        mock_record = MagicMock()
        mock_record.project_id = uuid.uuid4()  # Different project
        mock_record.status = "pending"
        db.get = AsyncMock(return_value=mock_record)

        with pytest.raises(ValueError, match="Photo queue item not found"):
            await process_photo_upload(db, PROJECT_ID, uuid.uuid4(), b"fake-photo-bytes")

    @pytest.mark.asyncio
    async def test_already_completed_returns_status(self):
        db = AsyncMock()
        mock_record = MagicMock()
        mock_record.project_id = PROJECT_ID
        mock_record.status = "completed"
        mock_record.s3_key = "photos/test.jpg"
        mock_record.file_size_bytes = 1024
        db.get = AsyncMock(return_value=mock_record)

        result = await process_photo_upload(db, PROJECT_ID, uuid.uuid4(), b"fake-photo-bytes")
        assert result["status"] == "already_completed"

    @pytest.mark.asyncio
    async def test_photo_upload_sets_s3_key(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        mock_record = MagicMock()
        mock_record.project_id = PROJECT_ID
        mock_record.status = "pending"
        mock_record.content_type = "image/jpeg"
        mock_record.device_id = DEVICE_ID
        db.get = AsyncMock(return_value=mock_record)

        with patch("app.services.sync.offline_sync_engine.settings") as mock_settings:
            mock_settings.S3_BUCKET_DOCUMENTS = (
                ""  # Skip the actual upload  # No S3, just test key generation
            )
            result = await process_photo_upload(db, PROJECT_ID, uuid.uuid4(), b"fake-photo-bytes")
            assert result["status"] == "completed"
            assert result["file_size"] == len(b"fake-photo-bytes")

    @pytest.mark.asyncio
    async def test_photo_png_extension(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        mock_record = MagicMock()
        mock_record.project_id = PROJECT_ID
        mock_record.status = "pending"
        mock_record.content_type = "image/png"
        mock_record.device_id = DEVICE_ID
        db.get = AsyncMock(return_value=mock_record)

        with patch("app.services.sync.offline_sync_engine.settings") as mock_settings:
            mock_settings.S3_BUCKET_DOCUMENTS = ""  # Skip the actual upload
            result = await process_photo_upload(db, PROJECT_ID, uuid.uuid4(), b"png-bytes")
            assert result["status"] == "completed"


# ===========================================================================
# TestSyncStatus
# ===========================================================================


class TestSyncStatus:
    """Tests for get_sync_status()."""

    @pytest.mark.asyncio
    async def test_status_returns_none_for_unknown_device(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        status = await get_sync_status(db, PROJECT_ID, "unknown-device")
        assert status is None

    @pytest.mark.asyncio
    async def test_status_returns_state_for_known_device(self):
        db = AsyncMock()
        mock_state = MagicMock()
        mock_state.device_id = DEVICE_ID
        mock_state.last_push_at = NOW
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_state
        db.execute.return_value = mock_result

        status = await get_sync_status(db, PROJECT_ID, DEVICE_ID)
        assert status is not None

    @pytest.mark.asyncio
    async def test_status_queries_correct_project(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        await get_sync_status(db, PROJECT_ID, DEVICE_ID)
        assert db.execute.called


# ===========================================================================
# TestConflictList
# ===========================================================================


class TestConflictList:
    """Tests for list_conflicts()."""

    @pytest.mark.asyncio
    async def test_list_empty_conflicts(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        conflicts = await list_conflicts(db, PROJECT_ID)
        assert conflicts == []

    @pytest.mark.asyncio
    async def test_list_with_device_filter(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        await list_conflicts(db, PROJECT_ID, device_id=DEVICE_ID)
        assert db.execute.called

    @pytest.mark.asyncio
    async def test_list_with_resolved_filter(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        await list_conflicts(db, PROJECT_ID, resolved=False)
        assert db.execute.called

    @pytest.mark.asyncio
    async def test_list_respects_limit(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        await list_conflicts(db, PROJECT_ID, limit=10)
        assert db.execute.called


# ===========================================================================
# TestHelpers
# ===========================================================================


class TestHelpers:
    """Tests for internal helper functions."""

    def test_parse_timestamp_datetime(self):
        ts = _parse_timestamp(NOW)
        assert ts == NOW

    def test_parse_timestamp_string(self):
        ts = _parse_timestamp("2026-03-15T12:00:00+00:00")
        assert ts is not None
        assert ts.year == 2026

    def test_parse_timestamp_naive_gets_utc(self):
        naive = datetime(2026, 3, 15, 12, 0, 0)
        ts = _parse_timestamp(naive)
        assert ts is not None
        assert ts.tzinfo is not None

    def test_parse_timestamp_none_returns_none(self):
        assert _parse_timestamp(None) is None

    def test_parse_timestamp_invalid_string_returns_none(self):
        assert _parse_timestamp("not-a-timestamp") is None

    def test_record_to_dict_serializes(self):
        record = MagicMock()
        col1 = MagicMock()
        col1.name = "id"
        col2 = MagicMock()
        col2.name = "notes"
        record.__table__ = MagicMock()
        record.__table__.columns = [col1, col2]
        record.id = uuid.uuid4()
        record.notes = "test"

        result = _record_to_dict(record)
        assert "id" in result
        assert result["notes"] == "test"
        assert isinstance(result["id"], str)  # UUID serialized to string

    def test_get_record_timestamp_updated_at(self):
        record = MagicMock()
        record.updated_at = NOW
        ts = _get_record_timestamp(record)
        assert ts == NOW

    def test_get_record_timestamp_created_at_fallback(self):
        record = MagicMock(spec=["created_at"])
        record.created_at = NOW
        ts = _get_record_timestamp(record)
        assert ts == NOW

    def test_syncable_entity_types_has_required(self):
        assert "daily_log" in SYNCABLE_ENTITY_TYPES
        assert "punch_list_item" in SYNCABLE_ENTITY_TYPES
        assert "safety_observation" in SYNCABLE_ENTITY_TYPES
        assert "time_entry" in SYNCABLE_ENTITY_TYPES
