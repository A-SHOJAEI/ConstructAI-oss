"""Schedule optimization API endpoints.

Provides generative optimization and scenario application for construction
schedules.  The optimization engine generates, evaluates, and Pareto-ranks
perturbation scenarios across crew sizing, shift work, resequencing, and
activity splitting.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.scheduling import ScheduleActivity, ScheduleBaseline
from app.models.user import User
from app.schemas.schedule_optimization import (
    ChangeDetailSchema,
    OptimizationRequest,
    OptimizationResponse,
    ScenarioResponse,
)
from app.services.scheduling.schedule_optimizer import (
    OptimizationConfig,
    ProjectContext,
    optimize_schedule,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_baseline_activities(
    baseline_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> tuple[ScheduleBaseline, list[dict]]:
    """Load and validate a baseline and its activities."""
    baseline = await db.get(ScheduleBaseline, baseline_id)
    if baseline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule baseline not found",
        )
    await verify_project_access(baseline.project_id, current_user, db)

    query = select(ScheduleActivity).where(ScheduleActivity.baseline_id == baseline_id)
    result = await db.execute(query)
    activities = result.scalars().all()

    if not activities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Baseline has no activities for optimization.",
        )

    activity_dicts = []
    for act in activities:
        # Convert predecessors to proper relationship dicts for CPM engine.
        # The predecessors JSONB column may contain plain string IDs or
        # fully-formed dicts with predecessor_id/type/lag keys.
        raw_preds = act.predecessors or []
        relationships: list[dict] = []
        for p in raw_preds:
            if isinstance(p, dict) and "predecessor_id" in p:
                relationships.append(p)  # Already in correct format
            elif isinstance(p, str):
                relationships.append({"predecessor_id": p, "type": "FS", "lag": 0})
            # Skip malformed entries

        activity_dicts.append(
            {
                "id": str(act.id),
                "name": act.name,
                "duration_days": act.duration_days,
                "relationships": relationships,
                "predecessors": [
                    p["predecessor_id"] if isinstance(p, dict) else str(p) for p in raw_preds
                ],
                "crew_size": (
                    act.resource_assignments[0].get("quantity", 0)
                    if act.resource_assignments
                    else 0
                ),
                "resources": (
                    {
                        ra.get("resource_type", "labor"): ra.get("quantity", 0)
                        for ra in act.resource_assignments
                    }
                    if act.resource_assignments
                    else {}
                ),
                "calendar_id": act.calendar_id,
            }
        )

    return baseline, activity_dicts


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/optimize", response_model=OptimizationResponse)
async def run_optimization(
    request: OptimizationRequest,
    current_user: User = Depends(require_permission("schedules", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Run generative schedule optimization on a baseline.

    Generates perturbation scenarios (crew sizing, shift work, resequencing,
    activity splitting), evaluates each through CPM + cost analysis, and
    returns Pareto-ranked results.
    """
    baseline, activity_dicts = await _load_baseline_activities(
        request.baseline_id, current_user, db
    )

    config = OptimizationConfig(
        max_scenarios=request.config.max_scenarios,
        max_crew_multiplier=request.config.max_crew_multiplier,
        allow_overtime=request.config.allow_overtime,
        allow_weekend_work=request.config.allow_weekend_work,
        allow_resequencing=request.config.allow_resequencing,
        allow_splitting=request.config.allow_splitting,
        overtime_cost_multiplier=request.config.overtime_cost_multiplier,
        shift_differential_pct=request.config.shift_differential_pct,
        weights=request.config.weights,
    )

    context = ProjectContext(
        project_id=str(request.project_id),
        start_date=baseline.baseline_date,
    )

    result = await optimize_schedule(activity_dicts, config, context)

    # Build response
    scenario_responses: list[ScenarioResponse] = []
    for sr in result.scenarios:
        changes = []
        for c in sr.scenario.changes:
            changes.append(
                ChangeDetailSchema(
                    activity_id=c.activity_id,
                    activity_name="",
                    field=c.field,
                    original_value=str(c.original_value),
                    new_value=str(c.new_value),
                    reason=c.reason,
                )
            )

        scenario_responses.append(
            ScenarioResponse(
                name=sr.scenario.name,
                perturbation_type=sr.scenario.perturbation_type,
                duration_days=sr.duration_days,
                duration_delta_days=sr.duration_days - result.baseline_duration,
                cost_delta=str(sr.cost_delta),
                risk_score=sr.risk_score,
                weather_delay_days=sr.weather_delay_days,
                is_pareto_optimal=sr.is_pareto_optimal,
                rank=sr.rank,
                changes=changes,
            )
        )

    return OptimizationResponse(
        baseline_duration=result.baseline_duration,
        baseline_cost=str(result.baseline_cost),
        total_scenarios=len(result.scenarios),
        pareto_count=len(result.pareto_front),
        scenarios=scenario_responses,
        processing_time_ms=result.processing_time_ms,
    )


@router.post("/optimize/{baseline_id}/apply/{scenario_index}")
async def apply_scenario(
    baseline_id: uuid.UUID,
    scenario_index: int,
    current_user: User = Depends(require_permission("schedules", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Apply an optimization scenario by creating a new baseline.

    Creates a copy of the baseline with the scenario's modified activities.
    The original baseline is preserved.

    Parameters
    ----------
    baseline_id:
        The baseline that was optimized.
    scenario_index:
        Zero-based index into the ranked scenario list from the most recent
        optimization result.  The caller should re-run optimization if needed
        and use the scenario rank - 1 as the index.
    """
    baseline, activity_dicts = await _load_baseline_activities(baseline_id, current_user, db)

    # Re-run optimization to get the scenario (stateless — no server-side cache)
    config = OptimizationConfig()
    context = ProjectContext(
        project_id=str(baseline.project_id),
        start_date=baseline.baseline_date,
    )

    result = await optimize_schedule(activity_dicts, config, context)

    if scenario_index < 0 or scenario_index >= len(result.scenarios):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scenario index {scenario_index}. "
            f"Valid range: 0-{len(result.scenarios) - 1}",
        )

    selected = result.scenarios[scenario_index]

    # Determine new version
    version_query = (
        select(ScheduleBaseline.version)
        .where(ScheduleBaseline.project_id == baseline.project_id)
        .order_by(ScheduleBaseline.version.desc())
        .limit(1)
    )
    version_result = await db.execute(version_query)
    latest_version = version_result.scalar() or 0

    new_baseline = ScheduleBaseline(
        project_id=baseline.project_id,
        name=f"Optimized: {selected.scenario.name}",
        version=latest_version + 1,
        baseline_date=baseline.baseline_date,
        created_by=current_user.id,
    )
    db.add(new_baseline)
    await db.flush()

    # Create activities from the scenario
    for act_dict in selected.scenario.activities:
        new_activity = ScheduleActivity(
            project_id=baseline.project_id,
            baseline_id=new_baseline.id,
            activity_code=act_dict.get("activity_code", act_dict.get("id", "")),
            name=act_dict.get("name", ""),
            duration_days=int(act_dict.get("duration_days", 0)),
            predecessors=act_dict.get("relationships", []),
            resource_assignments=[],
        )
        # Rebuild resource_assignments from scenario data
        crew_size = act_dict.get("crew_size", 0)
        if crew_size > 0:
            new_activity.resource_assignments = [{"resource_type": "labor", "quantity": crew_size}]

        db.add(new_activity)

    await db.flush()
    await db.refresh(new_baseline)

    return {
        "baseline_id": str(new_baseline.id),
        "name": new_baseline.name,
        "version": new_baseline.version,
        "scenario_applied": selected.scenario.name,
        "expected_duration": selected.duration_days,
        "cost_delta": str(selected.cost_delta),
    }
