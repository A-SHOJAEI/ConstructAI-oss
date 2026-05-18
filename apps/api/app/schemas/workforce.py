"""Pydantic schemas for workforce analytics endpoints."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Labor Aggregation schemas
# ---------------------------------------------------------------------------


class LaborAggregateRequest(BaseModel):
    """Request for labor data aggregation."""

    project_id: str | None = None
    org_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class LaborAggregateResponse(BaseModel):
    """Aggregated labor data response."""

    project_id: str | None = None
    date_range: list[str] | None = None
    total_workers: int
    total_manhours: float
    workers_by_trade: dict[str, int]
    manhours_by_trade: dict[str, float]
    daily_avg_workers: float
    daily_avg_manhours: float
    working_days: int


# ---------------------------------------------------------------------------
# Productivity Metrics schemas
# ---------------------------------------------------------------------------


class TradeProductivityMetricResponse(BaseModel):
    """Productivity metrics for a single trade."""

    trade: str
    activity_type: str | None = None
    avg_manhours_per_unit: float
    median_manhours_per_unit: float
    std_dev: float
    sample_count: int
    trend: str
    trend_slope: float
    unit_of_measure: str


class ProductivityMetricsResponse(BaseModel):
    """Response for productivity metrics."""

    metrics: list[TradeProductivityMetricResponse]
    total_trades: int


# ---------------------------------------------------------------------------
# Labor Forecast schemas
# ---------------------------------------------------------------------------


class LaborForecastResponse(BaseModel):
    """Forecasted labor needs response."""

    project_id: str
    forecast_date: str
    remaining_activities: int
    total_remaining_manhours: float
    by_trade: dict[str, float]
    by_month: dict[str, dict[str, float]]
    estimated_completion_date: str | None = None


# ---------------------------------------------------------------------------
# Overtime schemas
# ---------------------------------------------------------------------------


class OvertimeRequest(BaseModel):
    """Request to predict overtime."""

    remaining_activities: list[dict] = Field(
        ...,
        description="List of {trade, remaining_manhours, duration_days}",
    )
    available_workforce: dict[str, int] = Field(
        ...,
        description="Workers available by trade",
    )
    schedule_compression_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=50.0,
        description="Schedule compression percentage",
    )
    avg_hourly_rate: float | None = None


class OvertimePredictionResponse(BaseModel):
    """Overtime prediction response."""

    project_id: str
    schedule_compression_pct: float
    total_remaining_manhours: float
    standard_hours_available: float
    predicted_overtime_hours: float
    overtime_pct: float
    estimated_overtime_cost: float
    overtime_rate_multiplier: float
    risk_level: str
    recommendation: str


# ---------------------------------------------------------------------------
# Fatigue schemas
# ---------------------------------------------------------------------------


class FatigueRequest(BaseModel):
    """Request to assess fatigue risk."""

    worker_hours: list[dict] = Field(
        ...,
        description="List of {worker_id, trade, date, hours}",
    )
    threshold_daily: float = Field(default=10.0, ge=1.0, le=24.0)
    threshold_weekly: float = Field(default=50.0, ge=1.0, le=168.0)


class FatigueAlertResponse(BaseModel):
    """Fatigue alert response."""

    worker_id: str | None = None
    trade: str | None = None
    alert_type: str
    hours_worked: float
    threshold: float
    excess_hours: float
    risk_level: str
    recommendation: str


class FatigueAssessmentResponse(BaseModel):
    """Complete fatigue assessment response."""

    alerts: list[FatigueAlertResponse]
    total_alerts: int
    red_alerts: int
    yellow_alerts: int


# ---------------------------------------------------------------------------
# Craft Availability schemas
# ---------------------------------------------------------------------------


class CraftAvailabilityResponse(BaseModel):
    """Craft availability and gap analysis response."""

    project_id: str
    forecast_date: str
    trades: dict[str, dict]
    total_demand_manhours: float
    total_supply_workers: int
    overall_gap_pct: float


# ---------------------------------------------------------------------------
# Workforce Snapshot schemas
# ---------------------------------------------------------------------------


class WorkforceSnapshotResponse(BaseModel):
    """Workforce snapshot response."""

    id: str
    project_id: str
    snapshot_date: date
    total_workers: int
    workers_by_trade: dict
    total_manhours: float
    overtime_hours: float
    overtime_pct: float
    fatigue_flags: list
    created_at: str | None = None


class WorkforceSnapshotListResponse(BaseModel):
    """List of workforce snapshots."""

    data: list[WorkforceSnapshotResponse]
    total: int


# ---------------------------------------------------------------------------
# Portfolio schemas
# ---------------------------------------------------------------------------


class PortfolioWorkforceResponse(BaseModel):
    """Portfolio-level workforce summary."""

    org_id: str
    date_range: list[str] | None = None
    project_count: int
    total_workers: int
    total_manhours: float
    workers_by_trade: dict[str, int]
    manhours_by_trade: dict[str, float]
    daily_avg_workers: float
    working_days: int
