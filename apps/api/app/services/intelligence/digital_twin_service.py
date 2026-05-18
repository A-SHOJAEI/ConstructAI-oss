"""Digital twin management: model creation, IoT sensor overlay, and snapshots.

Provides real-time construction site intelligence by combining BIM/IFC models
with live sensor data, schedule progress, and photo overlays.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensor threshold configuration — real construction-relevant values
# ---------------------------------------------------------------------------

SENSOR_THRESHOLDS: dict[str, dict[str, float]] = {
    "temperature": {
        "warn_high": 90.0,  # degF — OSHA heat advisory threshold
        "alert_high": 100.0,  # degF — dangerous heat
    },
    "humidity": {
        "warn_high": 75.0,  # % — moisture damage risk for materials
        "alert_high": 85.0,  # % — mold / concrete curing issues
    },
    "concrete_cure": {
        "warn_low": 60.0,  # degF — ACI 306 cold-weather minimum
        "alert_low": 40.0,  # degF — concrete will not hydrate properly
    },
    "vibration": {
        "warn_high": 2.0,  # in/s PPV — cosmetic damage threshold
        "alert_high": 4.0,  # in/s PPV — structural concern
    },
    "strain": {
        "warn_high": 80.0,  # % of rated capacity
        "alert_high": 95.0,  # % of rated capacity
    },
    "dust": {
        "warn_high": 5.0,  # mg/m3 — OSHA PEL for respirable dust
        "alert_high": 10.0,  # mg/m3 — stop-work threshold
    },
    "noise": {
        "warn_high": 85.0,  # dB — OSHA hearing protection trigger
        "alert_high": 90.0,  # dB — OSHA 8-hr TWA PEL
    },
}

# Units are kept in a sibling dict so SENSOR_THRESHOLDS stays float-only
# (callers compare readings against the thresholds with `>=`/`<=`).
SENSOR_UNITS: dict[str, str] = {
    "temperature": "degF",
    "humidity": "%",
    "concrete_cure": "degF",
    "vibration": "in/s",
    "strain": "%",
    "dust": "mg/m3",
    "noise": "dB",
}

VALID_SENSOR_TYPES = set(SENSOR_THRESHOLDS.keys())

VALID_SOURCE_TYPES = {"ifc", "revit", "point_cloud", "photogrammetry"}

POINT_CLOUD_FORMATS = {".las", ".laz", ".ply", ".e57"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SensorReading:
    """A single sensor reading with metadata."""

    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    timestamp: datetime
    element_id: str | None = None
    location_xyz: dict | None = None


@dataclass
class SensorAnomaly:
    """An anomalous sensor reading that exceeds thresholds."""

    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    level: str  # "warning" or "alert"
    threshold: float
    message: str
    element_id: str | None = None
    location_xyz: dict | None = None


@dataclass
class TwinState:
    """Current state of a digital twin: model + sensors + schedule + anomalies."""

    twin_id: str
    project_id: str
    name: str
    source_type: str
    status: str
    element_count: int | None
    bounds: dict | None
    coordinate_system: str | None
    metadata: dict
    sensors: list[dict] = field(default_factory=list)
    anomalies: list[SensorAnomaly] = field(default_factory=list)
    latest_snapshot: dict | None = None


# ---------------------------------------------------------------------------
# IFC parsing helpers
# ---------------------------------------------------------------------------


def _extract_ifc_metadata(ifc_result: Any) -> dict:
    """Extract metadata dict from an IfcParseResult."""
    metadata: dict = {}
    if hasattr(ifc_result, "metadata"):
        metadata.update(ifc_result.metadata)
    if hasattr(ifc_result, "entities"):
        entity_types: dict[str, int] = {}
        for entity in ifc_result.entities:
            etype = entity.get("type", "unknown")
            entity_types[etype] = entity_types.get(etype, 0) + 1
        metadata["entity_types"] = entity_types
    return metadata


def _extract_ifc_element_count(ifc_result: Any) -> int:
    """Count total entities parsed from IFC."""
    if hasattr(ifc_result, "entities"):
        return len(ifc_result.entities)
    return 0


def _extract_ifc_bounds(ifc_result: Any) -> dict | None:
    """Attempt to extract coordinate bounds from IFC metadata.

    IFC files may contain IFCSITE with lat/lon or IFCBUILDING with placement.
    This is best-effort — full geometry extraction requires a geometry library.
    """
    # Return None since our lightweight regex parser does not extract geometry
    return None


def _extract_element_bounds(ifc_result: Any) -> dict[str, dict] | None:
    """SV-35: Extract per-element bounding boxes from IFC parse result.

    Iterates over parsed entities and extracts placement coordinates when
    available.  Returns a dict of element_id -> {min_x, min_y, min_z,
    max_x, max_y, max_z}.  Returns None if no geometry data is available.
    """
    if not hasattr(ifc_result, "entities"):
        return None

    element_bounds: dict[str, dict] = {}
    for entity in ifc_result.entities:
        eid = entity.get("id") or entity.get("global_id")
        if not eid:
            continue

        # Look for placement or coordinates in the entity data
        placement = entity.get("placement") or entity.get("coordinates")
        if placement and isinstance(placement, dict):
            x = placement.get("x", 0.0)
            y = placement.get("y", 0.0)
            z = placement.get("z", 0.0)

            # Use dimensions if available, otherwise create a point bound
            dims = entity.get("dimensions", {})
            dx = float(dims.get("length", 0)) / 2 if dims else 0
            dy = float(dims.get("width", 0)) / 2 if dims else 0
            dz = float(dims.get("height", 0)) / 2 if dims else 0

            element_bounds[str(eid)] = {
                "min_x": round(float(x) - dx, 3),
                "min_y": round(float(y) - dy, 3),
                "min_z": round(float(z) - dz, 3),
                "max_x": round(float(x) + dx, 3),
                "max_y": round(float(y) + dy, 3),
                "max_z": round(float(z) + dz, 3),
            }

        # Also check for bounding_box directly
        bbox = entity.get("bounding_box")
        if bbox and isinstance(bbox, dict) and "min" in bbox and "max" in bbox:
            element_bounds[str(eid)] = {
                "min_x": float(bbox["min"].get("x", 0)),
                "min_y": float(bbox["min"].get("y", 0)),
                "min_z": float(bbox["min"].get("z", 0)),
                "max_x": float(bbox["max"].get("x", 0)),
                "max_y": float(bbox["max"].get("y", 0)),
                "max_z": float(bbox["max"].get("z", 0)),
            }

    return element_bounds if element_bounds else None


# ---------------------------------------------------------------------------
# Point cloud header parsing
# ---------------------------------------------------------------------------


def _parse_las_header(file_bytes: bytes) -> dict:
    """Parse LAS/LAZ file header to extract point count and bounds.

    LAS 1.2+ file format (ASPRS):
    - Bytes 0-3: file signature "LASF"
    - Bytes 96-99 (LAS 1.2) or variable: legacy point count (uint32)
    - Bytes 107-154: scale factors (3 doubles) and offsets (3 doubles)
    - Bytes 155-202: max/min x,y,z (6 doubles)

    For LAS 1.4, the 64-bit point count is at byte offset 247.
    """
    import struct

    metadata: dict = {"format": "LAS"}

    if len(file_bytes) < 227:
        metadata["error"] = "File too small for valid LAS header"
        return metadata

    # Check signature
    signature = file_bytes[0:4]
    if signature != b"LASF":
        metadata["error"] = "Invalid LAS signature"
        return metadata

    # Version
    version_major = file_bytes[24]
    version_minor = file_bytes[25]
    metadata["version"] = f"{version_major}.{version_minor}"

    # Header size
    struct.unpack_from("<H", file_bytes, 94)[0]

    # Legacy point count (offset 107 in LAS 1.2, but let's use the standard offset)
    # In LAS 1.0-1.3: offset 107 is legacy_point_count (uint32)
    if len(file_bytes) >= 111:
        legacy_point_count = struct.unpack_from("<I", file_bytes, 107)[0]
        metadata["point_count"] = legacy_point_count

    # LAS 1.4 has 64-bit point count at offset 247
    if version_major >= 1 and version_minor >= 4 and len(file_bytes) >= 255:
        point_count_64 = struct.unpack_from("<Q", file_bytes, 247)[0]
        if point_count_64 > 0:
            metadata["point_count"] = point_count_64

    # Scale and offset (offset 131 in standard LAS)
    if len(file_bytes) >= 227:
        try:
            scales = struct.unpack_from("<3d", file_bytes, 131)
            offsets = struct.unpack_from("<3d", file_bytes, 155)
            max_x, min_x = struct.unpack_from("<2d", file_bytes, 179)
            max_y, min_y = struct.unpack_from("<2d", file_bytes, 195)
            max_z, min_z = struct.unpack_from("<2d", file_bytes, 211)
            metadata["bounds"] = {
                "min": {"x": min_x, "y": min_y, "z": min_z},
                "max": {"x": max_x, "y": max_y, "z": max_z},
            }
            metadata["scale"] = {"x": scales[0], "y": scales[1], "z": scales[2]}
            metadata["offset"] = {"x": offsets[0], "y": offsets[1], "z": offsets[2]}
        except struct.error:
            pass

    return metadata


def _parse_ply_header(file_bytes: bytes) -> dict:
    """Parse PLY file header to extract vertex count.

    PLY header is ASCII text ending with 'end_header\\n', containing
    'element vertex <count>' lines.
    """
    metadata: dict = {"format": "PLY"}

    # PLY header is ASCII regardless of data format
    try:
        header_end = file_bytes.index(b"end_header")
        header_text = file_bytes[:header_end].decode("ascii", errors="replace")
    except ValueError:
        metadata["error"] = "No end_header found"
        return metadata

    for line in header_text.split("\n"):
        line = line.strip()
        if line.startswith("element vertex"):
            parts = line.split()
            if len(parts) >= 3:
                with contextlib.suppress(ValueError):
                    metadata["point_count"] = int(parts[2])
        elif line.startswith("format"):
            metadata["ply_format"] = line

    return metadata


def _parse_point_cloud_header(file_bytes: bytes, filename: str) -> dict:
    """Dispatch to format-specific header parser."""
    lower = filename.lower()
    if lower.endswith((".las", ".laz")):
        return _parse_las_header(file_bytes)
    elif lower.endswith(".ply"):
        return _parse_ply_header(file_bytes)
    elif lower.endswith(".e57"):
        return {"format": "E57", "note": "E57 header parsing requires libE57"}
    return {"format": "unknown"}


# ---------------------------------------------------------------------------
# Public API — Twin lifecycle
# ---------------------------------------------------------------------------


async def create_twin_from_ifc(
    db: AsyncSession,
    project_id: uuid.UUID,
    ifc_file_bytes: bytes,
    filename: str,
    created_by: uuid.UUID | None = None,
) -> Any:
    """Create a digital twin from an IFC file.

    Parses the IFC to extract element count, metadata, and coordinate info.
    Uploads to S3. Creates and returns a DigitalTwinModel record.
    """
    from app.models.digital_twin import DigitalTwinModel
    from app.services.ingestion.ifc_parser import parse_ifc
    from app.utils.s3 import upload_file

    # Parse IFC
    ifc_result = parse_ifc(ifc_file_bytes)

    element_count = _extract_ifc_element_count(ifc_result)
    bounds = _extract_ifc_bounds(ifc_result)
    metadata = _extract_ifc_metadata(ifc_result)
    metadata["original_filename"] = filename

    # SV-35: Extract element bounding boxes for element-level positioning.
    # Parse IFC entities for placement/geometry hints (min/max xyz per element).
    element_bounds = _extract_element_bounds(ifc_result)
    if element_bounds:
        metadata["element_bounds"] = element_bounds

    # Upload to S3
    twin_id = uuid.uuid4()
    s3_key = f"twins/{project_id}/{twin_id}/{filename}"
    upload_file(s3_key, ifc_file_bytes, "application/x-step")

    # Create record
    twin = DigitalTwinModel(
        id=twin_id,
        project_id=project_id,
        name=metadata.get("file_name", filename),
        source_type="ifc",
        s3_key=s3_key,
        file_size_bytes=len(ifc_file_bytes),
        element_count=element_count,
        coordinate_system=metadata.get("schema", [None])[0]
        if isinstance(metadata.get("schema"), list) and metadata.get("schema")
        else None,
        bounds=bounds,
        metadata_=metadata,
        status="ready",
        created_by=created_by,
    )
    db.add(twin)
    await db.flush()
    await db.refresh(twin)

    logger.info(
        "Digital twin created from IFC: twin_id=%s, elements=%d, project=%s",
        twin.id,
        element_count,
        project_id,
    )
    return twin


async def create_twin_from_point_cloud(
    db: AsyncSession,
    project_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    file_format: str,
    created_by: uuid.UUID | None = None,
) -> Any:
    """Create a digital twin from a point cloud file (LAS/LAZ/PLY/E57).

    Extracts basic metadata (point count, bounds) from the file header.
    Uploads to S3. Creates and returns a DigitalTwinModel record.
    """
    from app.models.digital_twin import DigitalTwinModel
    from app.utils.s3 import upload_file

    # Validate format
    fmt_lower = file_format.lower().lstrip(".")
    valid_ext = f".{fmt_lower}"
    if valid_ext not in POINT_CLOUD_FORMATS:
        raise ValueError(
            f"Unsupported point cloud format '{file_format}'. "
            f"Supported: {', '.join(sorted(POINT_CLOUD_FORMATS))}"
        )

    # Parse header
    header_meta = _parse_point_cloud_header(file_bytes, filename)

    point_count = header_meta.get("point_count")
    bounds = header_meta.get("bounds")
    metadata = {
        "original_filename": filename,
        "format": fmt_lower,
        "header": header_meta,
    }

    # Upload to S3
    twin_id = uuid.uuid4()
    s3_key = f"twins/{project_id}/{twin_id}/{filename}"
    content_type = "application/octet-stream"
    upload_file(s3_key, file_bytes, content_type)

    twin = DigitalTwinModel(
        id=twin_id,
        project_id=project_id,
        name=filename,
        source_type="point_cloud",
        s3_key=s3_key,
        file_size_bytes=len(file_bytes),
        element_count=point_count,
        coordinate_system=None,
        bounds=bounds,
        metadata_=metadata,
        status="ready",
        created_by=created_by,
    )
    db.add(twin)
    await db.flush()
    await db.refresh(twin)

    logger.info(
        "Digital twin created from point cloud: twin_id=%s, points=%s, project=%s",
        twin.id,
        point_count,
        project_id,
    )
    return twin


# ---------------------------------------------------------------------------
# Public API — Sensor management
# ---------------------------------------------------------------------------


async def register_sensor(
    db: AsyncSession,
    twin_id: uuid.UUID,
    sensor_id: str,
    sensor_type: str,
    location_xyz: dict,
    element_id: str | None = None,
) -> Any:
    """Register an IoT sensor at a position within a digital twin.

    Validates sensor_type against the known set and ensures the twin exists.
    """
    from app.models.digital_twin import DigitalTwinModel, TwinSensorLink

    # Validate sensor type
    if sensor_type not in VALID_SENSOR_TYPES:
        raise ValueError(
            f"Invalid sensor type '{sensor_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_SENSOR_TYPES))}"
        )

    # Validate location_xyz has required keys
    for key in ("x", "y", "z"):
        if key not in location_xyz:
            raise ValueError(f"location_xyz must contain '{key}' coordinate")

    # Verify twin exists
    twin = await db.get(DigitalTwinModel, twin_id)
    if twin is None:
        raise ValueError(f"Digital twin {twin_id} not found")

    sensor = TwinSensorLink(
        twin_id=twin_id,
        sensor_id=sensor_id,
        sensor_type=sensor_type,
        location_xyz=location_xyz,
        element_id=element_id,
    )
    db.add(sensor)
    await db.flush()
    await db.refresh(sensor)

    logger.info(
        "Sensor registered: sensor_id=%s, type=%s, twin=%s",
        sensor_id,
        sensor_type,
        twin_id,
    )
    return sensor


async def ingest_sensor_reading(
    db: AsyncSession,
    twin_id: uuid.UUID,
    sensor_id: str,
    value: float,
    unit: str,
    timestamp: datetime | None = None,
) -> Any:
    """Ingest a single sensor reading, updating the latest_reading on the link.

    Validates that sensor_id belongs to the specified twin.
    """
    from app.models.digital_twin import TwinSensorLink

    ts = timestamp or datetime.now(UTC)

    # Find sensor link
    result = await db.execute(
        select(TwinSensorLink).where(
            TwinSensorLink.twin_id == twin_id,
            TwinSensorLink.sensor_id == sensor_id,
        )
    )
    sensor = result.scalar_one_or_none()
    if sensor is None:
        raise ValueError(f"Sensor '{sensor_id}' not found on twin {twin_id}")

    reading_entry = {
        "value": value,
        "unit": unit,
        "timestamp": ts.isoformat(),
    }

    sensor.latest_reading = reading_entry
    sensor.last_updated = ts

    # SV-34: Append to readings_history (capped at 100 entries)
    history = list(sensor.readings_history or [])
    history.append(reading_entry)
    if len(history) > 100:
        history = history[-100:]
    sensor.readings_history = history

    await db.flush()

    return sensor


async def ingest_sensor_batch(
    db: AsyncSession,
    twin_id: uuid.UUID,
    readings: list[dict],
) -> int:
    """Batch ingest multiple sensor readings. Returns count of sensors updated.

    Each reading dict must have: sensor_id, value, unit.
    Optional: timestamp (ISO string or datetime).
    """
    from app.models.digital_twin import TwinSensorLink

    if not readings:
        return 0

    # Fetch all sensors for this twin in one query
    sensor_ids = [r["sensor_id"] for r in readings]
    result = await db.execute(
        select(TwinSensorLink).where(
            TwinSensorLink.twin_id == twin_id,
            TwinSensorLink.sensor_id.in_(sensor_ids),
        )
    )
    sensor_map = {s.sensor_id: s for s in result.scalars().all()}

    updated = 0
    for reading in readings:
        sid = reading["sensor_id"]
        sensor = sensor_map.get(sid)
        if sensor is None:
            logger.warning("Sensor '%s' not found on twin %s, skipping", sid, twin_id)
            continue

        ts_raw = reading.get("timestamp")
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = datetime.now(UTC)

        reading_entry = {
            "value": reading["value"],
            "unit": reading["unit"],
            "timestamp": ts.isoformat(),
        }

        sensor.latest_reading = reading_entry
        sensor.last_updated = ts

        # SV-34: Append to readings_history (capped at 100 entries)
        history = list(sensor.readings_history or [])
        history.append(reading_entry)
        if len(history) > 100:
            history = history[-100:]
        sensor.readings_history = history

        updated += 1

    if updated:
        await db.flush()

    logger.info(
        "Batch sensor ingest: %d/%d updated for twin %s",
        updated,
        len(readings),
        twin_id,
    )
    return updated


# ---------------------------------------------------------------------------
# Public API — Snapshots
# ---------------------------------------------------------------------------


async def create_snapshot(
    db: AsyncSession,
    twin_id: uuid.UUID,
    schedule_overlay: dict[str, float] | None = None,
    photo_urls: list[str] | None = None,
    notes: str | None = None,
) -> Any:
    """Capture a point-in-time state of the twin.

    Reads all current sensor readings and merges with the provided
    schedule progress overlay and optional photo URLs.

    IG-07: When *schedule_overlay* is None, auto-populates it by querying
    the latest schedule activity progress (ai_pct_complete or pct_complete)
    for the twin's project.  This ensures every snapshot has schedule
    context without requiring the caller to supply it explicitly.
    """
    from app.models.digital_twin import DigitalTwinModel, TwinSensorLink, TwinSnapshot

    # Verify twin exists
    twin = await db.get(DigitalTwinModel, twin_id)
    if twin is None:
        raise ValueError(f"Digital twin {twin_id} not found")

    # IG-07: Auto-populate schedule overlay from project activities
    if schedule_overlay is None:
        try:
            from app.models.scheduling import ScheduleActivity

            activities_result = await db.execute(
                select(
                    ScheduleActivity.id,
                    ScheduleActivity.pct_complete,
                    ScheduleActivity.ai_pct_complete,
                ).where(ScheduleActivity.project_id == twin.project_id)
            )
            schedule_overlay = {
                str(a.id): float(a.ai_pct_complete or a.pct_complete or 0)
                for a in activities_result.all()
            }
            if schedule_overlay:
                logger.info(
                    "Auto-populated schedule overlay for twin %s with %d activities",
                    twin_id,
                    len(schedule_overlay),
                )
        except Exception as exc:
            logger.warning(
                "Failed to auto-populate schedule overlay for twin %s: %s",
                twin_id,
                exc,
            )
            schedule_overlay = {}

    # Gather all sensor readings
    result = await db.execute(select(TwinSensorLink).where(TwinSensorLink.twin_id == twin_id))
    sensors = list(result.scalars().all())

    sensor_readings: dict[str, dict] = {}
    for s in sensors:
        if s.latest_reading is not None:
            sensor_readings[s.sensor_id] = {
                "sensor_type": s.sensor_type,
                "reading": s.latest_reading,
                "element_id": s.element_id,
                "location_xyz": s.location_xyz,
            }

    snapshot = TwinSnapshot(
        twin_id=twin_id,
        snapshot_date=datetime.now(UTC),
        sensor_readings=sensor_readings,
        schedule_overlay=schedule_overlay or {},
        photo_overlay_urls=photo_urls,
        notes=notes,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    logger.info(
        "Twin snapshot created: snapshot=%s, twin=%s, sensors=%d, schedule_activities=%d",
        snapshot.id,
        twin_id,
        len(sensor_readings),
        len(schedule_overlay or {}),
    )
    return snapshot


# ---------------------------------------------------------------------------
# Public API — Queries and analysis
# ---------------------------------------------------------------------------


async def get_twin_state(db: AsyncSession, twin_id: uuid.UUID) -> TwinState:
    """Return the current state of a digital twin.

    Combines model metadata, all sensor readings, and anomaly detection.
    """
    from app.models.digital_twin import DigitalTwinModel, TwinSensorLink, TwinSnapshot

    twin = await db.get(DigitalTwinModel, twin_id)
    if twin is None:
        raise ValueError(f"Digital twin {twin_id} not found")

    # Fetch sensors
    result = await db.execute(select(TwinSensorLink).where(TwinSensorLink.twin_id == twin_id))
    sensors = list(result.scalars().all())

    sensor_dicts = [
        {
            "id": str(s.id),
            "sensor_id": s.sensor_id,
            "sensor_type": s.sensor_type,
            "location_xyz": s.location_xyz,
            "element_id": s.element_id,
            "latest_reading": s.latest_reading,
            "last_updated": s.last_updated.isoformat() if s.last_updated else None,
        }
        for s in sensors
    ]

    # Detect anomalies
    anomalies = detect_sensor_anomalies(sensors)

    # Fetch latest snapshot
    snap_result = await db.execute(
        select(TwinSnapshot)
        .where(TwinSnapshot.twin_id == twin_id)
        .order_by(TwinSnapshot.snapshot_date.desc())
        .limit(1)
    )
    latest_snap = snap_result.scalar_one_or_none()
    snap_dict = None
    if latest_snap:
        snap_dict = {
            "id": str(latest_snap.id),
            "snapshot_date": latest_snap.snapshot_date.isoformat(),
            "schedule_overlay": latest_snap.schedule_overlay,
            "sensor_count": len(latest_snap.sensor_readings),
            "notes": latest_snap.notes,
        }

    return TwinState(
        twin_id=str(twin.id),
        project_id=str(twin.project_id),
        name=twin.name,
        source_type=twin.source_type,
        status=twin.status,
        element_count=twin.element_count,
        bounds=twin.bounds,
        coordinate_system=twin.coordinate_system,
        metadata=twin.metadata_,
        sensors=sensor_dicts,
        anomalies=anomalies,
        latest_snapshot=snap_dict,
    )


def detect_sensor_anomalies(sensors: list[Any]) -> list[SensorAnomaly]:
    """Check sensor readings against construction-safety thresholds.

    Returns a list of anomalies for sensors with readings that exceed
    warning or alert thresholds.
    """
    anomalies: list[SensorAnomaly] = []

    for sensor in sensors:
        reading = None
        sensor_type = None
        sensor_id_val = None
        element_id_val = None
        location_xyz_val = None

        # Support both ORM objects and plain dicts
        if hasattr(sensor, "latest_reading"):
            reading = sensor.latest_reading
            sensor_type = sensor.sensor_type
            sensor_id_val = sensor.sensor_id
            element_id_val = getattr(sensor, "element_id", None)
            location_xyz_val = getattr(sensor, "location_xyz", None)
        elif isinstance(sensor, dict):
            reading = sensor.get("latest_reading")
            sensor_type = sensor.get("sensor_type")
            sensor_id_val = sensor.get("sensor_id")
            element_id_val = sensor.get("element_id")
            location_xyz_val = sensor.get("location_xyz")

        if reading is None or sensor_type is None:
            continue

        value = reading.get("value") if isinstance(reading, dict) else None
        if value is None:
            continue

        value = float(value)
        thresholds = SENSOR_THRESHOLDS.get(sensor_type)
        if thresholds is None:
            continue

        unit = SENSOR_UNITS.get(sensor_type, "")
        # SensorAnomaly.sensor_id is required; fall back to empty string
        # for dict-shaped sensors missing the id.
        sensor_id_str = sensor_id_val or ""

        # Check high thresholds (temperature, humidity, vibration, strain, dust, noise)
        alert_high = thresholds.get("alert_high")
        warn_high = thresholds.get("warn_high")

        if alert_high is not None and value >= alert_high:
            anomalies.append(
                SensorAnomaly(
                    sensor_id=sensor_id_str,
                    sensor_type=sensor_type,
                    value=value,
                    unit=unit,
                    level="alert",
                    threshold=alert_high,
                    message=(
                        f"{sensor_type} reading {value}{unit} exceeds alert "
                        f"threshold {alert_high}{unit}"
                    ),
                    element_id=element_id_val,
                    location_xyz=location_xyz_val,
                )
            )
        elif warn_high is not None and value >= warn_high:
            anomalies.append(
                SensorAnomaly(
                    sensor_id=sensor_id_str,
                    sensor_type=sensor_type,
                    value=value,
                    unit=unit,
                    level="warning",
                    threshold=warn_high,
                    message=(
                        f"{sensor_type} reading {value}{unit} exceeds warning "
                        f"threshold {warn_high}{unit}"
                    ),
                    element_id=element_id_val,
                    location_xyz=location_xyz_val,
                )
            )

        # Check low thresholds (concrete_cure)
        alert_low = thresholds.get("alert_low")
        warn_low = thresholds.get("warn_low")

        if alert_low is not None and value <= alert_low:
            anomalies.append(
                SensorAnomaly(
                    sensor_id=sensor_id_str,
                    sensor_type=sensor_type,
                    value=value,
                    unit=unit,
                    level="alert",
                    threshold=alert_low,
                    message=(
                        f"{sensor_type} reading {value}{unit} below alert "
                        f"threshold {alert_low}{unit}"
                    ),
                    element_id=element_id_val,
                    location_xyz=location_xyz_val,
                )
            )
        elif warn_low is not None and value <= warn_low:
            anomalies.append(
                SensorAnomaly(
                    sensor_id=sensor_id_str,
                    sensor_type=sensor_type,
                    value=value,
                    unit=unit,
                    level="warning",
                    threshold=warn_low,
                    message=(
                        f"{sensor_type} reading {value}{unit} below warning "
                        f"threshold {warn_low}{unit}"
                    ),
                    element_id=element_id_val,
                    location_xyz=location_xyz_val,
                )
            )

    return anomalies


async def get_element_sensors(db: AsyncSession, twin_id: uuid.UUID, element_id: str) -> list[Any]:
    """Return all sensors attached to a specific IFC element."""
    from app.models.digital_twin import TwinSensorLink

    result = await db.execute(
        select(TwinSensorLink).where(
            TwinSensorLink.twin_id == twin_id,
            TwinSensorLink.element_id == element_id,
        )
    )
    return list(result.scalars().all())


async def list_twins(db: AsyncSession, project_id: uuid.UUID) -> list[Any]:
    """List all digital twin models for a project."""
    from app.models.digital_twin import DigitalTwinModel

    result = await db.execute(
        select(DigitalTwinModel)
        .where(DigitalTwinModel.project_id == project_id)
        .order_by(DigitalTwinModel.created_at.desc())
    )
    return list(result.scalars().all())


async def list_snapshots(
    db: AsyncSession,
    twin_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> list[Any]:
    """List snapshots for a twin, most recent first."""
    from app.models.digital_twin import TwinSnapshot

    result = await db.execute(
        select(TwinSnapshot)
        .where(TwinSnapshot.twin_id == twin_id)
        .order_by(TwinSnapshot.snapshot_date.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# SV-36: 3D data export for visualization (Three.js compatible)
# ---------------------------------------------------------------------------


async def export_twin_data(db: AsyncSession, twin_id: uuid.UUID) -> dict:
    """Export twin data as a JSON structure for 3D visualization.

    Returns a dict suitable for consumption by a Three.js frontend:
    - model_bounds: overall model bounding box
    - element_bounds: per-element bounding boxes (if available from SV-35)
    - sensors: list of sensor positions with latest readings and anomaly status
    - latest_snapshot: schedule overlay and photo URLs
    - metadata: twin metadata (source type, element count, etc.)

    Raises ValueError if the twin is not found.
    """
    from app.models.digital_twin import DigitalTwinModel, TwinSensorLink, TwinSnapshot

    twin = await db.get(DigitalTwinModel, twin_id)
    if twin is None:
        raise ValueError(f"Digital twin {twin_id} not found")

    # Fetch sensors
    sensor_result = await db.execute(
        select(TwinSensorLink).where(TwinSensorLink.twin_id == twin_id)
    )
    sensors = list(sensor_result.scalars().all())

    # Detect anomalies for current readings
    anomalies = detect_sensor_anomalies(sensors)
    anomaly_sensor_ids = {a.sensor_id for a in anomalies}

    sensor_data = []
    for s in sensors:
        sensor_entry = {
            "sensor_id": s.sensor_id,
            "sensor_type": s.sensor_type,
            "position": s.location_xyz,
            "element_id": s.element_id,
            "latest_reading": s.latest_reading,
            "last_updated": s.last_updated.isoformat() if s.last_updated else None,
            "has_anomaly": s.sensor_id in anomaly_sensor_ids,
        }
        sensor_data.append(sensor_entry)

    # Fetch latest snapshot
    snap_result = await db.execute(
        select(TwinSnapshot)
        .where(TwinSnapshot.twin_id == twin_id)
        .order_by(TwinSnapshot.snapshot_date.desc())
        .limit(1)
    )
    latest_snap = snap_result.scalar_one_or_none()

    snapshot_data = None
    if latest_snap:
        snapshot_data = {
            "id": str(latest_snap.id),
            "snapshot_date": latest_snap.snapshot_date.isoformat(),
            "schedule_overlay": latest_snap.schedule_overlay or {},
            "photo_overlay_urls": latest_snap.photo_overlay_urls or [],
            "notes": latest_snap.notes,
        }

    # Extract element bounds from metadata if available (SV-35)
    element_bounds = None
    if twin.metadata_ and isinstance(twin.metadata_, dict):
        element_bounds = twin.metadata_.get("element_bounds")

    export = {
        "twin_id": str(twin.id),
        "project_id": str(twin.project_id),
        "name": twin.name,
        "source_type": twin.source_type,
        "status": twin.status,
        "model_bounds": twin.bounds,
        "element_bounds": element_bounds,
        "element_count": twin.element_count,
        "coordinate_system": twin.coordinate_system,
        "sensors": sensor_data,
        "anomalies": [
            {
                "sensor_id": a.sensor_id,
                "sensor_type": a.sensor_type,
                "level": a.level,
                "value": a.value,
                "threshold": a.threshold,
                "message": a.message,
            }
            for a in anomalies
        ],
        "latest_snapshot": snapshot_data,
    }

    logger.info(
        "Exported twin data for visualization: twin=%s, sensors=%d, anomalies=%d",
        twin_id,
        len(sensor_data),
        len(anomalies),
    )
    return export
