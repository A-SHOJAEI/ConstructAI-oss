"""Pydantic response schemas for project report endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class ReportPeriod(BaseModel):
    month: int | None = None
    year: int | None = None
    months: int | None = None
    start_date: str | None = None
    end_date: str | None = None


# ---------------------------------------------------------------------------
# Monthly Cost Report
# ---------------------------------------------------------------------------


class BudgetVsActualItem(BaseModel):
    division: str
    budget: float
    actual_this_period: float
    variance: float
    percent_spent: float


class ChangeOrderSummaryItem(BaseModel):
    co_number: str
    title: str
    cost_impact: float
    schedule_impact_days: int
    status: str


class ChangeOrderSummary(BaseModel):
    count: int
    total_cost_impact: float
    total_schedule_impact_days: int
    items: list[ChangeOrderSummaryItem]


class CostReportSummary(BaseModel):
    original_budget: float
    approved_cos_total: float
    adjusted_budget: float
    actuals_this_period: float
    actuals_cumulative: float
    remaining_budget: float
    percent_spent: float


class MonthlyCostReportResponse(BaseModel):
    report_type: str = "monthly_cost"
    project_id: str
    period: ReportPeriod
    generated_at: str
    summary: CostReportSummary
    budget_vs_actual_by_division: list[BudgetVsActualItem]
    change_order_summary: ChangeOrderSummary
    projection: dict[str, Any] = Field(default_factory=dict)
    cash_flow_summary: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Schedule Performance Report
# ---------------------------------------------------------------------------


class SPITrendItem(BaseModel):
    date: str
    spi: float
    cpi: float
    percent_complete: float


class CriticalActivityItem(BaseModel):
    id: str
    name: str
    activity_code: str
    duration_days: int
    start_date: str | None = None
    finish_date: str | None = None
    total_float: int | None = None
    status: str
    pct_complete: float


class DelayedActivityItem(BaseModel):
    id: str
    name: str
    activity_code: str
    planned_finish: str | None = None
    days_late: int
    pct_complete: float
    status: str


class LookaheadItem(BaseModel):
    id: str
    name: str
    activity_code: str
    start_date: str | None = None
    finish_date: str | None = None
    duration_days: int
    is_critical: bool


class SchedulePerformanceSummary(BaseModel):
    current_spi: float | None = None
    current_cpi: float | None = None
    schedule_status: str
    critical_activity_count: int
    delayed_activity_count: int
    lookahead_activity_count: int


class SchedulePerformanceReportResponse(BaseModel):
    report_type: str = "schedule_performance"
    project_id: str
    generated_at: str
    summary: SchedulePerformanceSummary
    spi_trend: list[SPITrendItem]
    critical_activities: list[CriticalActivityItem]
    delayed_activities: list[DelayedActivityItem]
    two_week_lookahead: list[LookaheadItem]


# ---------------------------------------------------------------------------
# Safety Trend Report
# ---------------------------------------------------------------------------


class SafetyMonthlyTrendItem(BaseModel):
    month: str
    total_alerts: int
    critical_alerts: int
    ppe_violations: int
    near_misses: int
    false_positive_rate: float
    acknowledgment_rate: float


class HazardItem(BaseModel):
    type: str
    count: int


class SafetyTrendSummary(BaseModel):
    total_alerts: int
    critical_alerts: int
    false_positive_rate: float
    acknowledgment_rate: float
    estimated_trir: float


class SafetyTrendReportResponse(BaseModel):
    report_type: str = "safety_trend"
    project_id: str
    generated_at: str
    period: ReportPeriod
    summary: SafetyTrendSummary
    monthly_trend: list[SafetyMonthlyTrendItem]
    top_hazards: list[HazardItem]
    priority_distribution: dict[str, int]
    alert_type_distribution: dict[str, int]


# ---------------------------------------------------------------------------
# Subcontractor Performance Report
# ---------------------------------------------------------------------------


class SubScores(BaseModel):
    submission_compliance: int
    quality: int
    rfi_responsiveness: int


class SubMetrics(BaseModel):
    total_submissions: int
    approved_submissions: int
    defect_count: int
    rfi_count: int
    rfis_answered: int
    avg_rfi_response_days: float


class SubcontractorScorecard(BaseModel):
    subcontractor_id: str
    company_name: str
    trade: str
    status: str
    overall_score: int
    scores: SubScores
    metrics: SubMetrics


class SubcontractorPerformanceReportResponse(BaseModel):
    report_type: str = "subcontractor_performance"
    project_id: str
    generated_at: str
    subcontractor_count: int
    scorecards: list[SubcontractorScorecard]


# ---------------------------------------------------------------------------
# Portfolio Summary Report (RP-05)
# ---------------------------------------------------------------------------


class ProjectStatusSummary(BaseModel):
    status: str
    count: int


class PortfolioBudgetSummary(BaseModel):
    total_contract_value: float
    total_actuals: float
    total_remaining: float


class PortfolioEVMSummary(BaseModel):
    avg_spi: float | None = None
    avg_cpi: float | None = None
    projects_with_evm: int


class PortfolioSafetySummary(BaseModel):
    total_incidents: int
    total_critical: int


class PortfolioSummaryReportResponse(BaseModel):
    report_type: str = "portfolio_summary"
    org_id: str
    generated_at: str
    project_count: int
    projects_by_status: list[ProjectStatusSummary]
    budget_summary: PortfolioBudgetSummary
    evm_summary: PortfolioEVMSummary
    safety_summary: PortfolioSafetySummary


# ---------------------------------------------------------------------------
# OSHA Compliance Report (RP-06)
# ---------------------------------------------------------------------------


class OSHAComplianceItem(BaseModel):
    activity: str
    standard: str
    subpart: str
    topic: str


class OSHAComplianceReportResponse(BaseModel):
    report_type: str = "osha_compliance"
    project_id: str
    generated_at: str
    activities_analyzed: int
    applicable_standards: list[OSHAComplianceItem]
    total_standards: int


# ---------------------------------------------------------------------------
# Certified Payroll Report (RP-07)
# ---------------------------------------------------------------------------


class CertifiedPayrollReportResponse(BaseModel):
    report_type: str = "certified_payroll"
    project_id: str
    generated_at: str
    period_start: str
    period_end: str
    wh347_data: dict[str, Any]


# ---------------------------------------------------------------------------
# EVM Report (RP-08)
# ---------------------------------------------------------------------------


class EVMTrendItem(BaseModel):
    date: str
    earned_value: float
    planned_value: float
    actual_cost: float
    spi: float
    cpi: float
    sv: float
    cv: float


class EVMProjection(BaseModel):
    bac: float
    eac: float
    etc: float
    vac: float
    tcpi: float | None = None


class SCurveDataPoint(BaseModel):
    date: str
    planned: float
    earned: float
    actual: float


class EVMReportResponse(BaseModel):
    report_type: str = "evm"
    project_id: str
    generated_at: str
    current_status: dict[str, Any]
    trend_data: list[EVMTrendItem]
    projection: EVMProjection | None = None
    s_curve_data: list[SCurveDataPoint]
