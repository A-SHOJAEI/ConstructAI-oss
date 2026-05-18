"""AI progress tracking from site photos.

Maps YOLO object detections to schedule activities via CSI code mapping
and activity keyword matching.  Estimates percent complete per activity
based on equipment presence, worker density, and historical comparisons.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection → CSI division mapping
# ---------------------------------------------------------------------------

DETECTION_TO_CSI_MAP: dict[str, list[str]] = {
    "excavator": ["31 00 00"],
    "crane": ["05 00 00", "03 00 00"],
    "concrete_mixer": ["03 00 00"],
    "scaffolding": ["01 50 00"],
    "dump_truck": ["31 00 00"],
    "loader": ["31 00 00"],
}

# Detections that indicate worker presence (used for headcount)
_WORKER_CLASSES = {"person", "hard_hat", "safety_vest"}


# ---------------------------------------------------------------------------
# CSI division → activity keyword mapping
# ---------------------------------------------------------------------------

ACTIVITY_PHASE_KEYWORDS: dict[str, list[str]] = {
    "31 00 00": ["excavat", "grade", "backfill", "trench", "earthwork", "site"],
    "03 00 00": ["concrete", "pour", "formwork", "rebar", "foundation", "slab", "footing"],
    "05 00 00": ["steel", "erect", "weld", "iron", "beam", "column", "structural steel"],
    "01 50 00": ["temporary", "scaffold", "shoring", "protection"],
    "04 00 00": ["masonry", "brick", "block", "mortar", "cmu"],
    "06 00 00": ["wood", "framing", "carpentry", "lumber", "plywood"],
    "07 00 00": ["roofing", "waterproof", "insulation", "membrane", "flashing"],
    "08 00 00": ["door", "window", "glazing", "curtain wall", "storefront"],
    "09 00 00": ["drywall", "paint", "finish", "flooring", "ceiling", "tile"],
    "22 00 00": ["plumb", "pipe", "sanitary", "water line", "drain"],
    "23 00 00": ["hvac", "duct", "mechanical", "air handling"],
    "26 00 00": ["electr", "wiring", "conduit", "panel", "switchgear"],
    "32 00 00": ["paving", "landscape", "curb", "sidewalk", "asphalt"],
    "33 00 00": ["utility", "sewer", "storm", "water main"],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ActivityMatch:
    """A match between a detection and a schedule activity."""

    activity_id: str
    activity_name: str
    detection_class: str
    csi_division: str
    match_score: float  # 0.0 - 1.0
    detection_confidence: float


@dataclass
class ProgressAnalysisResult:
    """Result of analyzing a single progress photo."""

    photo_id: str
    project_id: str
    worker_count: int
    equipment_detected: list[dict]
    activity_matches: list[ActivityMatch]
    estimated_progress: dict[str, Decimal]  # activity_id -> pct
    overall_confidence: Decimal


@dataclass
class ProgressVariance:
    """Variance between AI-estimated and scheduled progress."""

    activity_id: str
    activity_name: str
    scheduled_pct: Decimal
    estimated_pct: Decimal
    variance_pct: Decimal  # positive = ahead, negative = behind
    status: str  # "ahead", "behind", "on_track"


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


async def analyze_progress_photo(
    db: AsyncSession,
    project_id: uuid.UUID,
    photo_bytes: bytes,
    photo_url: str,
    uploaded_by: uuid.UUID | None = None,
) -> ProgressAnalysisResult:
    """Run YOLO detection on a photo and map results to schedule activities.

    1. Runs object detection on the photo bytes.
    2. Maps detections to schedule activities via CSI codes + keywords.
    3. Estimates percent complete per matched activity.
    4. Stores a ProgressPhoto record.
    5. Returns the full analysis result.
    """
    from app.models.progress_tracking import ProgressPhoto
    from app.models.scheduling import ScheduleActivity

    # Step 1: Run YOLO detection
    detections = await _run_detection(photo_bytes)

    # Count workers and catalog equipment
    worker_count = 0
    equipment_detected: list[dict] = []
    for det in detections:
        if det["class_name"] in _WORKER_CLASSES:
            worker_count += 1
        elif det["class_name"] in DETECTION_TO_CSI_MAP:
            equipment_detected.append(
                {
                    "class_name": det["class_name"],
                    "confidence": det["confidence"],
                    "bbox": det.get("bbox"),
                }
            )

    # Step 2: Fetch schedule activities for this project
    result = await db.execute(
        select(ScheduleActivity).where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.status.in_(["not_started", "in_progress"]),
        )
    )
    activities = list(result.scalars().all())
    activity_dicts = [
        {
            "id": str(a.id),
            "name": a.name,
            "activity_code": a.activity_code,
            "wbs_code": a.wbs_code,
            "pct_complete": a.pct_complete,
            "duration_days": a.duration_days,
            "start_date": a.start_date,
            "finish_date": a.finish_date,
            "resource_assignments": a.resource_assignments,
        }
        for a in activities
    ]

    # Step 3: Map detections to activities
    matches = map_detections_to_activities(detections, activity_dicts)

    # Step 4: Fetch historical snapshots for trend comparison
    from app.models.progress_tracking import ProgressSnapshot

    hist_result = await db.execute(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.snapshot_date.desc())
        .limit(5)
    )
    historical = list(hist_result.scalars().all())
    hist_dicts = [
        {"activities_progress": s.activities_progress, "snapshot_date": s.snapshot_date}
        for s in historical
    ]

    # Step 5: Estimate progress
    estimated_progress = estimate_percent_complete(matches, hist_dicts)

    # Calculate overall confidence
    if matches:
        avg_conf = sum(m.detection_confidence * m.match_score for m in matches) / len(matches)
        overall_confidence = Decimal(str(round(min(avg_conf, 0.99), 2)))
    else:
        overall_confidence = Decimal("0.00")

    # Step 6: Persist ProgressPhoto
    s3_key = f"progress/{project_id}/{uuid.uuid4()}.jpg"
    photo_record = ProgressPhoto(
        project_id=project_id,
        photo_url=photo_url,
        s3_key=s3_key,
        detections=[
            {
                "class_name": d["class_name"],
                "confidence": d["confidence"],
                "bbox": d.get("bbox"),
            }
            for d in detections
        ],
        matched_activities=[
            {
                "activity_id": m.activity_id,
                "activity_name": m.activity_name,
                "detection_class": m.detection_class,
                "match_score": m.match_score,
            }
            for m in matches
        ],
        overall_confidence=overall_confidence,
        uploaded_by=uploaded_by,
    )
    db.add(photo_record)
    await db.flush()
    await db.refresh(photo_record)

    analysis_result = ProgressAnalysisResult(
        photo_id=str(photo_record.id),
        project_id=str(project_id),
        worker_count=worker_count,
        equipment_detected=equipment_detected,
        activity_matches=matches,
        estimated_progress=estimated_progress,
        overall_confidence=overall_confidence,
    )

    # IG-15: Check for recent drone captures for cross-reference.
    # If any DroneCapture records exist for this project within the last 7 days,
    # attach them as informational cross-references for the user to compare.
    try:
        from datetime import datetime as _dt
        from datetime import timedelta

        from app.models.drone import DroneCapture, DroneFlightLog

        seven_days_ago = _dt.utcnow() - timedelta(days=7)
        drone_result = await db.execute(
            select(DroneCapture)
            .join(DroneFlightLog, DroneCapture.flight_id == DroneFlightLog.id)
            .where(
                DroneFlightLog.project_id == project_id,
                DroneCapture.created_at >= seven_days_ago,
            )
            .order_by(DroneCapture.created_at.desc())
            .limit(10)
        )
        recent_captures = list(drone_result.scalars().all())

        if recent_captures:
            analysis_result.related_drone_captures = [  # type: ignore[attr-defined]
                {
                    "capture_id": str(c.id),
                    "capture_type": c.capture_type,
                    "s3_key": c.s3_key,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "processing_status": c.processing_status,
                }
                for c in recent_captures
            ]
            logger.info(
                "Found %d drone captures within last 7 days for project %s",
                len(recent_captures),
                project_id,
            )
    except Exception:
        logger.warning(
            "Failed to check for related drone captures for project %s",
            project_id,
            exc_info=True,
        )

    return analysis_result


def map_detections_to_activities(
    detections: list[dict],
    activities: list[dict],
) -> list[ActivityMatch]:
    """Map CV detections to schedule activities.

    For each equipment detection, finds matching activities by:
    1. Looking up the CSI division(s) for the detected class.
    2. Searching activity names/descriptions for matching keywords.
    3. Scoring matches by detection confidence and keyword relevance.
    """
    matches: list[ActivityMatch] = []
    seen_pairs: set[tuple[str, str]] = set()  # (activity_id, detection_class)

    for det in detections:
        class_name = det["class_name"]
        confidence = det["confidence"]

        # Skip worker detections — those are used for headcount, not activity matching
        if class_name in _WORKER_CLASSES:
            continue

        csi_divisions = DETECTION_TO_CSI_MAP.get(class_name, [])
        if not csi_divisions:
            continue

        for csi_div in csi_divisions:
            keywords = ACTIVITY_PHASE_KEYWORDS.get(csi_div, [])
            if not keywords:
                continue

            for activity in activities:
                activity_id = str(activity["id"])
                pair_key = (activity_id, class_name)
                if pair_key in seen_pairs:
                    continue

                act_name = (activity.get("name") or "").lower()
                act_code = (activity.get("activity_code") or "").lower()
                act_wbs = (activity.get("wbs_code") or "").lower()
                search_text = f"{act_name} {act_code} {act_wbs}"

                # Score keyword matches
                matched_keywords = [kw for kw in keywords if kw.lower() in search_text]
                if not matched_keywords:
                    continue

                # Match score: combine keyword coverage and detection confidence
                keyword_score = len(matched_keywords) / len(keywords)
                match_score = min(1.0, keyword_score * 0.6 + confidence * 0.4)

                seen_pairs.add(pair_key)
                matches.append(
                    ActivityMatch(
                        activity_id=activity_id,
                        activity_name=activity.get("name", ""),
                        detection_class=class_name,
                        csi_division=csi_div,
                        match_score=round(match_score, 3),
                        detection_confidence=confidence,
                    )
                )

    # Sort by match score descending
    matches.sort(key=lambda m: m.match_score, reverse=True)
    return matches


def estimate_percent_complete(
    matches: list[ActivityMatch],
    historical_snapshots: list[dict] | None = None,
) -> dict[str, Decimal]:
    """Estimate percent complete per matched activity.

    Heuristics:
    - Equipment presence on site indicates the activity is underway (base 15-25%).
    - Higher detection confidence raises the estimate.
    - Historical comparison: if the activity was at X% yesterday and equipment
      is still present, estimate X + delta.
    - Conservative: never estimate above 85% from photos alone.
    - SV-16 Completion detection: if equipment AND worker counts are DECREASING
      compared to the previous snapshot (activity winding down), allow up to 95%.
      The final 5% (punch list / closeout) is human-confirmed only.
    - Multiple equipment types for the same activity raise confidence.
    - SV-17 Temporal correlation: if the same equipment type was detected at
      the same activity in the previous 2+ snapshots, boost confidence by 10%
      (indicates sustained work, not a one-off sighting).
    - SV-18 Worker activity recognition: worker count patterns near specific
      equipment types refine activity phase estimates.
    """
    if not matches:
        return {}

    # Group matches by activity_id
    activity_matches: dict[str, list[ActivityMatch]] = {}
    for m in matches:
        activity_matches.setdefault(m.activity_id, []).append(m)

    # Build historical lookup: activity_id -> last known pct
    last_known: dict[str, Decimal] = {}
    # SV-16: Also track equipment/worker counts from historical snapshots
    # to detect winding-down activities.
    prev_equipment_counts: dict[str, int] = {}
    prev_worker_counts: dict[str, int] = {}
    if historical_snapshots:
        # Use the most recent snapshot
        for snap in historical_snapshots:
            progress = snap.get("activities_progress", {})
            for aid, pct in progress.items():
                if aid not in last_known:
                    last_known[aid] = Decimal(str(pct))
            # Extract per-activity detection counts from snapshot metadata
            equipment_counts = snap.get("equipment_counts", {})
            worker_counts = snap.get("worker_counts", {})
            for aid, cnt in equipment_counts.items():
                if aid not in prev_equipment_counts:
                    prev_equipment_counts[aid] = int(cnt)
            for aid, cnt in worker_counts.items():
                if aid not in prev_worker_counts:
                    prev_worker_counts[aid] = int(cnt)

    # SV-16: Compute current equipment and worker counts per activity
    # from the current matches for comparison.
    current_equipment_counts: dict[str, int] = {}
    current_worker_counts: dict[str, int] = {}
    # SV-17: Track current equipment types per activity for temporal correlation
    current_equipment_types: dict[str, set[str]] = {}
    for m in matches:
        if m.detection_class not in _WORKER_CLASSES:
            current_equipment_counts[m.activity_id] = (
                current_equipment_counts.get(m.activity_id, 0) + 1
            )
            current_equipment_types.setdefault(m.activity_id, set()).add(m.detection_class)
        else:
            current_worker_counts[m.activity_id] = current_worker_counts.get(m.activity_id, 0) + 1

    # SV-17: Build historical equipment type presence per activity across snapshots.
    # Count how many consecutive prior snapshots had the same equipment type at each activity.
    equipment_streak: dict[str, int] = {}  # activity_id -> consecutive snapshot count
    if historical_snapshots and len(historical_snapshots) >= 2:
        for activity_id, equip_types in current_equipment_types.items():
            streak = 0
            for snap in historical_snapshots:
                snap_equip = snap.get("equipment_types", {})
                past_types = set(snap_equip.get(activity_id, []))
                if equip_types & past_types:  # any overlap
                    streak += 1
                else:
                    break  # streak broken
            equipment_streak[activity_id] = streak

    # SV-18: Worker activity recognition mapping.
    # High worker count near specific equipment indicates specific phases.
    _WORKER_PHASE_MAP: dict[str, dict[str, str]] = {
        "concrete_mixer": {"high_workers": "pour_phase", "low_workers": "prep_phase"},
        "crane": {"high_workers": "erection_phase", "low_workers": "staging_phase"},
        "excavator": {"high_workers": "active_excavation", "low_workers": "grading_phase"},
        "scaffolding": {"high_workers": "forming_phase", "low_workers": "setup_phase"},
    }
    activity_phase_refinements: dict[str, str] = {}
    for activity_id, equip_types in current_equipment_types.items():
        worker_count = current_worker_counts.get(activity_id, 0)
        for equip_type in equip_types:
            phase_map = _WORKER_PHASE_MAP.get(equip_type)
            if phase_map:
                # "high" = 3+ workers near this equipment type
                if worker_count >= 3:
                    activity_phase_refinements[activity_id] = phase_map["high_workers"]
                else:
                    activity_phase_refinements[activity_id] = phase_map["low_workers"]

    estimates: dict[str, Decimal] = {}
    for activity_id, act_matches in activity_matches.items():
        # Base estimate from equipment presence
        best_match = max(act_matches, key=lambda m: m.match_score)
        base_pct = Decimal("15") + Decimal(str(round(best_match.match_score * 10, 1)))

        # Boost for multiple different equipment types
        unique_classes = {m.detection_class for m in act_matches}
        if len(unique_classes) > 1:
            base_pct += Decimal("5") * (len(unique_classes) - 1)

        # Historical comparison: use last known + small delta
        prev_pct = last_known.get(activity_id)
        if prev_pct is not None:
            # Activity was already tracked: assume progress since last photo
            # Minimum: previous value (never go backwards)
            # Delta: 2-5% progress per observation
            delta = Decimal("2") + Decimal(str(round(best_match.match_score * 3, 1)))
            historical_estimate = prev_pct + delta
            base_pct = max(base_pct, historical_estimate)

        # SV-17: Temporal correlation boost — if the same equipment was seen
        # at this activity in 2+ consecutive prior snapshots, boost by 10%
        # (sustained work, not a one-off sighting).
        streak = equipment_streak.get(activity_id, 0)
        if streak >= 2:
            base_pct = base_pct * Decimal("1.10")

        # SV-18: Refine based on worker activity phase.
        # Active phases (pour, erection, active excavation) get a small boost.
        phase = activity_phase_refinements.get(activity_id)
        if phase and phase in (
            "pour_phase",
            "erection_phase",
            "active_excavation",
            "forming_phase",
        ):
            base_pct += Decimal("3")

        # SV-16: Completion detection — if both equipment and worker counts
        # are decreasing compared to the previous snapshot, the activity is
        # likely winding down. Allow estimates up to 95% (the final 5% is
        # punch list / closeout, human-confirmed only).
        activity_winding_down = False
        if prev_pct is not None and prev_pct >= Decimal("70"):
            prev_equip = prev_equipment_counts.get(activity_id, 0)
            curr_equip = current_equipment_counts.get(activity_id, 0)
            prev_workers = prev_worker_counts.get(activity_id, 0)
            curr_workers = current_worker_counts.get(activity_id, 0)

            if prev_equip > 0 and curr_equip < prev_equip and curr_workers < prev_workers:
                activity_winding_down = True

        if activity_winding_down:
            # Allow up to 95% for winding-down activities
            base_pct = min(base_pct, Decimal("95"))
        else:
            # Standard cap at 85% — full completion requires manual confirmation
            base_pct = min(base_pct, Decimal("85"))

        base_pct = max(base_pct, Decimal("5"))  # minimum 5%

        estimates[activity_id] = base_pct.quantize(Decimal("0.01"))

    return estimates


async def compare_against_schedule(
    db: AsyncSession,
    project_id: uuid.UUID,
    estimated_progress: dict[str, Decimal],
) -> list[ProgressVariance]:
    """Compare AI estimates against the scheduled percent complete.

    Returns a list of variance records indicating whether each activity
    is ahead, behind, or on track relative to the schedule.
    """
    from app.models.scheduling import ScheduleActivity

    if not estimated_progress:
        return []

    # Fetch current schedule data for matched activities
    activity_ids = [uuid.UUID(aid) for aid in estimated_progress]
    result = await db.execute(
        select(ScheduleActivity).where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.id.in_(activity_ids),
        )
    )
    activities = {str(a.id): a for a in result.scalars().all()}

    variances: list[ProgressVariance] = []
    for activity_id, estimated_pct in estimated_progress.items():
        activity = activities.get(activity_id)
        if not activity:
            continue

        scheduled_pct = activity.pct_complete or Decimal("0")
        variance = estimated_pct - scheduled_pct

        # Determine status based on variance magnitude
        if variance > Decimal("5"):
            status = "ahead"
        elif variance < Decimal("-5"):
            status = "behind"
        else:
            status = "on_track"

        variances.append(
            ProgressVariance(
                activity_id=activity_id,
                activity_name=activity.name,
                scheduled_pct=scheduled_pct,
                estimated_pct=estimated_pct,
                variance_pct=variance,
                status=status,
            )
        )

    return variances


async def auto_update_schedule_progress(
    db: AsyncSession,
    project_id: uuid.UUID,
    progress_updates: dict[str, Decimal],
    snapshot_id: uuid.UUID | None = None,
) -> int:
    """Update ScheduleActivity records with AI-estimated progress.

    Sets ``ai_pct_complete`` and ``last_photo_update`` metadata fields
    (stored in the activity's JSONB metadata column) for each matched
    activity. Does NOT overwrite the official ``pct_complete`` field.

    Returns the count of activities updated.
    """
    from app.models.scheduling import ScheduleActivity

    if not progress_updates:
        return 0

    activity_ids = [uuid.UUID(aid) for aid in progress_updates]
    result = await db.execute(
        select(ScheduleActivity).where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.id.in_(activity_ids),
        )
    )
    activities = list(result.scalars().all())
    updated = 0

    for activity in activities:
        aid = str(activity.id)
        new_pct = progress_updates.get(aid)
        if new_pct is None:
            continue

        # Update metadata with AI progress (non-destructive)
        meta = dict(activity.metadata_ or {})
        meta["ai_pct_complete"] = float(new_pct)
        meta["last_photo_update"] = date.today().isoformat()
        if snapshot_id:
            meta["last_snapshot_id"] = str(snapshot_id)
        activity.metadata_ = meta
        updated += 1

    if updated:
        await db.flush()

    logger.info(
        "Updated AI progress for %d/%d activities in project %s",
        updated,
        len(progress_updates),
        project_id,
    )
    return updated


async def create_progress_snapshot(
    db: AsyncSession,
    project_id: uuid.UUID,
    photo_ids: list[uuid.UUID],
    activities_progress: dict[str, Decimal],
    created_by: uuid.UUID | None = None,
    update_evm: bool = False,
) -> Any:
    """Create a progress snapshot capturing AI-estimated progress at a point in time.

    Args:
        db: Async database session.
        project_id: Project to snapshot.
        photo_ids: IDs of photos contributing to this snapshot.
        activities_progress: activity_id -> estimated percent complete.
        created_by: User who initiated the snapshot.
        update_evm: If True, recalculate earned value from the new progress
            data and create an updated EVMSnapshot when existing EVM data
            exists for the project.  Defaults to False so existing callers
            are unaffected.
    """
    from app.models.progress_tracking import ProgressSnapshot
    from app.models.scheduling import ScheduleActivity

    # Calculate overall project progress as weighted average by duration
    overall_progress = Decimal("0")
    if activities_progress:
        activity_ids = [uuid.UUID(aid) for aid in activities_progress]
        result = await db.execute(
            select(ScheduleActivity).where(
                ScheduleActivity.project_id == project_id,
                ScheduleActivity.id.in_(activity_ids),
            )
        )
        acts = list(result.scalars().all())

        total_duration = sum(a.duration_days for a in acts) or 1
        weighted_sum = Decimal("0")
        for a in acts:
            pct = activities_progress.get(str(a.id), Decimal("0"))
            weight = Decimal(str(a.duration_days)) / Decimal(str(total_duration))
            weighted_sum += pct * weight
        overall_progress = weighted_sum.quantize(Decimal("0.01"))

    # Serialize for JSONB
    progress_json = {k: float(v) for k, v in activities_progress.items()}

    snapshot = ProgressSnapshot(
        project_id=project_id,
        snapshot_date=date.today(),
        activities_progress=progress_json,
        overall_progress=overall_progress,
        photo_ids=[str(pid) for pid in photo_ids],
        created_by=created_by,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    # IG-01: Optionally recalculate earned value from updated progress
    if update_evm and activities_progress:
        try:
            await _update_evm_from_progress(db, project_id, activities_progress)
        except Exception as exc:
            logger.warning(
                "EVM update after progress snapshot failed for project %s: %s",
                project_id,
                exc,
            )

    return snapshot


async def _update_evm_from_progress(
    db: AsyncSession,
    project_id: uuid.UUID,
    activities_progress: dict[str, Decimal],
) -> None:
    """Recalculate earned value from activity progress and persist an EVMSnapshot.

    Looks up the most recent EVMSnapshot for the project.  If one exists,
    computes a new EV by weighting each activity's progress by its scheduled
    value (total_float-adjusted duration * hourly cost, or simply its
    duration weight against the BAC).  Creates a new EVMSnapshot with the
    updated EV while preserving the existing BAC, PV, and AC.

    If no prior EVMSnapshot exists the function returns silently — there is
    no baseline to update against.
    """
    from app.models.evm import EVMSnapshot
    from app.models.scheduling import ScheduleActivity
    from app.services.controls.evm_engine import calculate_evm_metrics

    # Fetch the latest EVMSnapshot for this project
    latest_result = await db.execute(
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(EVMSnapshot.snapshot_date.desc())
        .limit(1)
    )
    latest_snap = latest_result.scalar_one_or_none()
    if latest_snap is None:
        return  # No EVM baseline — nothing to update

    bac = latest_snap.bac
    pv = latest_snap.pv
    ac = latest_snap.ac

    # Fetch all project activities to compute weighted EV
    act_result = await db.execute(
        select(ScheduleActivity).where(ScheduleActivity.project_id == project_id)
    )
    all_activities = list(act_result.scalars().all())
    if not all_activities:
        return

    total_duration = sum(a.duration_days for a in all_activities) or 1

    # Compute new EV: sum of (activity_weight * pct_complete * BAC)
    # Use AI progress where available, fall back to scheduled pct_complete
    new_ev = Decimal("0")
    for act in all_activities:
        aid = str(act.id)
        pct = activities_progress.get(aid)
        if pct is None:
            pct = act.pct_complete or Decimal("0")
        weight = Decimal(str(act.duration_days)) / Decimal(str(total_duration))
        new_ev += weight * (pct / Decimal("100")) * bac

    new_ev = new_ev.quantize(Decimal("0.01"))

    # Calculate full EVM metrics with the updated EV
    try:
        metrics = calculate_evm_metrics(bac, pv, new_ev, ac)
    except ValueError:
        return  # BAC validation failure — skip

    new_snapshot = EVMSnapshot(
        project_id=project_id,
        snapshot_date=date.today(),
        bac=bac,
        pv=pv,
        ev=new_ev,
        ac=ac,
        sv=Decimal(str(metrics["sv"])),
        cv=Decimal(str(metrics["cv"])),
        spi=Decimal(str(metrics["spi"])) if metrics["spi"] is not None else Decimal("0"),
        cpi=Decimal(str(metrics["cpi"])) if metrics["cpi"] is not None else Decimal("0"),
        eac=Decimal(str(metrics["eac"])) if metrics["eac"] is not None else bac,
        etc=Decimal(str(metrics["etc"])) if metrics["etc"] is not None else Decimal("0"),
        vac=Decimal(str(metrics["vac"])) if metrics["vac"] is not None else Decimal("0"),
        tcpi=Decimal(str(metrics["tcpi"])) if metrics["tcpi"] is not None else Decimal("0"),
        percent_complete=Decimal(str(metrics["percent_complete"])),
        data_date=date.today(),
        metadata_={"source": "progress_tracker", "auto_updated": True},
    )
    db.add(new_snapshot)
    await db.flush()

    logger.info(
        "EVM snapshot auto-updated from progress tracker: project=%s, EV=%s, SPI=%s, CPI=%s",
        project_id,
        new_ev,
        metrics["spi"],
        metrics["cpi"],
    )


# ---------------------------------------------------------------------------
# IG-06: Drone capture → progress tracking integration
# ---------------------------------------------------------------------------


async def analyze_drone_capture_for_progress(
    db: AsyncSession,
    project_id: uuid.UUID,
    capture_id: uuid.UUID,
) -> Any:
    """Analyze a drone capture and create a ProgressSnapshot from the results.

    Loads the DroneCapture record.  For 'photo' and 'orthomosaic' capture
    types, downloads the image bytes from S3 and runs the same YOLO
    detection pipeline used for regular progress photos.  Creates a
    ProgressSnapshot with the AI-estimated activity progress.

    Args:
        db: Async database session.
        project_id: Project the drone capture belongs to.
        capture_id: ID of the DroneCapture to analyze.

    Returns:
        The created ProgressSnapshot, or None if the capture type is not
        supported for progress analysis (e.g., point_cloud, video).

    Raises:
        ValueError: If the capture is not found.
    """
    from app.models.drone import DroneCapture

    capture = await db.get(DroneCapture, capture_id)
    if capture is None:
        raise ValueError(f"DroneCapture {capture_id} not found")

    # Only photo and orthomosaic types are suitable for visual progress analysis
    if capture.capture_type not in ("photo", "orthomosaic"):
        logger.info(
            "Skipping drone capture %s (type=%s) — not suitable for progress analysis",
            capture_id,
            capture.capture_type,
        )
        return None

    # Download image from S3
    try:
        from app.utils.s3 import download_file

        photo_bytes = download_file(capture.s3_key)
    except Exception as exc:
        logger.error(
            "Failed to download drone capture %s from S3 (%s): %s",
            capture_id,
            capture.s3_key,
            exc,
        )
        return None

    # Run the standard progress photo analysis pipeline
    photo_url = f"drone://{capture.s3_key}"
    analysis = await analyze_progress_photo(
        db,
        project_id,
        photo_bytes,
        photo_url,
    )

    if not analysis.estimated_progress:
        logger.info(
            "Drone capture %s yielded no activity progress estimates",
            capture_id,
        )
        return None

    # Create a progress snapshot from the drone analysis
    snapshot = await create_progress_snapshot(
        db,
        project_id,
        photo_ids=[uuid.UUID(analysis.photo_id)],
        activities_progress=analysis.estimated_progress,
    )

    # Update the capture's processing status
    capture.processing_status = "analyzed"
    meta = dict(capture.metadata_ or {})
    meta["progress_snapshot_id"] = str(snapshot.id)
    meta["worker_count"] = analysis.worker_count
    meta["equipment_detected"] = len(analysis.equipment_detected)
    meta["activities_matched"] = len(analysis.activity_matches)
    capture.metadata_ = meta
    await db.flush()

    logger.info(
        "Drone capture %s analyzed: %d activities matched, snapshot %s created",
        capture_id,
        len(analysis.activity_matches),
        snapshot.id,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_detection(photo_bytes: bytes) -> list[dict]:
    """Run YOLO detection on photo bytes.

    Attempts to use the custom safety YOLO model (13 construction classes).
    Falls back to the generic YOLO detector if the safety model is unavailable.
    Returns a list of detection dicts with class_name, confidence, bbox.
    """
    try:
        import io

        import numpy as np
        from PIL import Image

        from app.services.vision.detector_yolo import YOLODetector

        detector = YOLODetector()

        # Try safety model first (construction-specific classes)
        model_paths = [
            "models/safety_yolo_v1.0/best.pt",
            "models/safety_yolo/best.pt",
        ]
        loaded = False
        for path in model_paths:
            try:
                detector.load_model(path)
                loaded = True
                break
            except Exception:
                logger.warning("Failed to load YOLO model from %s", path, exc_info=True)
                continue

        if not loaded:
            # Fallback to default YOLO
            try:
                detector.load_model("yolo11n.pt")
            except Exception:
                logger.warning("No YOLO model available; returning empty detections")
                return []

        img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        img_array = np.array(img)

        raw_detections = detector.detect(img_array, confidence_threshold=0.3)
        return [
            {
                "class_name": d.class_name,
                "confidence": round(d.confidence, 3),
                "bbox": list(d.bbox),
            }
            for d in raw_detections
        ]

    except ImportError:
        logger.warning(
            "YOLO dependencies not available (ultralytics/PIL/numpy); returning empty detections"
        )
        return []
    except Exception as exc:
        logger.error("Detection failed: %s", exc)
        return []
