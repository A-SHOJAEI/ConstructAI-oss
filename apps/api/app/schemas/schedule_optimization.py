"""Pydantic schemas for the generative schedule optimization API."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class OptimizationConfigSchema(BaseModel):
    """Tunable knobs for the optimization engine."""

    max_scenarios: int = Field(
        default=50, ge=1, le=500, description="Maximum scenarios to generate"
    )
    max_crew_multiplier: float = Field(
        default=2.0, ge=1.0, le=5.0, description="Maximum crew multiplier vs baseline"
    )
    allow_overtime: bool = Field(default=True, description="Allow second-shift scenarios")
    allow_weekend_work: bool = Field(default=False, description="Allow 6/7-day week scenarios")
    allow_resequencing: bool = Field(default=True, description="Allow FS->SS resequencing")
    allow_splitting: bool = Field(default=True, description="Allow splitting long activities")
    overtime_cost_multiplier: float = Field(
        default=1.5, ge=1.0, le=3.0, description="Overtime hourly rate multiplier"
    )
    shift_differential_pct: float = Field(
        default=15.0, ge=0.0, le=50.0, description="Shift differential percentage"
    )
    weights: dict[str, float] = Field(
        default_factory=lambda: {"duration": 0.4, "cost": 0.35, "risk": 0.25},
        description="Objective weights for ranking (duration, cost, risk)",
    )


class OptimizationRequest(BaseModel):
    """Request body for the /scheduling/optimize endpoint."""

    project_id: uuid.UUID
    baseline_id: uuid.UUID
    config: OptimizationConfigSchema = Field(default_factory=OptimizationConfigSchema)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ChangeDetailSchema(BaseModel):
    """A single field-level change within a scenario."""

    activity_id: str
    activity_name: str = ""
    field: str
    original_value: str
    new_value: str
    reason: str


class ScenarioResponse(BaseModel):
    """Evaluated scenario summary."""

    name: str
    perturbation_type: str
    duration_days: int
    duration_delta_days: int
    cost_delta: str
    risk_score: float
    weather_delay_days: int
    is_pareto_optimal: bool
    rank: int | None = None
    changes: list[ChangeDetailSchema]


class OptimizationResponse(BaseModel):
    """Top-level response for an optimization run."""

    baseline_duration: int
    baseline_cost: str
    total_scenarios: int
    pareto_count: int
    scenarios: list[ScenarioResponse]
    processing_time_ms: int
