"""Ambient field intelligence service.

Ingests GPS pings, equipment telemetry, and badge events from IoT devices
and mobile apps. Aggregates into daily snapshots that feed the automated
daily report generator without requiring manual data entry.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid enum values
# ---------------------------------------------------------------------------
VALID_EQUIPMENT_STATUSES = {"idle", "running", "off"}
VALID_BADGE_EVENT_TYPES = {"check_in", "check_out", "break_start", "break_end"}

# Deduplication window: pings from the same worker within this many seconds
# are treated as duplicates.
PING_DEDUP_WINDOW_SECONDS = 5

# Default assumed work hours when a check_out is missing.
DEFAULT_MISSING_CHECKOUT_HOURS = 8.0

# Maximum batch size for ingestion endpoints.
MAX_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Ingestion: Field pings
# ---------------------------------------------------------------------------


async def ingest_field_pings(
    db: AsyncSession,
    project_id: uuid.UUID,
    pings: list[dict],
) -> int:
    """Batch insert GPS pings with coordinate validation and deduplication.

    Each ping dict must contain: worker_id, latitude, longitude, timestamp.
    Optional: accuracy_m, altitude_m, trade.

    Deduplication: if a ping exists for the same worker_id within 5 seconds
    of the submitted timestamp, the new ping is skipped.

    Returns the count of inserted pings.
    """
    from app.models.ambient_field import FieldPing

    if not pings:
        return 0

    # Validate and filter
    valid_pings: list[dict] = []
    for p in pings:
        lat = p.get("latitude")
        lon = p.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat_f <= 90):
            continue
        if not (-180 <= lon_f <= 180):
            continue
        ts = p.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue
        worker_id = p.get("worker_id")
        if not worker_id:
            continue
        valid_pings.append(
            {
                "worker_id": str(worker_id),
                "latitude": lat_f,
                "longitude": lon_f,
                "accuracy_m": p.get("accuracy_m"),
                "altitude_m": p.get("altitude_m"),
                "trade": p.get("trade"),
                "timestamp": ts,
            }
        )

    if not valid_pings:
        return 0

    # --- Batch deduplication (replaces per-ping N+1 query pattern) ---
    # Build OR conditions in chunks to avoid excessively large queries.
    DEDUP_CHUNK_SIZE = 100

    existing_set: set[tuple[str, datetime]] = set()

    for chunk_start in range(0, len(valid_pings), DEDUP_CHUNK_SIZE):
        chunk = valid_pings[chunk_start : chunk_start + DEDUP_CHUNK_SIZE]
        or_conditions = [
            and_(
                FieldPing.worker_id == p["worker_id"],
                FieldPing.timestamp
                >= p["timestamp"] - timedelta(seconds=PING_DEDUP_WINDOW_SECONDS),
                FieldPing.timestamp
                <= p["timestamp"] + timedelta(seconds=PING_DEDUP_WINDOW_SECONDS),
            )
            for p in chunk
        ]
        if not or_conditions:
            continue

        result = await db.execute(
            select(FieldPing.worker_id, FieldPing.timestamp).where(
                FieldPing.project_id == project_id,
                or_(*or_conditions),
            )
        )
        for row in result.all():
            existing_set.add((row[0], row[1]))

    # Filter out duplicates: a ping is a duplicate if any existing ping
    # for the same worker_id falls within the dedup window.
    def _is_duplicate(ping_data: dict) -> bool:
        wid = ping_data["worker_id"]
        ts = ping_data["timestamp"]
        for existing_wid, existing_ts in existing_set:
            if existing_wid != wid:
                continue
            delta = abs((ts - existing_ts).total_seconds())
            if delta <= PING_DEDUP_WINDOW_SECONDS:
                return True
        return False

    inserted = 0
    for ping_data in valid_pings:
        if _is_duplicate(ping_data):
            continue

        record = FieldPing(project_id=project_id, **ping_data)
        db.add(record)
        # Add to existing_set so later pings in the same batch
        # detect intra-batch duplicates.
        existing_set.add((ping_data["worker_id"], ping_data["timestamp"]))
        inserted += 1

    if inserted:
        await db.flush()

    logger.info(
        "Ingested %d/%d field pings for project %s",
        inserted,
        len(pings),
        project_id,
    )
    return inserted


# ---------------------------------------------------------------------------
# Ingestion: Equipment telemetry
# ---------------------------------------------------------------------------


async def ingest_equipment_telemetry(
    db: AsyncSession,
    project_id: uuid.UUID,
    telemetry_batch: list[dict],
) -> int:
    """Batch insert equipment telemetry records.

    Each dict must contain: equipment_id, timestamp.
    Optional: equipment_type, status (idle/running/off), fuel_level_pct,
    engine_hours, latitude, longitude, raw_payload.

    Returns the count of inserted records.
    """
    from app.models.ambient_field import AmbientEquipmentTelemetry

    if not telemetry_batch:
        return 0

    inserted = 0
    for t in telemetry_batch:
        equipment_id = t.get("equipment_id")
        if not equipment_id:
            continue

        ts = t.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue

        status = t.get("status", "idle")
        if status not in VALID_EQUIPMENT_STATUSES:
            continue

        record = AmbientEquipmentTelemetry(
            project_id=project_id,
            equipment_id=str(equipment_id),
            equipment_type=t.get("equipment_type"),
            status=status,
            fuel_level_pct=t.get("fuel_level_pct"),
            engine_hours=t.get("engine_hours"),
            latitude=t.get("latitude"),
            longitude=t.get("longitude"),
            raw_payload=t.get("raw_payload", {}),
            timestamp=ts,
        )
        db.add(record)
        inserted += 1

    if inserted:
        await db.flush()

    logger.info(
        "Ingested %d/%d equipment telemetry records for project %s",
        inserted,
        len(telemetry_batch),
        project_id,
    )
    return inserted


# ---------------------------------------------------------------------------
# Ingestion: Badge events
# ---------------------------------------------------------------------------


async def ingest_badge_events(
    db: AsyncSession,
    project_id: uuid.UUID,
    events: list[dict],
) -> int:
    """Batch insert badge check-in/check-out events.

    Each dict must contain: worker_id, event_type, timestamp.
    Valid event_types: check_in, check_out, break_start, break_end.
    Optional: worker_name, trade, gate_id.

    Returns the count of inserted events.
    """
    from app.models.ambient_field import BadgeEvent

    if not events:
        return 0

    inserted = 0
    for e in events:
        worker_id = e.get("worker_id")
        if not worker_id:
            continue

        event_type = e.get("event_type")
        if event_type not in VALID_BADGE_EVENT_TYPES:
            continue

        ts = e.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue

        record = BadgeEvent(
            project_id=project_id,
            worker_id=str(worker_id),
            worker_name=e.get("worker_name"),
            trade=e.get("trade"),
            event_type=event_type,
            gate_id=e.get("gate_id"),
            timestamp=ts,
        )
        db.add(record)
        inserted += 1

    if inserted:
        await db.flush()

    logger.info(
        "Ingested %d/%d badge events for project %s",
        inserted,
        len(events),
        project_id,
    )
    return inserted


# ---------------------------------------------------------------------------
# Daily aggregation (the core function)
# ---------------------------------------------------------------------------


async def aggregate_daily_snapshot(
    db: AsyncSession,
    project_id: uuid.UUID,
    snapshot_date: date,
) -> Any:
    """Aggregate all ambient data for a given date into a daily snapshot.

    Queries field_pings, ambient_equipment_telemetry, and badge_events
    for the date. Computes workforce summary, equipment utilization,
    site activity, zone activity, and data quality metrics.

    Upserts into ambient_daily_snapshots (one row per project per date).

    Returns the AmbientDailySnapshot record.
    """
    from app.models.ambient_field import (
        AmbientDailySnapshot,
        AmbientEquipmentTelemetry,
        BadgeEvent,
        FieldPing,
    )

    day_start = datetime(snapshot_date.year, snapshot_date.month, snapshot_date.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    # --- Fetch raw data for the date ---
    pings_result = await db.execute(
        select(FieldPing)
        .where(
            FieldPing.project_id == project_id,
            FieldPing.timestamp >= day_start,
            FieldPing.timestamp < day_end,
        )
        .order_by(FieldPing.timestamp)
    )
    pings = list(pings_result.scalars().all())

    telemetry_result = await db.execute(
        select(AmbientEquipmentTelemetry)
        .where(
            AmbientEquipmentTelemetry.project_id == project_id,
            AmbientEquipmentTelemetry.timestamp >= day_start,
            AmbientEquipmentTelemetry.timestamp < day_end,
        )
        .order_by(AmbientEquipmentTelemetry.timestamp)
    )
    telemetry = list(telemetry_result.scalars().all())

    badge_result = await db.execute(
        select(BadgeEvent)
        .where(
            BadgeEvent.project_id == project_id,
            BadgeEvent.timestamp >= day_start,
            BadgeEvent.timestamp < day_end,
        )
        .order_by(BadgeEvent.timestamp)
    )
    badge_events = list(badge_result.scalars().all())

    # --- Compute summaries ---
    badge_dicts: list[dict[str, Any]] = [
        {
            "worker_id": b.worker_id,
            "worker_name": b.worker_name,
            "trade": b.trade,
            "event_type": b.event_type,
            "timestamp": b.timestamp,
        }
        for b in badge_events
    ]
    worker_hours = _compute_worker_hours(badge_dicts)

    telemetry_dicts = [
        {
            "equipment_id": t.equipment_id,
            "equipment_type": t.equipment_type,
            "status": t.status,
            "timestamp": t.timestamp,
            "fuel_level_pct": float(t.fuel_level_pct) if t.fuel_level_pct else None,
            "engine_hours": float(t.engine_hours) if t.engine_hours else None,
        }
        for t in telemetry
    ]
    equipment_util = _compute_equipment_utilization(telemetry_dicts)

    ping_dicts: list[dict[str, Any]] = [
        {
            "worker_id": p.worker_id,
            "latitude": float(p.latitude),
            "longitude": float(p.longitude),
            "timestamp": p.timestamp,
            "trade": p.trade,
        }
        for p in pings
    ]
    zones = _detect_site_zones(ping_dicts)

    # --- Workforce summary ---
    unique_workers = set()
    trade_counts: dict[str, int] = defaultdict(int)
    total_hours = 0.0
    for wid, info in worker_hours.items():
        unique_workers.add(wid)
        trade = info.get("trade") or "unknown"
        trade_counts[trade] += 1
        total_hours += info.get("hours", 0.0)

    # Also count unique workers from pings who may not have badge events
    for p in ping_dicts:
        unique_workers.add(p["worker_id"])

    def _worker_detail(info: dict[str, Any]) -> dict[str, Any]:
        check_in = info.get("in")
        check_out = info.get("out")
        return {
            "trade": info.get("trade", "unknown"),
            "hours": round(info.get("hours", 0.0), 2),
            "check_in": check_in.isoformat() if check_in is not None else None,
            "check_out": check_out.isoformat() if check_out is not None else None,
        }

    workforce_summary = {
        "total_headcount": len(unique_workers),
        "total_hours": round(total_hours, 2),
        "by_trade": [
            {"trade": trade, "headcount": count} for trade, count in sorted(trade_counts.items())
        ],
        "worker_details": {wid: _worker_detail(info) for wid, info in worker_hours.items()},
    }

    # --- Equipment summary ---
    equipment_summary = {
        "equipment_count": len(equipment_util),
        "total_running_hours": round(sum(e.get("running_hours", 0) for e in equipment_util), 2),
        "average_utilization_pct": round(
            (sum(e.get("utilization_pct", 0) for e in equipment_util) / len(equipment_util))
            if equipment_util
            else 0,
            1,
        ),
        "details": equipment_util,
    }

    # --- Site activity ---
    all_timestamps = (
        [b.timestamp for b in badge_events]
        + [p.timestamp for p in pings]
        + [t.timestamp for t in telemetry]
    )
    first_arrival = min(all_timestamps).isoformat() if all_timestamps else None
    last_departure = max(all_timestamps).isoformat() if all_timestamps else None

    # Peak headcount by hour
    hourly_counts: dict[int, set[str]] = defaultdict(set)
    for b in badge_dicts:
        if b["event_type"] == "check_in":
            hourly_counts[b["timestamp"].hour].add(b["worker_id"])
    for p in ping_dicts:
        hourly_counts[p["timestamp"].hour].add(p["worker_id"])

    peak_hour = 0
    peak_count = 0
    for hour, workers in hourly_counts.items():
        if len(workers) > peak_count:
            peak_count = len(workers)
            peak_hour = hour

    site_activity = {
        "first_arrival": first_arrival,
        "last_departure": last_departure,
        "peak_headcount": peak_count,
        "peak_hour": peak_hour,
        "hourly_activity": {str(h): len(w) for h, w in sorted(hourly_counts.items())},
    }

    # --- Data quality ---
    data_quality = {
        "ping_count": len(pings),
        "telemetry_count": len(telemetry),
        "badge_event_count": len(badge_events),
        "workers_with_badge": len(worker_hours),
        "workers_with_pings": len({p["worker_id"] for p in ping_dicts}),
        "coverage_pct": round(
            (len(worker_hours) / len(unique_workers) * 100) if unique_workers else 0,
            1,
        ),
    }

    # --- Upsert snapshot ---
    existing_result = await db.execute(
        select(AmbientDailySnapshot).where(
            AmbientDailySnapshot.project_id == project_id,
            AmbientDailySnapshot.snapshot_date == snapshot_date,
        )
    )
    snapshot = existing_result.scalars().first()

    if snapshot:
        snapshot.workforce_summary = workforce_summary
        snapshot.equipment_summary = equipment_summary
        snapshot.site_activity = site_activity
        snapshot.zone_activity = zones
        snapshot.data_quality = data_quality
        snapshot.updated_at = datetime.now(UTC)
    else:
        snapshot = AmbientDailySnapshot(
            project_id=project_id,
            snapshot_date=snapshot_date,
            workforce_summary=workforce_summary,
            equipment_summary=equipment_summary,
            site_activity=site_activity,
            zone_activity=zones,
            data_quality=data_quality,
        )
        db.add(snapshot)

    await db.flush()
    await db.refresh(snapshot)

    logger.info(
        "Aggregated daily snapshot for project %s date %s: %d workers, %d equipment",
        project_id,
        snapshot_date,
        len(unique_workers),
        len(equipment_util),
    )
    return snapshot


# ---------------------------------------------------------------------------
# Report generation bridge
# ---------------------------------------------------------------------------


async def generate_report_from_snapshot(
    db: AsyncSession,
    project_id: uuid.UUID,
    snapshot_date: date,
    generated_by: uuid.UUID | None = None,
) -> Any:
    """Load an ambient snapshot and feed it into the daily report generator.

    Converts the snapshot data into a ``DailyDataAggregate`` and passes it
    directly to ``create_daily_report`` via the *pre_built_aggregate* parameter.
    This ensures the IoT-sourced workforce and equipment data from the ambient
    snapshot is actually injected into the generated report instead of being
    silently discarded while the report generator independently re-queries its
    own (possibly empty) data sources.

    The aggregate starts from the standard DB sources (daily_log, weather,
    safety, schedule, quality) and then overlays / supplements with the richer
    ambient snapshot data for workforce and equipment sections.
    """
    from app.models.ambient_field import AmbientDailySnapshot
    from app.services.reporting.daily_report_generator import (
        aggregate_daily_data,
        create_daily_report,
    )

    result = await db.execute(
        select(AmbientDailySnapshot).where(
            AmbientDailySnapshot.project_id == project_id,
            AmbientDailySnapshot.snapshot_date == snapshot_date,
        )
    )
    snapshot = result.scalars().first()
    if snapshot is None:
        raise ValueError(f"No ambient snapshot found for project {project_id} on {snapshot_date}")

    # Start with the standard aggregate (weather, safety, schedule, quality, etc.)
    aggregate = await aggregate_daily_data(db, project_id, snapshot_date)

    # Overlay ambient workforce data — the snapshot has richer IoT-sourced
    # headcount and hours than the manual daily log.
    ws = snapshot.workforce_summary or {}
    if ws.get("total_headcount"):
        ambient_workforce = {
            "total_headcount": ws.get("total_headcount", 0),
            "total_hours": ws.get("total_hours", 0),
            "by_trade": ws.get("by_trade", []),
        }
        # Merge: prefer ambient data when it has more headcount (IoT is more accurate)
        existing_headcount = aggregate.workforce.get("total_headcount", 0)
        if ws["total_headcount"] >= existing_headcount:
            aggregate.workforce = ambient_workforce

    # Overlay ambient equipment data — the snapshot has utilization metrics
    # that standard equipment queries don't provide.
    es = snapshot.equipment_summary or {}
    if es.get("equipment_count"):
        ambient_equipment = []
        for detail in es.get("details", []):
            ambient_equipment.append(
                {
                    "equipment_type": detail.get("equipment_type", "Unknown"),
                    "equipment_id": detail.get("equipment_id", ""),
                    "status": "active" if detail.get("utilization_pct", 0) > 0 else "idle",
                    "running_hours": detail.get("running_hours", 0),
                    "utilization_pct": detail.get("utilization_pct", 0),
                }
            )
        # Supplement: append ambient equipment to any existing equipment list
        if ambient_equipment:
            existing_ids = {
                e.get("equipment_id") for e in aggregate.equipment if e.get("equipment_id")
            }
            for eq in ambient_equipment:
                if eq.get("equipment_id") not in existing_ids:
                    aggregate.equipment.append(eq)

    # Inject site activity into the daily_log dict if available
    sa = snapshot.site_activity or {}
    if sa and aggregate.daily_log is None:
        aggregate.daily_log = {}
    if sa and aggregate.daily_log is not None:
        if sa.get("first_arrival"):
            aggregate.daily_log.setdefault("site_first_arrival", sa["first_arrival"])
        if sa.get("last_departure"):
            aggregate.daily_log.setdefault("site_last_departure", sa["last_departure"])
        if sa.get("peak_headcount"):
            aggregate.daily_log.setdefault("peak_headcount", sa["peak_headcount"])

    report = await create_daily_report(
        db=db,
        project_id=project_id,
        report_date=snapshot_date,
        generated_by=generated_by,
        pre_built_aggregate=aggregate,
    )

    logger.info(
        "Generated daily report %s from ambient snapshot for project %s date %s",
        report.id,
        project_id,
        snapshot_date,
    )
    return report


# ---------------------------------------------------------------------------
# Pure functions for aggregation
# ---------------------------------------------------------------------------


def _compute_worker_hours(badge_events: list[dict]) -> dict[str, dict]:
    """Compute working hours per worker from badge events.

    Pairs check_in/check_out events chronologically by worker_id.
    Handles missing check_out (assumes DEFAULT_MISSING_CHECKOUT_HOURS).
    Deducts break time from break_start/break_end pairs.

    Returns {worker_id: {trade, hours, in, out}}.
    """
    # Group events by worker
    by_worker: dict[str, list[dict]] = defaultdict(list)
    for event in badge_events:
        by_worker[event["worker_id"]].append(event)

    result: dict[str, dict] = {}
    for worker_id, events in by_worker.items():
        events.sort(key=lambda e: e["timestamp"])

        check_in_time: datetime | None = None
        break_start_time: datetime | None = None
        total_break_hours = 0.0
        trade = None

        for e in events:
            if e.get("trade"):
                trade = e["trade"]

            if e["event_type"] == "check_in":
                check_in_time = e["timestamp"]
            elif e["event_type"] == "check_out":
                if check_in_time is not None:
                    delta = (e["timestamp"] - check_in_time).total_seconds() / 3600.0
                    hours = max(0.0, delta - total_break_hours)
                    result[worker_id] = {
                        "trade": trade,
                        "hours": round(hours, 2),
                        "in": check_in_time,
                        "out": e["timestamp"],
                    }
                    # Reset for potential second shift
                    check_in_time = None
                    total_break_hours = 0.0
            elif e["event_type"] == "break_start":
                break_start_time = e["timestamp"]
            elif e["event_type"] == "break_end" and break_start_time is not None:
                break_delta = (e["timestamp"] - break_start_time).total_seconds() / 3600.0
                total_break_hours += break_delta
                break_start_time = None

        # Handle missing check_out: if check_in exists but no check_out, assume default
        if check_in_time is not None and worker_id not in result:
            hours = max(0.0, DEFAULT_MISSING_CHECKOUT_HOURS - total_break_hours)
            result[worker_id] = {
                "trade": trade,
                "hours": round(hours, 2),
                "in": check_in_time,
                "out": None,
            }

    return result


def _compute_equipment_utilization(telemetry: list[dict]) -> list[dict]:
    """Compute utilization percentage per equipment from telemetry status transitions.

    Groups telemetry by equipment_id, sorts chronologically, and calculates
    running time vs total observed time.

    Returns list of {equipment_id, equipment_type, running_hours, idle_hours,
    total_hours, utilization_pct, fuel_consumption}.
    """
    by_equipment: dict[str, list[dict]] = defaultdict(list)
    for t in telemetry:
        by_equipment[t["equipment_id"]].append(t)

    results: list[dict] = []
    for equipment_id, readings in by_equipment.items():
        readings.sort(key=lambda r: r["timestamp"])

        if len(readings) < 2:
            # Single reading: cannot compute utilization
            results.append(
                {
                    "equipment_id": equipment_id,
                    "equipment_type": readings[0].get("equipment_type"),
                    "running_hours": 0.0,
                    "idle_hours": 0.0,
                    "total_hours": 0.0,
                    "utilization_pct": 0.0,
                    "readings_count": 1,
                }
            )
            continue

        running_seconds = 0.0
        idle_seconds = 0.0
        off_seconds = 0.0

        for i in range(len(readings) - 1):
            delta = (readings[i + 1]["timestamp"] - readings[i]["timestamp"]).total_seconds()
            # Clamp intervals to max 2 hours to avoid counting overnight gaps
            delta = min(delta, 7200.0)
            status = readings[i].get("status", "idle")
            if status == "running":
                running_seconds += delta
            elif status == "idle":
                idle_seconds += delta
            else:
                off_seconds += delta

        total_seconds = running_seconds + idle_seconds + off_seconds
        total_hours = total_seconds / 3600.0
        running_hours = running_seconds / 3600.0
        idle_hours = idle_seconds / 3600.0

        utilization_pct = (running_seconds / total_seconds * 100) if total_seconds > 0 else 0.0

        # Get fuel consumption from first/last readings if available
        fuel_start = None
        fuel_end = None
        for r in readings:
            if r.get("fuel_level_pct") is not None:
                if fuel_start is None:
                    fuel_start = r["fuel_level_pct"]
                fuel_end = r["fuel_level_pct"]

        fuel_consumed = None
        if fuel_start is not None and fuel_end is not None and fuel_start > fuel_end:
            fuel_consumed = round(fuel_start - fuel_end, 1)

        results.append(
            {
                "equipment_id": equipment_id,
                "equipment_type": readings[0].get("equipment_type"),
                "running_hours": round(running_hours, 2),
                "idle_hours": round(idle_hours, 2),
                "total_hours": round(total_hours, 2),
                "utilization_pct": round(utilization_pct, 1),
                "fuel_consumed_pct": fuel_consumed,
                "readings_count": len(readings),
            }
        )

    return results


def _detect_site_zones(
    pings: list[dict],
    cluster_radius_m: float = 50.0,
) -> list[dict]:
    """Detect activity zones by clustering GPS pings into a grid.

    Divides the site into grid cells of approximately cluster_radius_m
    and counts pings per cell. Cells with significant activity (>= 3 pings)
    are returned as zones.

    Returns list of {center_lat, center_lon, worker_count, ping_count, trades}.
    """
    if not pings:
        return []

    # Compute the average latitude for longitude correction
    avg_lat = sum(p["latitude"] for p in pings) / len(pings)
    lat_rad = math.radians(avg_lat)

    # 1 degree lat ~ 111,000 m everywhere.
    # 1 degree lon ~ 111,000 * cos(latitude) m (varies with latitude).
    lat_step = cluster_radius_m / 111_000.0
    lon_step = cluster_radius_m / (111_000.0 * max(math.cos(lat_rad), 0.01))

    grid: dict[tuple[int, int], list[dict]] = defaultdict(list)

    for p in pings:
        lat = p["latitude"]
        lon = p["longitude"]
        cell_y = int(lat / lat_step)
        cell_x = int(lon / lon_step)
        grid[(cell_x, cell_y)].append(p)

    zones: list[dict] = []
    min_pings_for_zone = 3

    for (_cx, _cy), cell_pings in grid.items():
        if len(cell_pings) < min_pings_for_zone:
            continue

        center_lat = sum(p["latitude"] for p in cell_pings) / len(cell_pings)
        center_lon = sum(p["longitude"] for p in cell_pings) / len(cell_pings)
        unique_workers = {p["worker_id"] for p in cell_pings}
        trades = {p.get("trade") for p in cell_pings if p.get("trade")}

        zones.append(
            {
                "center_lat": round(center_lat, 7),
                "center_lon": round(center_lon, 7),
                "worker_count": len(unique_workers),
                "ping_count": len(cell_pings),
                "trades": sorted(trades),
            }
        )

    # Sort zones by ping count descending
    zones.sort(key=lambda z: z["ping_count"], reverse=True)
    return zones
