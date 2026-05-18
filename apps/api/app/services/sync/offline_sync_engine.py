"""Offline-first mobile sync engine.

Handles bidirectional sync between mobile devices and the server.
Uses Last-Writer-Wins (LWW) conflict resolution with full conflict
audit logging. Supports deferred photo uploads.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Syncable entity types → table/model mapping
# ---------------------------------------------------------------------------

SYNCABLE_ENTITY_TYPES: dict[str, str] = {
    "daily_log": "daily_logs",
    "punch_list_item": "punch_list_items",
    "safety_observation": "safety_alerts",
    "time_entry": "crew_productivity",
    # SV-40: Additional entity types for broader offline sync coverage
    "rfi": "rfis",
    "inspection": "inspections",
    "equipment_log": "equipment",
}

# Max items in a single push request
MAX_PUSH_ITEMS = 200

# Max items in a single pull response
MAX_PULL_ITEMS = 500

# Per-entity-type allowlist of fields that clients may write via sync.
# Any field NOT in this set for a given entity type will be silently
# rejected (and logged as a warning) to prevent mass-assignment of
# server-managed columns (e.g. created_by, data_source, procore_id).
_ENTITY_WRITABLE_FIELDS: dict[str, set[str]] = {
    "daily_log": {
        "log_date",
        "weather",
        "temperature_high",
        "temperature_low",
        "notes",
        "manpower_by_trade",
        "activities_performed",
        "delays",
        "visitors",
    },
    "punch_list_item": {
        "description",
        "location",
        "status",
        "priority",
        "due_date",
        "notes",
        "photo_url",
    },
    "safety_observation": {
        "observation_type",
        "description",
        "severity",
        "location",
        "corrective_action",
        "notes",
    },
    "time_entry": {
        "worker_id",
        "trade",
        "hours_regular",
        "hours_overtime",
        "date",
        "notes",
    },
    "rfi": {
        "subject",
        "question",
        "priority",
        "due_date",
        "spec_section",
        "notes",
    },
    "inspection": {
        "inspection_type",
        "location",
        "status",
        "findings",
        "notes",
    },
    "equipment_log": {
        "status",
        "hours_operated",
        "fuel_consumed",
        "notes",
        "location",
    },
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SyncPushResult:
    """Result of processing a push request from a mobile device."""

    processed: int = 0
    created: int = 0
    updated: int = 0
    conflicts: int = 0
    errors: int = 0
    conflict_details: list[dict] = field(default_factory=list)
    error_details: list[dict] = field(default_factory=list)
    server_timestamp: str = ""


@dataclass
class SyncPullResult:
    """Result of a pull request to the server."""

    items: list[dict] = field(default_factory=list)
    server_timestamp: str = ""
    has_more: bool = False
    total_available: int = 0
    compressed: bool = False  # SV-42: True if items are gzip-compressed
    compressed_item_count: int = 0  # Number of items in the compressed payload


# ---------------------------------------------------------------------------
# Core: Push (client → server)
# ---------------------------------------------------------------------------


async def sync_push(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
    user_id: uuid.UUID,
    items: list[dict],
) -> SyncPushResult:
    """Process a batch of items pushed from a mobile device.

    For each item:
    - If entity_id does not exist on server: create.
    - If entity_id exists: compare timestamps (LWW).
      - client newer → update server.
      - server newer → server wins, log conflict.
    - Queue any photo references for deferred upload.
    - Log all conflicts.

    Each item dict must contain:
      entity_type, entity_id, operation (create|update|delete),
      payload (dict), client_timestamp (ISO string or datetime)
    """

    now = datetime.now(UTC)
    result = SyncPushResult(server_timestamp=now.isoformat())

    for item in items:
        entity_type = item.get("entity_type")
        entity_id = item.get("entity_id")
        operation = item.get("operation", "update")
        payload = item.get("payload", {})
        client_ts_raw = item.get("client_timestamp")

        # Validate
        if entity_type not in SYNCABLE_ENTITY_TYPES:
            result.errors += 1
            result.error_details.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error": f"Unsupported entity type: {entity_type}",
                }
            )
            continue

        if not entity_id:
            result.errors += 1
            result.error_details.append(
                {
                    "entity_type": entity_type,
                    "error": "Missing entity_id",
                }
            )
            continue

        # Parse client timestamp
        client_ts = _parse_timestamp(client_ts_raw)
        if client_ts is None:
            result.errors += 1
            result.error_details.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error": "Invalid or missing client_timestamp",
                }
            )
            continue

        try:
            # Check if entity exists on server
            server_record = await _get_entity_record(db, entity_type, entity_id, project_id)

            if server_record is None:
                # Entity does not exist: create
                if operation == "delete":
                    # Nothing to delete
                    result.processed += 1
                    continue
                await _apply_entity_upsert(db, entity_type, entity_id, payload, project_id, user_id)
                result.created += 1
            else:
                # Entity exists: resolve conflict
                server_ts = _get_record_timestamp(server_record)
                server_data = _record_to_dict(server_record)

                if operation == "delete":
                    # Client wants to delete — treat as client_wins if newer
                    if client_ts > server_ts:
                        await _apply_entity_delete(db, entity_type, entity_id, project_id)
                        await _resolve_conflict(
                            db,
                            project_id,
                            device_id,
                            entity_type,
                            entity_id,
                            payload,
                            server_data,
                            client_ts,
                            server_ts,
                            resolution="client_wins",
                        )
                        result.updated += 1
                    else:
                        await _resolve_conflict(
                            db,
                            project_id,
                            device_id,
                            entity_type,
                            entity_id,
                            payload,
                            server_data,
                            client_ts,
                            server_ts,
                            resolution="server_wins",
                        )
                        result.conflicts += 1
                        result.conflict_details.append(
                            {
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                "resolution": "server_wins",
                            }
                        )
                elif client_ts > server_ts:
                    # Client is newer → client wins
                    await _apply_entity_upsert(
                        db, entity_type, entity_id, payload, project_id, user_id
                    )
                    if server_data != payload:
                        await _resolve_conflict(
                            db,
                            project_id,
                            device_id,
                            entity_type,
                            entity_id,
                            payload,
                            server_data,
                            client_ts,
                            server_ts,
                            resolution="client_wins",
                        )
                    result.updated += 1
                else:
                    # Server is newer → server wins
                    await _resolve_conflict(
                        db,
                        project_id,
                        device_id,
                        entity_type,
                        entity_id,
                        payload,
                        server_data,
                        client_ts,
                        server_ts,
                        resolution="server_wins",
                    )
                    result.conflicts += 1
                    result.conflict_details.append(
                        {
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "resolution": "server_wins",
                        }
                    )

            result.processed += 1

            # Queue photos if present in payload
            photo_refs = payload.get("_photo_refs", [])
            if photo_refs:
                await _queue_photos(
                    db,
                    project_id,
                    device_id,
                    user_id,
                    entity_type,
                    entity_id,
                    photo_refs,
                    client_ts,
                )

        except Exception as exc:
            logger.error("Sync push error for %s/%s: %s", entity_type, entity_id, exc)
            result.errors += 1
            result.error_details.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error": str(exc),
                }
            )

    # Update device sync state
    await _update_device_state(db, project_id, device_id, user_id, push_at=now, server_ts=now)

    await db.flush()

    logger.info(
        "Sync push from device %s: %d processed, %d created, %d updated, %d conflicts, %d errors",
        device_id,
        result.processed,
        result.created,
        result.updated,
        result.conflicts,
        result.errors,
    )
    return result


# ---------------------------------------------------------------------------
# Core: Pull (server → client)
# ---------------------------------------------------------------------------


async def sync_pull(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
    since: datetime | None,
    entity_types: list[str] | None = None,
    limit: int = MAX_PULL_ITEMS,
) -> SyncPullResult:
    """Pull updated records from the server since the given timestamp.

    Queries each syncable entity table for records updated after `since`.
    Returns items + new server_timestamp + has_more flag.
    Updates DeviceSyncState.
    """
    now = datetime.now(UTC)
    result = SyncPullResult(server_timestamp=now.isoformat())

    types_to_query = entity_types or list(SYNCABLE_ENTITY_TYPES.keys())
    # Filter to valid types only
    types_to_query = [t for t in types_to_query if t in SYNCABLE_ENTITY_TYPES]

    # --- Parallel count queries across entity types ---
    async def _count_entity_type(etype: str) -> tuple[str, int]:
        model = _get_model_class(etype)
        if model is None:
            return etype, 0
        query = select(model).where(model.project_id == project_id)
        if since is not None:
            ts_col = getattr(model, "updated_at", None) or getattr(model, "created_at", None)
            if ts_col is not None:
                query = query.where(ts_col > since)
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await db.execute(count_query)
        return etype, count_result.scalar() or 0

    count_tasks = [_count_entity_type(et) for et in types_to_query]
    count_results = await asyncio.gather(*count_tasks)
    type_counts = dict(count_results)
    total_available = sum(type_counts.values())

    # --- Parallel data fetch across entity types ---
    # Distribute limit proportionally to each entity type
    per_type_limit = max(1, limit // max(len(types_to_query), 1))

    async def _fetch_entity_type(etype: str, fetch_limit: int) -> list[dict]:
        model = _get_model_class(etype)
        if model is None:
            return []
        query = select(model).where(model.project_id == project_id)
        if since is not None:
            ts_col = getattr(model, "updated_at", None) or getattr(model, "created_at", None)
            if ts_col is not None:
                query = query.where(ts_col > since)
        ts_col = getattr(model, "updated_at", None) or getattr(model, "created_at", None)
        if ts_col is not None:
            query = query.order_by(ts_col)
        query = query.limit(fetch_limit)
        records_result = await db.execute(query)
        records = list(records_result.scalars().all())
        items = []
        for record in records:
            record_dict = _record_to_dict(record)
            record_dict["_entity_type"] = etype
            record_dict["_entity_id"] = str(record.id)
            items.append(record_dict)
        return items

    fetch_tasks = [
        _fetch_entity_type(et, per_type_limit)
        for et in types_to_query
        if type_counts.get(et, 0) > 0
    ]
    fetch_results = await asyncio.gather(*fetch_tasks)

    all_items: list[dict] = []
    for items in fetch_results:
        all_items.extend(items)
        if len(all_items) >= limit:
            all_items = all_items[:limit]
            break

    result.items = all_items
    result.total_available = total_available
    result.has_more = total_available > len(all_items)

    # SV-42: Gzip compression for large responses (>100KB)
    import base64 as _base64
    import gzip as _gzip
    import json as _json

    if all_items:
        try:
            raw_json = _json.dumps(all_items, default=str).encode("utf-8")
            if len(raw_json) > 100_000:  # 100KB threshold
                compressed = _gzip.compress(raw_json, compresslevel=6)
                item_count = len(all_items)
                # Store compressed data as base64 string in a single-element list
                result.items = [{"_compressed": _base64.b64encode(compressed).decode("ascii")}]
                result.compressed = True
                result.compressed_item_count = item_count
                logger.info(
                    "Sync pull response compressed: %d -> %d bytes (%.0f%% reduction), %d items",
                    len(raw_json),
                    len(compressed),
                    (1 - len(compressed) / len(raw_json)) * 100,
                    item_count,
                )
        except Exception as exc:
            logger.debug("Response compression failed (returning uncompressed): %s", exc)

    # Update device sync state

    device_state = await _get_or_create_device_state(db, project_id, device_id)
    if device_state:
        device_state.last_pull_at = now
        device_state.last_server_timestamp = now
        device_state.updated_at = now

    await db.flush()

    logger.info(
        "Sync pull for device %s: %d items returned, %d total available, has_more=%s",
        device_id,
        len(all_items),
        total_available,
        result.has_more,
    )
    return result


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


async def _resolve_conflict(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
    entity_type: str,
    entity_id: str,
    client_data: dict,
    server_data: dict,
    client_ts: datetime,
    server_ts: datetime,
    resolution: str = "server_wins",
) -> str:
    """Log a conflict to the conflict_logs table.

    SV-41: When both client and server modified the same record, performs
    field-level merge instead of whole-record LWW:
    - Fields only the client changed -> use client value
    - Fields only the server changed -> use server value
    - Fields both changed -> LWW on that specific field

    Returns the resolution string.
    """
    from app.models.offline_sync import ConflictLog

    # SV-41: Field-level merge when both sides have data and it's not a delete
    merged_data = None
    if client_data and server_data and resolution in ("client_wins", "server_wins"):
        merged_data = _field_level_merge(client_data, server_data, client_ts, server_ts)
        if merged_data is not None:
            resolution = "field_merged"
            # Apply the merged data to the entity
            try:
                await _apply_entity_upsert(db, entity_type, entity_id, merged_data, project_id)
            except Exception as exc:
                logger.warning(
                    "Field-level merge apply failed for %s/%s: %s, falling back to LWW",
                    entity_type,
                    entity_id,
                    exc,
                )
                merged_data = None
                resolution = "client_wins" if client_ts > server_ts else "server_wins"

    conflict_server_data = server_data
    if merged_data is not None:
        # Store merged result in the conflict log for auditability
        conflict_server_data = {
            "_original_server": server_data,
            "_merged_result": merged_data,
        }

    conflict = ConflictLog(
        project_id=project_id,
        device_id=device_id,
        entity_type=entity_type,
        entity_id=entity_id,
        client_data=client_data,
        server_data=conflict_server_data,
        client_timestamp=client_ts,
        server_timestamp=server_ts,
        resolution=resolution,
        is_resolved=True,
        resolved_at=datetime.now(UTC),
    )
    db.add(conflict)

    logger.debug(
        "Conflict logged: %s/%s resolution=%s (client_ts=%s, server_ts=%s)",
        entity_type,
        entity_id,
        resolution,
        client_ts,
        server_ts,
    )
    return resolution


def _field_level_merge(
    client_data: dict,
    server_data: dict,
    client_ts: datetime,
    server_ts: datetime,
) -> dict | None:
    """SV-41: Merge two versions of a record field-by-field.

    Compares each field:
    - Fields only client changed -> client value
    - Fields only server changed -> server value
    - Fields both changed -> LWW on that specific field

    Returns the merged dict, or None if field-level merge is not applicable.
    """
    _SKIP_FIELDS = {"id", "project_id", "created_at", "updated_at", "_photo_refs"}

    all_keys = (set(client_data.keys()) | set(server_data.keys())) - _SKIP_FIELDS

    if not all_keys:
        return None

    merged: dict = {}
    merge_log: list[str] = []

    for key in all_keys:
        client_val = client_data.get(key)
        server_val = server_data.get(key)

        if client_val == server_val:
            # No conflict on this field
            merged[key] = client_val
        elif key not in server_data:
            # Client added this field
            merged[key] = client_val
            merge_log.append(f"{key}: client_added")
        elif key not in client_data:
            # Server added this field
            merged[key] = server_val
            merge_log.append(f"{key}: server_added")
        else:
            # Both changed this field — LWW on this specific field
            if client_ts >= server_ts:
                merged[key] = client_val
                merge_log.append(f"{key}: client_wins_lww")
            else:
                merged[key] = server_val
                merge_log.append(f"{key}: server_wins_lww")

    if merge_log:
        merged["_merge_log"] = merge_log
        logger.debug("Field-level merge: %s", merge_log)

    return merged


# ---------------------------------------------------------------------------
# Entity CRUD helpers
# ---------------------------------------------------------------------------


async def _apply_entity_upsert(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    data: dict,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> None:
    """Map entity_type to the ORM model and perform an upsert.

    For creates: sets project_id, id, and allowlisted payload fields.
    For updates: merges allowlisted payload fields onto the existing record.

    Only fields listed in ``_ENTITY_WRITABLE_FIELDS`` for the given
    *entity_type* are accepted.  Any other keys present in *data* are
    logged as a warning and silently dropped to prevent mass-assignment
    of server-managed columns (created_by, data_source, procore_id, etc.).
    """
    model = _get_model_class(entity_type)
    if model is None:
        raise ValueError(f"Unknown entity type: {entity_type}")

    try:
        entity_uuid = uuid.UUID(entity_id)
    except ValueError:
        raise ValueError(f"Invalid entity_id: {entity_id}")

    # --- Allowlist filtering ---
    allowed = _ENTITY_WRITABLE_FIELDS.get(entity_type)
    if allowed is not None:
        rejected_keys = (
            set(data.keys())
            - allowed
            - {
                "id",
                "project_id",
                "created_at",
                "updated_at",
                "_photo_refs",
                "_merge_log",
            }
        )
        if rejected_keys:
            logger.warning(
                "Sync upsert for %s/%s: rejected non-allowlisted fields: %s",
                entity_type,
                entity_id,
                sorted(rejected_keys),
            )
        filtered_data = {k: v for k, v in data.items() if k in allowed}
    else:
        # Entity type has no allowlist defined — fall back to the
        # infrastructure-only denylist as a safety net.
        filtered_data = {
            k: v
            for k, v in data.items()
            if k not in ("id", "project_id", "created_at", "updated_at")
        }
        logger.warning(
            "Sync upsert for %s: no allowlist defined, using denylist fallback",
            entity_type,
        )

    existing = await db.get(model, entity_uuid)

    if existing is None:
        # Create new record
        kwargs: dict[str, Any] = {"id": entity_uuid, "project_id": project_id}

        # Map allowlisted payload fields to model columns
        for col in model.__table__.columns:
            col_name = col.name
            if col_name in ("id", "project_id", "created_at", "updated_at"):
                continue
            if col_name in filtered_data:
                kwargs[col_name] = filtered_data[col_name]

        # Set created_by / user_id if the model has it
        if hasattr(model, "created_by") and "created_by" not in kwargs and user_id:
            kwargs["created_by"] = user_id

        record = model(**kwargs)
        db.add(record)
    else:
        # Update existing record
        for col in model.__table__.columns:
            col_name = col.name
            if col_name in ("id", "project_id", "created_at"):
                continue
            if col_name in filtered_data:
                setattr(existing, col_name, filtered_data[col_name])
        if hasattr(existing, "updated_at"):
            existing.updated_at = datetime.now(UTC)


async def _apply_entity_delete(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    project_id: uuid.UUID,
) -> None:
    """Soft-delete or hard-delete an entity by type and ID."""
    model = _get_model_class(entity_type)
    if model is None:
        raise ValueError(f"Unknown entity type: {entity_type}")

    try:
        entity_uuid = uuid.UUID(entity_id)
    except ValueError:
        raise ValueError(f"Invalid entity_id: {entity_id}")

    record = await db.get(model, entity_uuid)
    if record is not None and record.project_id == project_id:
        # Soft delete if status field exists, otherwise hard delete
        if hasattr(record, "status"):
            record.status = "deleted"
            if hasattr(record, "updated_at"):
                record.updated_at = datetime.now(UTC)
        else:
            await db.delete(record)


async def _get_entity_record(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    project_id: uuid.UUID,
) -> Any | None:
    """Fetch an entity record by type and ID."""
    model = _get_model_class(entity_type)
    if model is None:
        return None

    try:
        entity_uuid = uuid.UUID(entity_id)
    except ValueError:
        return None

    record = await db.get(model, entity_uuid)
    if record is not None and record.project_id == project_id:
        return record
    return None


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------


async def get_sync_status(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
) -> Any | None:
    """Get the current sync state for a device+project pair."""
    from app.models.offline_sync import DeviceSyncState

    result = await db.execute(
        select(DeviceSyncState).where(
            DeviceSyncState.project_id == project_id,
            DeviceSyncState.device_id == device_id,
        )
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Photo upload queue
# ---------------------------------------------------------------------------


async def process_photo_upload(
    db: AsyncSession,
    project_id: uuid.UUID,
    photo_queue_id: uuid.UUID,
    file_bytes: bytes,
) -> dict:
    """Upload a queued photo to S3 and update the queue status.

    Returns {s3_key, file_size, status}.
    """
    from app.models.offline_sync import PhotoUploadQueue

    record = await db.get(PhotoUploadQueue, photo_queue_id)
    if record is None or record.project_id != project_id:
        raise ValueError("Photo queue item not found")

    if record.status == "completed":
        return {
            "s3_key": record.s3_key,
            "file_size": record.file_size_bytes,
            "status": "already_completed",
        }

    # Generate S3 key
    ext = ".jpg"
    if record.content_type == "image/png":
        ext = ".png"
    elif record.content_type == "image/heic":
        ext = ".heic"

    s3_key = f"offline-photos/{project_id}/{record.device_id}/{photo_queue_id}{ext}"

    # Upload to S3 — use the module-level `settings` so tests that patch
    # ``app.services.sync.offline_sync_engine.settings`` actually intercept
    # the S3 path. A local ``from app.config import settings`` would
    # rebind to the un-patched module attribute and bypass the test mock.
    try:
        if settings.S3_BUCKET_DOCUMENTS:
            import boto3

            s3_client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL or None,
                region_name=getattr(settings, "S3_REGION", "us-east-1"),
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
            )
            s3_client.put_object(
                Bucket=settings.S3_BUCKET_DOCUMENTS,
                Key=s3_key,
                Body=file_bytes,
                ContentType=record.content_type,
            )
        else:
            logger.warning("S3 not configured; storing key reference only")
    except Exception as exc:
        logger.error("S3 upload failed for photo %s: %s", photo_queue_id, exc)
        record.status = "failed"
        record.error_message = str(exc)
        await db.flush()
        raise

    record.s3_key = s3_key
    record.file_size_bytes = len(file_bytes)
    record.status = "completed"
    record.completed_at = datetime.now(UTC)
    record.error_message = None
    await db.flush()
    await db.refresh(record)

    logger.info("Photo %s uploaded to %s (%d bytes)", photo_queue_id, s3_key, len(file_bytes))
    return {
        "s3_key": s3_key,
        "file_size": len(file_bytes),
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Conflict listing
# ---------------------------------------------------------------------------


async def list_conflicts(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str | None = None,
    resolved: bool | None = None,
    limit: int = 50,
) -> list[Any]:
    """List conflict logs for a project, optionally filtered by device and resolution status."""
    from app.models.offline_sync import ConflictLog

    query = (
        select(ConflictLog)
        .where(ConflictLog.project_id == project_id)
        .order_by(ConflictLog.created_at.desc())
    )

    if device_id is not None:
        query = query.where(ConflictLog.device_id == device_id)
    if resolved is not None:
        query = query.where(ConflictLog.is_resolved == resolved)

    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_model_class(entity_type: str) -> Any | None:
    """Resolve an entity_type string to a SQLAlchemy model class."""
    if entity_type == "daily_log":
        from app.models.productivity import DailyLog

        return DailyLog
    elif entity_type == "punch_list_item":
        from app.models.field_management import PunchListItem

        return PunchListItem
    elif entity_type == "safety_observation":
        from app.models.safety_incident import SafetyAlert

        return SafetyAlert
    elif entity_type == "time_entry":
        from app.models.productivity import CrewProductivity

        return CrewProductivity
    # SV-40: Additional entity types
    elif entity_type == "rfi":
        from app.models.communication import RFI

        return RFI
    elif entity_type == "inspection":
        from app.models.quality import Inspection

        return Inspection
    elif entity_type == "equipment_log":
        from app.models.field_management import Equipment

        return Equipment
    return None


def _get_record_timestamp(record: Any) -> datetime:
    """Extract the updated_at or created_at timestamp from a record."""
    ts = getattr(record, "updated_at", None)
    if ts is not None:
        return ts
    ts = getattr(record, "created_at", None)
    if ts is not None:
        return ts
    return datetime.min.replace(tzinfo=UTC)


def _record_to_dict(record: Any) -> dict:
    """Convert a SQLAlchemy record to a serializable dict."""
    result: dict[str, Any] = {}
    for col in record.__table__.columns:
        val = getattr(record, col.name, None)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, uuid.UUID):
            val = str(val)
        elif val is not None and hasattr(val, "__float__"):
            val = float(val)
        result[col.name] = val
    return result


def _parse_timestamp(ts_raw: Any) -> datetime | None:
    """Parse a timestamp from various formats."""
    if ts_raw is None:
        return None
    if isinstance(ts_raw, datetime):
        if ts_raw.tzinfo is None:
            return ts_raw.replace(tzinfo=UTC)
        return ts_raw
    if isinstance(ts_raw, str):
        try:
            parsed = datetime.fromisoformat(ts_raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            return None
    return None


async def _update_device_state(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
    user_id: uuid.UUID,
    push_at: datetime | None = None,
    pull_at: datetime | None = None,
    server_ts: datetime | None = None,
) -> Any:
    """Create or update the DeviceSyncState record."""
    from app.models.offline_sync import DeviceSyncState

    result = await db.execute(
        select(DeviceSyncState).where(
            DeviceSyncState.project_id == project_id,
            DeviceSyncState.device_id == device_id,
        )
    )
    state = result.scalars().first()

    now = datetime.now(UTC)
    if state is None:
        state = DeviceSyncState(
            project_id=project_id,
            device_id=device_id,
            user_id=user_id,
            last_push_at=push_at,
            last_pull_at=pull_at,
            last_server_timestamp=server_ts,
        )
        db.add(state)
    else:
        if push_at:
            state.last_push_at = push_at
        if pull_at:
            state.last_pull_at = pull_at
        if server_ts:
            state.last_server_timestamp = server_ts
        state.updated_at = now

    return state


async def _get_or_create_device_state(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
) -> Any | None:
    """Get or create a DeviceSyncState (for pull updates)."""
    from app.models.offline_sync import DeviceSyncState

    result = await db.execute(
        select(DeviceSyncState).where(
            DeviceSyncState.project_id == project_id,
            DeviceSyncState.device_id == device_id,
        )
    )
    return result.scalars().first()


async def _queue_photos(
    db: AsyncSession,
    project_id: uuid.UUID,
    device_id: str,
    user_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    photo_refs: list[dict],
    client_ts: datetime,
) -> None:
    """Queue photo references for deferred upload."""
    from app.models.offline_sync import PhotoUploadQueue

    for ref in photo_refs:
        file_name = ref.get("file_name", f"{uuid.uuid4()}.jpg")
        content_type = ref.get("content_type", "image/jpeg")
        file_size = ref.get("file_size_bytes")

        queue_item = PhotoUploadQueue(
            project_id=project_id,
            device_id=device_id,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            file_name=file_name,
            content_type=content_type,
            file_size_bytes=file_size,
            client_timestamp=client_ts,
        )
        db.add(queue_item)
