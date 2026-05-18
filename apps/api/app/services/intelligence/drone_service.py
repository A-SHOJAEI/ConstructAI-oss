"""Drone/UAV data integration: flight logs, capture uploads, and earthwork volume.

Provides real earthwork volume calculation from point cloud data using grid-based
and cross-section (average end area) methods with numpy for performance.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False
    logger.warning("numpy not installed; earthwork volume calculations unavailable")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_CAPTURE_TYPES = {"orthomosaic", "point_cloud", "video", "thermal", "photo"}

SUPPORTED_CAPTURE_FORMATS: dict[str, list[str]] = {
    "orthomosaic": [".tif", ".tiff"],
    "point_cloud": [".las", ".laz", ".ply", ".e57"],
    "video": [".mp4", ".mov"],
    "thermal": [".tif"],
    "photo": [".jpg", ".jpeg", ".png", ".dng"],
}

VOLUME_METHODS = {"grid", "tin", "cross_section"}

# Conversion: 1 cubic foot = 1/27 cubic yard
_CF_TO_CY = Decimal("1") / Decimal("27")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VolumeComparison:
    """Comparison of earthwork volumes between two capture dates."""

    zone_name: str
    before_date: str
    after_date: str
    before_cut_cy: Decimal
    before_fill_cy: Decimal
    after_cut_cy: Decimal
    after_fill_cy: Decimal
    delta_cut_cy: Decimal  # positive = more cut
    delta_fill_cy: Decimal  # positive = more fill
    delta_net_cy: Decimal
    progress_pct: float  # estimated earthwork progress


@dataclass
class FlightSummary:
    """Aggregate summary of drone flights for a project."""

    project_id: str
    total_flights: int
    total_area_covered_sf: Decimal
    total_flight_minutes: int
    captures_by_type: dict[str, int] = field(default_factory=dict)
    date_range: dict[str, str | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core volume math
# ---------------------------------------------------------------------------


def _grid_volume_calculation(
    points: Any,
    grid_spacing: float,
    reference_elevation: float,
    bounds: dict | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate cut/fill volumes using the grid method.

    Divides the area into a regular grid, computes the average elevation
    per cell, and calculates the volume above/below the reference elevation.

    Parameters
    ----------
    points : np.ndarray
        Nx3 array of (x, y, z) coordinates in feet.
    grid_spacing : float
        Grid cell size in feet.
    reference_elevation : float
        Reference elevation in feet; material above this is "cut",
        below is "fill".
    bounds : dict, optional
        Override bounds: {"min_x", "max_x", "min_y", "max_y"}.

    Returns
    -------
    (cut_cy, fill_cy, surface_area_sf) as Decimals.
    """
    if not _HAS_NUMPY:
        raise RuntimeError("numpy is required for volume calculations")

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] < 3:
        raise ValueError("Points must be an Nx3 array of (x, y, z)")

    if points.shape[0] == 0:
        return Decimal("0"), Decimal("0"), Decimal("0")

    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    # Determine bounds
    if bounds:
        min_x = float(bounds.get("min_x", x.min()))
        max_x = float(bounds.get("max_x", x.max()))
        min_y = float(bounds.get("min_y", y.min()))
        max_y = float(bounds.get("max_y", y.max()))
    else:
        min_x, max_x = float(x.min()), float(x.max())
        min_y, max_y = float(y.min()), float(y.max())

    # Create grid
    n_cols = max(1, int(np.ceil((max_x - min_x) / grid_spacing)))
    n_rows = max(1, int(np.ceil((max_y - min_y) / grid_spacing)))

    # Assign each point to a grid cell
    col_idx = np.clip(((x - min_x) / grid_spacing).astype(int), 0, n_cols - 1)
    row_idx = np.clip(((y - min_y) / grid_spacing).astype(int), 0, n_rows - 1)

    # Compute average elevation per cell
    cell_idx = row_idx * n_cols + col_idx
    n_cells = n_rows * n_cols

    # Sum elevations and counts per cell
    cell_sum = np.bincount(cell_idx, weights=z, minlength=n_cells)
    cell_count = np.bincount(cell_idx, minlength=n_cells)

    # Only consider cells with data points
    valid = cell_count > 0
    avg_elevation = np.zeros(n_cells, dtype=np.float64)
    avg_elevation[valid] = cell_sum[valid] / cell_count[valid]

    # Volume per cell = cell_area * (elevation - reference)
    cell_area = grid_spacing * grid_spacing  # sq ft

    diff = avg_elevation - reference_elevation
    # Cut: positive diff (ground is above reference, needs excavation)
    # Fill: negative diff (ground is below reference, needs fill)

    cut_cf = float(np.sum(np.where((diff > 0) & valid, diff * cell_area, 0.0)))
    fill_cf = float(np.sum(np.where((diff < 0) & valid, abs(diff) * cell_area, 0.0)))

    # Surface area = number of valid cells * cell_area
    surface_area_sf = float(np.sum(valid)) * cell_area

    # Convert cubic feet to cubic yards
    cut_cy = (Decimal(str(cut_cf)) * _CF_TO_CY).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    fill_cy = (Decimal(str(fill_cf)) * _CF_TO_CY).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    surface_area = Decimal(str(surface_area_sf)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return cut_cy, fill_cy, surface_area


def _cross_section_volume(
    points: Any,
    section_spacing: float,
    reference_elevation: float,
) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate cut/fill using the average end area (cross-section) method.

    Sorts points along the primary axis (X), groups into cross-sections
    at regular intervals, and applies the average end area formula:
        V = (A1 + A2) / 2 * distance

    Parameters
    ----------
    points : np.ndarray
        Nx3 array of (x, y, z) coordinates in feet.
    section_spacing : float
        Distance between cross-sections in feet.
    reference_elevation : float
        Reference elevation in feet.

    Returns
    -------
    (cut_cy, fill_cy, surface_area_sf) as Decimals.
    """
    if not _HAS_NUMPY:
        raise RuntimeError("numpy is required for volume calculations")

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] < 3:
        raise ValueError("Points must be an Nx3 array of (x, y, z)")

    if points.shape[0] == 0:
        return Decimal("0"), Decimal("0"), Decimal("0")

    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    min_x = float(x.min())
    max_x = float(x.max())
    min_y = float(y.min())
    max_y = float(y.max())
    y_width = max_y - min_y
    if y_width <= 0:
        y_width = 1.0

    # Generate section locations along X axis
    section_locs = np.arange(min_x, max_x + section_spacing, section_spacing)
    if len(section_locs) < 2:
        return Decimal("0"), Decimal("0"), Decimal("0")

    # For each section, compute the cross-sectional cut and fill areas
    half_spacing = section_spacing / 2.0

    section_cut_areas: list[float] = []
    section_fill_areas: list[float] = []

    for sx in section_locs:
        # Select points within half_spacing of this section
        mask = np.abs(x - sx) <= half_spacing
        if not np.any(mask):
            section_cut_areas.append(0.0)
            section_fill_areas.append(0.0)
            continue

        section_z = z[mask]
        section_y_vals = y[mask]

        # Sort by Y to compute cross-section area via trapezoidal rule
        sort_idx = np.argsort(section_y_vals)
        sy = section_y_vals[sort_idx]
        sz = section_z[sort_idx]

        diff = sz - reference_elevation

        # Cut area (where diff > 0) and fill area (where diff < 0)
        cut_diff = np.where(diff > 0, diff, 0.0)
        fill_diff = np.where(diff < 0, np.abs(diff), 0.0)

        # Trapezoidal integration along Y
        if len(sy) >= 2:
            cut_area = float(np.trapz(cut_diff, sy))
            fill_area = float(np.trapz(fill_diff, sy))
        else:
            # Single point: estimate area as point value * y_width
            cut_area = float(cut_diff[0]) * y_width
            fill_area = float(fill_diff[0]) * y_width

        section_cut_areas.append(max(0.0, cut_area))
        section_fill_areas.append(max(0.0, fill_area))

    # Average end area method: V = sum of (A1+A2)/2 * distance between sections
    total_cut_cf = 0.0
    total_fill_cf = 0.0

    for i in range(len(section_locs) - 1):
        dist = float(section_locs[i + 1] - section_locs[i])
        total_cut_cf += (section_cut_areas[i] + section_cut_areas[i + 1]) / 2.0 * dist
        total_fill_cf += (section_fill_areas[i] + section_fill_areas[i + 1]) / 2.0 * dist

    # Surface area estimate
    surface_area_sf = (max_x - min_x) * y_width

    cut_cy = (Decimal(str(total_cut_cf)) * _CF_TO_CY).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    fill_cy = (Decimal(str(total_fill_cf)) * _CF_TO_CY).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    surface_area = Decimal(str(surface_area_sf)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return cut_cy, fill_cy, surface_area


def _compute_volume_confidence(point_count: int, area_sf: float, grid_spacing: float) -> Decimal:
    """Compute a confidence score for the volume calculation.

    Based on point density: higher density = higher confidence.
    - >1 pt/sq ft: 0.95
    - 0.5-1 pt/sq ft: 0.85
    - 0.1-0.5 pt/sq ft: 0.70
    - <0.1 pt/sq ft: 0.50
    """
    if area_sf <= 0:
        return Decimal("0.50")

    density = point_count / area_sf
    if density >= 1.0:
        conf = 0.95
    elif density >= 0.5:
        conf = 0.85
    elif density >= 0.1:
        conf = 0.70
    else:
        conf = 0.50

    return Decimal(str(conf))


# ---------------------------------------------------------------------------
# Public API — Flight management
# ---------------------------------------------------------------------------


async def create_flight_log(
    db: AsyncSession,
    project_id: uuid.UUID,
    flight_date: datetime,
    drone_id: str | None = None,
    duration_minutes: int | None = None,
    area_covered_sf: Decimal | None = None,
    altitude_ft: Decimal | None = None,
    flight_path: list[dict] | None = None,
    weather_conditions: dict | None = None,
    operator_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> Any:
    """Create a drone flight log record."""
    from app.models.drone import DroneFlightLog

    flight = DroneFlightLog(
        project_id=project_id,
        drone_id=drone_id,
        flight_date=flight_date,
        duration_minutes=duration_minutes,
        area_covered_sf=area_covered_sf,
        altitude_ft=altitude_ft,
        flight_path=flight_path,
        weather_conditions=weather_conditions,
        operator_id=operator_id,
        notes=notes,
    )
    db.add(flight)
    await db.flush()
    await db.refresh(flight)

    logger.info(
        "Flight log created: flight=%s, project=%s, date=%s",
        flight.id,
        project_id,
        flight_date.isoformat(),
    )
    return flight


async def upload_capture(
    db: AsyncSession,
    flight_id: uuid.UUID,
    file_bytes: bytes,
    capture_type: str,
    filename: str,
    resolution: str | None = None,
    metadata: dict | None = None,
) -> Any:
    """Upload a drone capture (orthomosaic, point cloud, video, thermal, photo).

    Validates capture type and file extension, uploads to S3, creates record.
    """
    from app.models.drone import DroneCapture, DroneFlightLog
    from app.utils.s3 import upload_file

    # Validate capture type
    if capture_type not in SUPPORTED_CAPTURE_TYPES:
        raise ValueError(
            f"Unsupported capture type '{capture_type}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_CAPTURE_TYPES))}"
        )

    # Validate file extension
    ext = ""
    dot_idx = filename.rfind(".")
    if dot_idx >= 0:
        ext = filename[dot_idx:].lower()

    valid_exts = SUPPORTED_CAPTURE_FORMATS.get(capture_type, [])
    if valid_exts and ext not in valid_exts:
        raise ValueError(
            f"Invalid file extension '{ext}' for capture type '{capture_type}'. "
            f"Expected: {', '.join(valid_exts)}"
        )

    # Verify flight exists and get project_id
    flight = await db.get(DroneFlightLog, flight_id)
    if flight is None:
        raise ValueError(f"Flight {flight_id} not found")

    project_id = flight.project_id

    # Determine content type
    content_type_map = {
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".las": "application/octet-stream",
        ".laz": "application/octet-stream",
        ".ply": "application/octet-stream",
        ".e57": "application/octet-stream",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".dng": "image/x-adobe-dng",
    }
    content_type = content_type_map.get(ext, "application/octet-stream")

    # Upload to S3
    capture_id = uuid.uuid4()
    s3_key = f"drones/{project_id}/{flight_id}/{capture_id}/{filename}"
    upload_file(s3_key, file_bytes, content_type)

    # Extract point count for point clouds
    point_count = None
    if capture_type == "point_cloud":
        from app.services.intelligence.digital_twin_service import (
            _parse_point_cloud_header,
        )

        header = _parse_point_cloud_header(file_bytes, filename)
        point_count = header.get("point_count")

    capture = DroneCapture(
        id=capture_id,
        flight_id=flight_id,
        capture_type=capture_type,
        s3_key=s3_key,
        file_size_bytes=len(file_bytes),
        resolution=resolution,
        point_count=point_count,
        metadata_=metadata or {},
        processing_status="ready" if capture_type in ("photo", "video") else "uploaded",
    )
    db.add(capture)
    await db.flush()
    await db.refresh(capture)

    logger.info(
        "Capture uploaded: capture=%s, type=%s, flight=%s, size=%d bytes",
        capture.id,
        capture_type,
        flight_id,
        len(file_bytes),
    )
    return capture


# ---------------------------------------------------------------------------
# Public API — Earthwork volumes
# ---------------------------------------------------------------------------


async def calculate_earthwork_volume(
    db: AsyncSession,
    project_id: uuid.UUID,
    zone_name: str,
    points: Any,
    grid_spacing_ft: float = 5.0,
    reference_elevation_ft: float = 0.0,
    method: str = "grid",
    capture_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    notes: str | None = None,
) -> Any:
    """Calculate cut/fill earthwork volume from point data.

    Parameters
    ----------
    points : array-like
        Nx3 array of (x, y, z) coordinates in feet.
    grid_spacing_ft : float
        Grid cell size for the grid method, or section spacing for
        the cross-section method.
    reference_elevation_ft : float
        Reference elevation for cut/fill determination.
    method : str
        "grid" or "cross_section".

    Returns an EarthworkVolume record.
    """
    from app.models.drone import EarthworkVolume

    if not _HAS_NUMPY:
        raise RuntimeError("numpy is required for earthwork calculations")

    if method not in ("grid", "cross_section"):
        raise ValueError(f"Unsupported method '{method}'. Use 'grid' or 'cross_section'.")

    points_arr = np.asarray(points, dtype=np.float64)

    if method == "grid":
        cut_cy, fill_cy, surface_area_sf = _grid_volume_calculation(
            points_arr, grid_spacing_ft, reference_elevation_ft
        )
    else:
        cut_cy, fill_cy, surface_area_sf = _cross_section_volume(
            points_arr, grid_spacing_ft, reference_elevation_ft
        )

    net_cy = cut_cy - fill_cy

    area_float = float(surface_area_sf) if surface_area_sf else 0.0
    confidence = _compute_volume_confidence(len(points_arr), area_float, grid_spacing_ft)

    volume = EarthworkVolume(
        project_id=project_id,
        capture_id=capture_id,
        calculation_date=date.today(),
        zone_name=zone_name,
        cut_volume_cy=cut_cy,
        fill_volume_cy=fill_cy,
        net_volume_cy=net_cy,
        surface_area_sf=surface_area_sf,
        reference_elevation_ft=Decimal(str(reference_elevation_ft)),
        method=method,
        confidence=confidence,
        notes=notes,
        created_by=created_by,
    )
    db.add(volume)
    await db.flush()
    await db.refresh(volume)

    logger.info(
        "Earthwork volume calculated: zone=%s, cut=%.2f CY, fill=%.2f CY, "
        "net=%.2f CY, method=%s, confidence=%.2f",
        zone_name,
        cut_cy,
        fill_cy,
        net_cy,
        method,
        confidence,
    )
    return volume


async def compare_captures(
    db: AsyncSession,
    capture_id_before: uuid.UUID,
    capture_id_after: uuid.UUID,
    zone_name: str,
) -> VolumeComparison:
    """Compare earthwork volumes between two captures (before/after).

    Looks up the most recent EarthworkVolume records linked to each capture
    for the specified zone and computes the delta.
    """
    from app.models.drone import DroneCapture, EarthworkVolume

    # Fetch volumes for each capture
    before_result = await db.execute(
        select(EarthworkVolume)
        .where(
            EarthworkVolume.capture_id == capture_id_before,
            EarthworkVolume.zone_name == zone_name,
        )
        .order_by(EarthworkVolume.calculation_date.desc())
        .limit(1)
    )
    before_vol = before_result.scalar_one_or_none()

    after_result = await db.execute(
        select(EarthworkVolume)
        .where(
            EarthworkVolume.capture_id == capture_id_after,
            EarthworkVolume.zone_name == zone_name,
        )
        .order_by(EarthworkVolume.calculation_date.desc())
        .limit(1)
    )
    after_vol = after_result.scalar_one_or_none()

    if before_vol is None:
        raise ValueError(
            f"No earthwork volume found for capture {capture_id_before} in zone '{zone_name}'"
        )
    if after_vol is None:
        raise ValueError(
            f"No earthwork volume found for capture {capture_id_after} in zone '{zone_name}'"
        )

    # Compute deltas
    delta_cut = after_vol.cut_volume_cy - before_vol.cut_volume_cy
    delta_fill = after_vol.fill_volume_cy - before_vol.fill_volume_cy
    delta_net = after_vol.net_volume_cy - before_vol.net_volume_cy

    # Estimate progress percentage based on volume change
    # Use the total expected work (before volumes) as baseline
    total_before = abs(before_vol.cut_volume_cy) + abs(before_vol.fill_volume_cy)
    abs(after_vol.cut_volume_cy) + abs(after_vol.fill_volume_cy)
    total_change = abs(delta_cut) + abs(delta_fill)

    if total_before > 0:
        progress_pct = float(
            min(
                Decimal("100"),
                (total_change / total_before) * Decimal("100"),
            )
        )
    else:
        progress_pct = 0.0

    # Get dates from captures
    await db.get(DroneCapture, capture_id_before)
    await db.get(DroneCapture, capture_id_after)

    before_date_str = before_vol.calculation_date.isoformat()
    after_date_str = after_vol.calculation_date.isoformat()

    return VolumeComparison(
        zone_name=zone_name,
        before_date=before_date_str,
        after_date=after_date_str,
        before_cut_cy=before_vol.cut_volume_cy,
        before_fill_cy=before_vol.fill_volume_cy,
        after_cut_cy=after_vol.cut_volume_cy,
        after_fill_cy=after_vol.fill_volume_cy,
        delta_cut_cy=delta_cut,
        delta_fill_cy=delta_fill,
        delta_net_cy=delta_net,
        progress_pct=round(progress_pct, 2),
    )


# ---------------------------------------------------------------------------
# Public API — Queries
# ---------------------------------------------------------------------------


async def get_flight_summary(
    db: AsyncSession,
    project_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
) -> FlightSummary:
    """Aggregate summary of drone flights and captures for a project."""
    from app.models.drone import DroneCapture, DroneFlightLog

    # Base query
    flight_query = select(DroneFlightLog).where(DroneFlightLog.project_id == project_id)
    if date_from:
        flight_query = flight_query.where(
            DroneFlightLog.flight_date >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        flight_query = flight_query.where(
            DroneFlightLog.flight_date <= datetime.combine(date_to, datetime.max.time())
        )

    result = await db.execute(flight_query.order_by(DroneFlightLog.flight_date))
    flights = list(result.scalars().all())

    total_flights = len(flights)
    total_area = Decimal("0")
    total_minutes = 0
    first_date = None
    last_date = None

    for flight in flights:
        if flight.area_covered_sf:
            total_area += flight.area_covered_sf
        if flight.duration_minutes:
            total_minutes += flight.duration_minutes
        flight_d = flight.flight_date
        if first_date is None or flight_d < first_date:
            first_date = flight_d
        if last_date is None or flight_d > last_date:
            last_date = flight_d

    # Captures by type
    captures_by_type: dict[str, int] = {}
    if flights:
        flight_ids = [f.id for f in flights]
        cap_result = await db.execute(
            select(DroneCapture.capture_type, func.count(DroneCapture.id))
            .where(DroneCapture.flight_id.in_(flight_ids))
            .group_by(DroneCapture.capture_type)
        )
        for cap_type, count in cap_result.all():
            captures_by_type[cap_type] = count

    return FlightSummary(
        project_id=str(project_id),
        total_flights=total_flights,
        total_area_covered_sf=total_area,
        total_flight_minutes=total_minutes,
        captures_by_type=captures_by_type,
        date_range={
            "first": first_date.isoformat() if first_date else None,
            "last": last_date.isoformat() if last_date else None,
        },
    )


async def list_flights(
    db: AsyncSession,
    project_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Any], int]:
    """List flight logs for a project with total count."""
    from app.models.drone import DroneFlightLog

    # Count
    count_result = await db.execute(
        select(func.count(DroneFlightLog.id)).where(DroneFlightLog.project_id == project_id)
    )
    total = count_result.scalar() or 0

    # Fetch
    result = await db.execute(
        select(DroneFlightLog)
        .where(DroneFlightLog.project_id == project_id)
        .order_by(DroneFlightLog.flight_date.desc())
        .offset(skip)
        .limit(limit)
    )
    flights = list(result.scalars().all())

    return flights, total


async def list_captures(db: AsyncSession, flight_id: uuid.UUID) -> list[Any]:
    """List all captures for a flight."""
    from app.models.drone import DroneCapture

    result = await db.execute(
        select(DroneCapture)
        .where(DroneCapture.flight_id == flight_id)
        .order_by(DroneCapture.created_at.desc())
    )
    return list(result.scalars().all())


async def list_earthwork_volumes(
    db: AsyncSession,
    project_id: uuid.UUID,
    zone_name: str | None = None,
) -> list[Any]:
    """List earthwork volume records for a project, optionally filtered by zone."""
    from app.models.drone import EarthworkVolume

    query = select(EarthworkVolume).where(EarthworkVolume.project_id == project_id)
    if zone_name:
        query = query.where(EarthworkVolume.zone_name == zone_name)

    query = query.order_by(EarthworkVolume.calculation_date.desc())
    result = await db.execute(query)
    return list(result.scalars().all())
