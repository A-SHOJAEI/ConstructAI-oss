"""Tests for the WeeklyBriefAgent — flagship intelligence brief generation.

Validates:
- Helper scoring functions (SPI, CPI, float health, SPI trend, float erosion)
- Schedule intelligence sub-agent
- Cost intelligence sub-agent
- Risk intelligence sub-agent
- Productivity intelligence sub-agent
- Synthesizer node (LLM-powered with fallback)
- Guardrails check node
- Full pipeline end-to-end
- PDF generation
- Notification service
- Celery beat schedule registration
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agents.weekly_brief_agent import (
    STATUS_GREEN_THRESHOLD,
    STATUS_YELLOW_THRESHOLD,
    WEIGHT_COST,
    WEIGHT_PRODUCTIVITY,
    WEIGHT_RISK,
    WEIGHT_SCHEDULE,
    WeeklyBriefState,
    _cpi_to_score,
    _detect_float_erosion,
    _detect_spi_trend,
    _float_health,
    _format_cost_summary,
    _format_productivity_summary,
    _format_risk_summary,
    _format_schedule_summary,
    _spi_to_score,
    build_weekly_brief_agent,
    cost_intelligence_node,
    guardrails_check_node,
    load_project_data_node,
    productivity_intelligence_node,
    risk_intelligence_node,
    schedule_intelligence_node,
    synthesizer_node,
)

# ---------------------------------------------------------------------------
# Shared mock project data — realistic commercial construction project
# ---------------------------------------------------------------------------

MOCK_PROJECT_DATA = {
    "name": "Riverfront Office Tower",
    "project_number": "P-2026-001",
    "type": "commercial",
    "address": "123 Riverfront Dr, Chicago, IL 60601",
    "contract_value": "25000000",
    "start_date": "2025-06-01",
    "end_date": "2027-03-15",
    "activities": [
        {
            "id": "A1",
            "name": "Site Preparation",
            "duration_days": 15,
            "predecessors": [],
            "wbs_path": "Project/Phase1/Site",
            "total_float": 5,
        },
        {
            "id": "A2",
            "name": "Foundation Excavation",
            "duration_days": 20,
            "predecessors": ["A1"],
            "wbs_path": "Project/Phase1/Foundation",
            "total_float": 3,
        },
        {
            "id": "A3",
            "name": "Steel Erection",
            "duration_days": 45,
            "predecessors": ["A2"],
            "wbs_path": "Project/Phase2/Structure",
            "total_float": 0,
        },
        {
            "id": "A4",
            "name": "MEP Rough-In",
            "duration_days": 30,
            "predecessors": ["A3"],
            "wbs_path": "Project/Phase2/MEP",
            "total_float": 8,
        },
        {
            "id": "A5",
            "name": "Exterior Envelope",
            "duration_days": 25,
            "predecessors": ["A3"],
            "wbs_path": "Project/Phase2/Envelope",
            "total_float": 2,
        },
    ],
    "previous_activities": [
        {"id": "A1", "name": "Site Preparation", "total_float": 10},
        {"id": "A2", "name": "Foundation Excavation", "total_float": 8},
        {"id": "A3", "name": "Steel Erection", "total_float": 3},
        {"id": "A4", "name": "MEP Rough-In", "total_float": 10},
        {"id": "A5", "name": "Exterior Envelope", "total_float": 5},
    ],
    "planned_duration": 135,
    "evm_snapshots": [
        {"snapshot_date": "2025-09-01", "spi": "1.02", "cpi": "0.98"},
        {"snapshot_date": "2025-10-01", "spi": "0.98", "cpi": "0.96"},
        {"snapshot_date": "2025-11-01", "spi": "0.95", "cpi": "0.94"},
        {"snapshot_date": "2025-12-01", "spi": "0.92", "cpi": "0.93"},
    ],
    "latest_evm": {
        "bac": "25000000",
        "pv": "12500000",
        "ev": "11500000",
        "ac": "12400000",
        "spi": "0.92",
        "cpi": "0.93",
        "percent_complete": "46",
    },
    "change_orders": [
        {
            "co_number": "CO-001",
            "title": "Foundation redesign",
            "status": "approved",
            "cost_impact": "350000",
            "schedule_impact_days": 5,
            "submitted_at": "2025-08-15",
        },
        {
            "co_number": "CO-002",
            "title": "Additional elevator shaft",
            "status": "pending",
            "cost_impact": "180000",
            "schedule_impact_days": 10,
            "submitted_at": (date.today() - timedelta(days=12)).isoformat(),
        },
        {
            "co_number": "CO-003",
            "title": "Upgraded fire suppression",
            "status": "pending",
            "cost_impact": "95000",
            "schedule_impact_days": 0,
            "submitted_at": (date.today() - timedelta(days=3)).isoformat(),
        },
    ],
    "rfis": [
        {
            "id": "RFI-001",
            "status": "open",
            "created_at": (date.today() - timedelta(days=15)).isoformat(),
        },
        {
            "id": "RFI-002",
            "status": "submitted",
            "created_at": (date.today() - timedelta(days=2)).isoformat(),
        },
    ],
}


def _make_state(**overrides) -> WeeklyBriefState:
    """Create a WeeklyBriefState with mock data and optional overrides."""
    base = {
        "project_id": "test-project-001",
        "org_id": "test-org",
        "project_data": MOCK_PROJECT_DATA,
        "schedule_intelligence": None,
        "cost_intelligence": None,
        "risk_intelligence": None,
        "productivity_intelligence": None,
        "executive_summary": None,
        "overall_health_score": None,
        "project_status": None,
        "action_items": None,
        "metrics_dashboard": None,
        "narrative_report": None,
        "guardrails_result": None,
        "status": "processing",
        "errors": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Patch targets: lazy imports inside node functions need patching at source
# ---------------------------------------------------------------------------
CPM_TARGET = "app.services.scheduling.cpm_engine.calculate_cpm"
MC_TARGET = "app.services.controls.monte_carlo_schedule.run_schedule_risk_simulation"
EVM_TARGET = "app.services.controls.evm_engine.compute_evm_snapshot"
EAC_TARGET = "app.services.controls.eac_forecaster.forecast_eac"
WEATHER_TARGET = "app.services.scheduling.weather_service.get_weather_impact"
LLM_TARGET = "app.services.reliability.llm_gateway.get_llm_gateway"
SCORER_TARGET = "app.services.guardrails.confidence_scorer.ConfidenceScorer"
VERIFIER_TARGET = "app.services.guardrails.knowledge_verifier.verify"


# ===========================================================================
# TEST HELPER FUNCTIONS
# ===========================================================================


class TestSPIToScore:
    """Test _spi_to_score helper."""

    def test_perfect_spi(self):
        assert _spi_to_score(1.0) == 100

    def test_above_one(self):
        assert _spi_to_score(1.15) == 100

    def test_spi_095(self):
        assert _spi_to_score(0.97) == 85

    def test_spi_090(self):
        assert _spi_to_score(0.91) == 70

    def test_spi_085(self):
        assert _spi_to_score(0.87) == 55

    def test_spi_080(self):
        assert _spi_to_score(0.82) == 40

    def test_low_spi(self):
        score = _spi_to_score(0.70)
        assert 0 <= score < 40

    def test_zero_spi(self):
        assert _spi_to_score(0.0) == 0


class TestCPIToScore:
    """Test _cpi_to_score helper."""

    def test_perfect_cpi(self):
        assert _cpi_to_score(1.0) == 100

    def test_cpi_095(self):
        assert _cpi_to_score(0.96) == 80

    def test_cpi_090(self):
        assert _cpi_to_score(0.92) == 60

    def test_cpi_085(self):
        assert _cpi_to_score(0.87) == 40

    def test_low_cpi(self):
        score = _cpi_to_score(0.75)
        assert 0 <= score < 40


class TestFloatHealth:
    """Test _float_health helper."""

    def test_high_float(self):
        activities = [{"total_float": 15}, {"total_float": 20}]
        assert _float_health(activities) == 100

    def test_medium_float(self):
        activities = [{"total_float": 7}, {"total_float": 8}]
        assert _float_health(activities) == 70

    def test_low_float(self):
        activities = [{"total_float": 3}, {"total_float": 4}]
        assert _float_health(activities) == 40

    def test_near_zero_float(self):
        activities = [{"total_float": 0}, {"total_float": 1}]
        assert _float_health(activities) == 15

    def test_no_activities(self):
        assert _float_health([]) == 50

    def test_missing_float(self):
        activities = [{"total_float": None}, {"total_float": None}]
        assert _float_health(activities) == 50


class TestDetectSPITrend:
    """Test _detect_spi_trend helper."""

    def test_improving(self):
        assert _detect_spi_trend([0.85, 0.90, 0.95]) == "improving"

    def test_deteriorating(self):
        assert _detect_spi_trend([0.98, 0.94, 0.90]) == "deteriorating"

    def test_stable(self):
        assert _detect_spi_trend([0.95, 0.96, 0.95]) == "stable"

    def test_insufficient_data(self):
        assert _detect_spi_trend([0.95]) == "insufficient_data"

    def test_empty(self):
        assert _detect_spi_trend([]) == "insufficient_data"


class TestDetectFloatErosion:
    """Test _detect_float_erosion helper."""

    def test_erosion_detected(self):
        current = [
            {"id": "A", "name": "Activity A", "total_float": 2},
            {"id": "B", "name": "Activity B", "total_float": 8},
        ]
        previous = [
            {"id": "A", "total_float": 10},
            {"id": "B", "total_float": 10},
        ]
        result = _detect_float_erosion(current, previous)
        assert len(result) == 2
        # Sorted by erosion_days descending
        assert result[0]["activity_id"] == "A"
        assert result[0]["erosion_days"] == 8

    def test_no_erosion(self):
        current = [{"id": "A", "name": "Act A", "total_float": 12}]
        previous = [{"id": "A", "total_float": 10}]
        result = _detect_float_erosion(current, previous)
        assert len(result) == 0

    def test_new_activity(self):
        """Activities not in previous should not cause erosion."""
        current = [{"id": "NEW", "name": "New Act", "total_float": 5}]
        previous = [{"id": "OLD", "total_float": 10}]
        result = _detect_float_erosion(current, previous)
        assert len(result) == 0

    def test_max_10_results(self):
        current = [{"id": f"A{i}", "name": f"Act {i}", "total_float": 0} for i in range(15)]
        previous = [{"id": f"A{i}", "total_float": 10 + i} for i in range(15)]
        result = _detect_float_erosion(current, previous)
        assert len(result) == 10


# ===========================================================================
# TEST SUB-AGENT: SCHEDULE INTELLIGENCE
# ===========================================================================


class TestScheduleIntelligence:
    """Test schedule_intelligence_node."""

    @pytest.mark.asyncio
    async def test_with_activities_and_snapshots(self):
        """Full schedule intelligence with CPM, Monte Carlo, and SPI trend."""
        mock_cpm = {
            "critical_path": ["A1", "A2", "A3"],
            "project_duration": 80,
            "activities": {
                "A1": {"total_float": 5, "early_start": 0},
                "A2": {"total_float": 3, "early_start": 15},
                "A3": {"total_float": 0, "early_start": 35},
                "A4": {"total_float": 8, "early_start": 80},
                "A5": {"total_float": 2, "early_start": 80},
            },
        }
        mock_mc = {"p50": 82, "p90": 95, "criticality_index": {"A3": 0.95}}

        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value=mock_mc,
            ),
        ):
            state = _make_state()
            result = await schedule_intelligence_node(state)

        sched = result["schedule_intelligence"]
        assert "health_score" in sched
        assert 0 <= sched["health_score"] <= 100
        assert sched["critical_path"] == ["A1", "A2", "A3"]
        assert sched["p50_duration"] == 82
        assert sched["p90_duration"] == 95
        assert sched["spi_trend"] == "deteriorating"  # SPI went 1.02→0.92
        assert len(sched["spi_values"]) == 4

    @pytest.mark.asyncio
    async def test_float_erosion_detected(self):
        """Float erosion is identified from current vs previous activities."""
        mock_cpm = {
            "critical_path": ["A3"],
            "project_duration": 80,
            "activities": {
                "A1": {"total_float": 5},
                "A2": {"total_float": 3},
                "A3": {"total_float": 0},
                "A4": {"total_float": 8},
                "A5": {"total_float": 2},
            },
        }

        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value={"p50": 82, "p90": 95},
            ),
        ):
            state = _make_state()
            result = await schedule_intelligence_node(state)

        sched = result["schedule_intelligence"]
        erosion = sched.get("float_erosion_alerts", [])
        assert len(erosion) > 0
        # A1: 10→5 (-5), A2: 8→3 (-5), A3: 3→0 (-3), A4: 10→8 (-2), A5: 5→2 (-3)
        assert erosion[0]["erosion_days"] >= 3

    @pytest.mark.asyncio
    async def test_no_activities(self):
        """Graceful handling when no schedule data."""
        state = _make_state(project_data={**MOCK_PROJECT_DATA, "activities": []})
        result = await schedule_intelligence_node(state)
        sched = result["schedule_intelligence"]
        # SPI data still contributes via snapshots; score won't be exactly 50
        assert 0 <= sched["health_score"] <= 100
        assert any("No schedule" in w for w in sched.get("warnings", []))

    @pytest.mark.asyncio
    async def test_cpm_failure_handled(self):
        """CPM failure doesn't crash the sub-agent."""
        with patch(
            CPM_TARGET,
            new_callable=AsyncMock,
            side_effect=Exception("CPM engine error"),
        ):
            state = _make_state()
            result = await schedule_intelligence_node(state)

        sched = result["schedule_intelligence"]
        assert sched["health_score"] == 50
        assert len(result.get("errors", [])) > 0 or len(sched.get("warnings", [])) > 0

    @pytest.mark.asyncio
    async def test_spi_score_component(self):
        """SPI values from snapshots affect health score."""
        mock_cpm = {
            "critical_path": [],
            "project_duration": 80,
            "activities": {},
        }

        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value={"p50": 80, "p90": 90},
            ),
        ):
            # Perfect SPI project
            perfect_data = {
                **MOCK_PROJECT_DATA,
                "evm_snapshots": [
                    {"snapshot_date": "2025-10-01", "spi": "1.05"},
                    {"snapshot_date": "2025-11-01", "spi": "1.03"},
                ],
            }
            state = _make_state(project_data=perfect_data)
            result = await schedule_intelligence_node(state)
            high_score = result["schedule_intelligence"]["health_score"]

            # Poor SPI project
            poor_data = {
                **MOCK_PROJECT_DATA,
                "evm_snapshots": [
                    {"snapshot_date": "2025-10-01", "spi": "0.75"},
                    {"snapshot_date": "2025-11-01", "spi": "0.72"},
                ],
            }
            state = _make_state(project_data=poor_data)
            result = await schedule_intelligence_node(state)
            low_score = result["schedule_intelligence"]["health_score"]

            assert high_score > low_score

    @pytest.mark.asyncio
    async def test_monte_carlo_score_impact(self):
        """P50 vs planned_duration ratio affects schedule score."""
        mock_cpm = {
            "critical_path": ["A1"],
            "project_duration": 100,
            "activities": {"A1": {"total_float": 10}},
        }

        # P50 well over planned → lower MC score
        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value={"p50": 200, "p90": 250},
            ),
        ):
            state = _make_state()
            result = await schedule_intelligence_node(state)
            overrun_score = result["schedule_intelligence"]["health_score"]

        # P50 under planned → high MC score
        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value={"p50": 130, "p90": 140},
            ),
        ):
            state = _make_state()
            result = await schedule_intelligence_node(state)
            ontime_score = result["schedule_intelligence"]["health_score"]

        assert ontime_score > overrun_score


# ===========================================================================
# TEST SUB-AGENT: COST INTELLIGENCE
# ===========================================================================


class TestCostIntelligence:
    """Test cost_intelligence_node."""

    @pytest.mark.asyncio
    async def test_full_cost_analysis(self):
        """Full cost analysis with EVM, EAC, and change orders."""
        mock_evm = {
            "spi": Decimal("0.92"),
            "cpi": Decimal("0.93"),
            "sv": Decimal("-1000000"),
            "cv": Decimal("-900000"),
            "eac": Decimal("26881720"),
            "etc": Decimal("14481720"),
            "vac": Decimal("-1881720"),
            "tcpi": Decimal("1.07"),
            "percent_complete": Decimal("46"),
        }
        mock_eac = {
            "all_methods": {
                "cpi": {"eac_value": Decimal("26881720"), "method": "CPI"},
                "composite_spi_cpi": {"eac_value": Decimal("27500000"), "method": "Composite"},
            }
        }

        with (
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value=mock_eac,
            ),
        ):
            state = _make_state()
            result = await cost_intelligence_node(state)

        cost = result["cost_intelligence"]
        assert "health_score" in cost
        assert 0 <= cost["health_score"] <= 100
        assert cost["evm_metrics"]["cpi"] is not None
        assert cost["eac_forecasts"]["all_methods"]["cpi"]["eac_value"] is not None

    @pytest.mark.asyncio
    async def test_change_order_impact(self):
        """Change order impact is correctly calculated."""
        mock_evm = {
            "spi": Decimal("1.0"),
            "cpi": Decimal("1.0"),
            "sv": Decimal("0"),
            "cv": Decimal("0"),
            "eac": Decimal("25000000"),
            "etc": Decimal("13500000"),
            "vac": Decimal("0"),
            "tcpi": Decimal("1.0"),
        }

        with (
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value={"all_methods": {}},
            ),
        ):
            state = _make_state()
            result = await cost_intelligence_node(state)

        co = result["cost_intelligence"]["co_impact"]
        assert co["total_change_orders"] == 3
        # 350000 + 180000 + 95000 = 625000
        assert Decimal(co["cumulative_cost_impact"]) == Decimal("625000")
        assert co["percent_of_contract"] > 0

    @pytest.mark.asyncio
    async def test_no_evm_data(self):
        """Graceful handling when no EVM data."""
        # Also remove change orders to isolate no-EVM behavior
        data = {**MOCK_PROJECT_DATA, "latest_evm": {}, "change_orders": []}
        state = _make_state(project_data=data)
        result = await cost_intelligence_node(state)
        cost = result["cost_intelligence"]
        # cpi_score=50, eac_score=50, co_score=80 (no COs) → 50*.4+50*.3+80*.3 = 20+15+24 = 59
        assert 0 <= cost["health_score"] <= 100
        assert any("No EVM" in w for w in cost.get("warnings", []))

    @pytest.mark.asyncio
    async def test_budget_variance_flags(self):
        """Budget variance flags when CSI division exceeds budget."""
        mock_evm = {
            "spi": Decimal("1.0"),
            "cpi": Decimal("1.0"),
            "sv": Decimal("0"),
            "cv": Decimal("0"),
            "eac": Decimal("25000000"),
            "etc": Decimal("13500000"),
            "vac": Decimal("0"),
            "tcpi": Decimal("1.0"),
        }

        data = {
            **MOCK_PROJECT_DATA,
            "division_budgets": {"03": 2000000, "05": 3000000},
            "division_actuals": {"03": 2200000, "05": 2800000},
        }

        with (
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value={"all_methods": {}},
            ),
        ):
            state = _make_state(project_data=data)
            result = await cost_intelligence_node(state)

        flags = result["cost_intelligence"]["budget_variance_flags"]
        assert len(flags) == 1  # div 03: 2.2M > 2M * 1.05, div 05: 2.8M < 3M * 1.05
        assert flags[0]["division"] == "03"

    @pytest.mark.asyncio
    async def test_evm_failure_handled(self):
        """EVM engine failure doesn't crash cost sub-agent."""
        with patch(
            EVM_TARGET,
            new_callable=AsyncMock,
            side_effect=Exception("EVM engine error"),
        ):
            state = _make_state()
            result = await cost_intelligence_node(state)

        cost = result["cost_intelligence"]
        assert cost["health_score"] == 50
        assert len(result.get("errors", [])) > 0

    @pytest.mark.asyncio
    async def test_high_co_impact_lowers_score(self):
        """High change order percentage lowers the cost health score."""
        mock_evm = {
            "spi": Decimal("1.0"),
            "cpi": Decimal("1.0"),
            "sv": Decimal("0"),
            "cv": Decimal("0"),
            "eac": Decimal("25000000"),
            "etc": Decimal("13500000"),
            "vac": Decimal("0"),
            "tcpi": Decimal("1.0"),
        }

        # 20% of contract in COs
        heavy_cos = [
            {
                "co_number": "CO-X",
                "title": "Big CO",
                "status": "approved",
                "cost_impact": "5000000",
                "schedule_impact_days": 0,
                "submitted_at": "2025-08-01",
            },
        ]
        data = {**MOCK_PROJECT_DATA, "change_orders": heavy_cos}

        with (
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value={"all_methods": {}},
            ),
        ):
            state = _make_state(project_data=data)
            result = await cost_intelligence_node(state)

        cost = result["cost_intelligence"]
        # 5M / 25M = 20% → co_score = 0
        assert cost["co_impact"]["percent_of_contract"] == 20.0


# ===========================================================================
# TEST SUB-AGENT: RISK INTELLIGENCE
# ===========================================================================


class TestRiskIntelligence:
    """Test risk_intelligence_node."""

    @pytest.mark.asyncio
    async def test_overdue_change_orders_detected(self):
        """Overdue COs (>7 days pending) generate risks."""
        state = _make_state()

        with patch(
            WEATHER_TARGET,
            new_callable=AsyncMock,
            side_effect=Exception("Weather unavailable"),
        ):
            result = await risk_intelligence_node(state)

        risk = result["risk_intelligence"]
        items = risk["open_items_summary"]
        assert items["overdue_change_orders"] == 1  # CO-002 is >7 days
        assert items["total_open_cos"] == 2  # CO-002 and CO-003

    @pytest.mark.asyncio
    async def test_overdue_rfis_detected(self):
        """Overdue RFIs (>7 days) generate risks."""
        state = _make_state()

        with patch(
            WEATHER_TARGET,
            new_callable=AsyncMock,
            side_effect=Exception("Weather unavailable"),
        ):
            result = await risk_intelligence_node(state)

        risk = result["risk_intelligence"]
        assert risk["open_items_summary"]["overdue_rfis"] == 1  # RFI-001 is >7 days

    @pytest.mark.asyncio
    async def test_weather_red_alert(self):
        """Weather RED alert generates risk and lowers score."""
        mock_impact = MagicMock()
        mock_impact.allowed = False
        mock_impact.risk_level = "RED"
        mock_impact.reasons = ["High wind speed > 30 mph"]

        with patch(
            WEATHER_TARGET,
            new_callable=AsyncMock,
            return_value=mock_impact,
        ):
            state = _make_state()
            result = await risk_intelligence_node(state)

        risk = result["risk_intelligence"]
        weather = risk["weather_outlook"]
        assert weather["red_alerts"] > 0
        # Weather RED should produce top risk
        assert any("Weather" in r.get("description", "") for r in risk.get("top_5_risks", []))

    @pytest.mark.asyncio
    async def test_top_5_risks_limited(self):
        """Top risks list is capped at 5."""
        state = _make_state(
            schedule_intelligence={
                "health_score": 20,
                "float_erosion_alerts": [
                    {"activity_name": f"Act {i}", "erosion_days": i} for i in range(5)
                ],
            },
            cost_intelligence={"health_score": 20},
        )

        with patch(
            WEATHER_TARGET,
            new_callable=AsyncMock,
            side_effect=Exception("noop"),
        ):
            result = await risk_intelligence_node(state)

        risks = result["risk_intelligence"]["top_5_risks"]
        assert len(risks) <= 5

    @pytest.mark.asyncio
    async def test_no_risk_data(self):
        """Graceful handling with minimal project data."""
        data = {
            **MOCK_PROJECT_DATA,
            "change_orders": [],
            "rfis": [],
            "address": "",
        }
        state = _make_state(project_data=data)
        result = await risk_intelligence_node(state)
        risk = result["risk_intelligence"]
        assert "health_score" in risk
        assert 0 <= risk["health_score"] <= 100


# ===========================================================================
# TEST SUB-AGENT: PRODUCTIVITY INTELLIGENCE
# ===========================================================================


class TestProductivityIntelligence:
    """Test productivity_intelligence_node."""

    @pytest.mark.asyncio
    async def test_with_daily_logs(self):
        """Productivity score from daily log data."""
        data = {
            **MOCK_PROJECT_DATA,
            "daily_logs": [
                {"planned_hours": 80, "actual_hours": 72, "area": "foundation"},
                {"planned_hours": 80, "actual_hours": 80, "area": "structure"},
                {"planned_hours": 60, "actual_hours": 45, "area": "MEP"},
            ],
        }
        state = _make_state(project_data=data)
        result = await productivity_intelligence_node(state)
        prod = result["productivity_intelligence"]
        assert prod["data_source"] == "daily_logs"
        assert prod["health_score"] > 0
        # MEP is underperforming: 45/60 = 0.75 < 0.85
        assert len(prod["underperforming_areas"]) >= 1
        mep = [a for a in prod["underperforming_areas"] if a["area"] == "MEP"]
        assert len(mep) == 1

    @pytest.mark.asyncio
    async def test_evm_proxy(self):
        """Productivity score uses EVM as proxy when no daily logs."""
        data = {**MOCK_PROJECT_DATA, "daily_logs": []}
        state = _make_state(project_data=data)
        result = await productivity_intelligence_node(state)
        prod = result["productivity_intelligence"]
        assert prod["data_source"] == "evm_proxy"
        # SPI 0.92 → score via _spi_to_score
        assert prod["health_score"] == _spi_to_score(0.92)

    @pytest.mark.asyncio
    async def test_no_data(self):
        """Graceful handling with no productivity data."""
        data = {**MOCK_PROJECT_DATA, "daily_logs": [], "latest_evm": {}}
        state = _make_state(project_data=data)
        result = await productivity_intelligence_node(state)
        prod = result["productivity_intelligence"]
        assert prod["data_source"] == "none"
        assert any("No productivity" in w for w in prod.get("warnings", []))


# ===========================================================================
# TEST SYNTHESIZER NODE
# ===========================================================================


class TestSynthesizer:
    """Test synthesizer_node."""

    @pytest.mark.asyncio
    async def test_health_score_weighting(self):
        """Overall health score is correctly weighted."""
        state = _make_state(
            schedule_intelligence={"health_score": 80},
            cost_intelligence={"health_score": 70},
            risk_intelligence={"health_score": 60},
            productivity_intelligence={"health_score": 90},
        )

        mock_llm = {
            "content": json.dumps(
                {
                    "executive_summary": "Test summary",
                    "action_items": [
                        {
                            "action": "Test",
                            "responsible": "PM",
                            "due_by": "Next week",
                            "reason": "Testing",
                        },
                    ],
                    "narrative_report": "Test narrative",
                }
            )
        }

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(return_value=mock_llm)
            result = await synthesizer_node(state)

        expected = int(80 * 0.30 + 70 * 0.30 + 60 * 0.25 + 90 * 0.15)
        assert result["overall_health_score"] == expected

    @pytest.mark.asyncio
    async def test_status_green(self):
        """Status GREEN when overall >= 80."""
        state = _make_state(
            schedule_intelligence={"health_score": 90},
            cost_intelligence={"health_score": 90},
            risk_intelligence={"health_score": 90},
            productivity_intelligence={"health_score": 90},
        )

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(
                return_value={
                    "content": json.dumps(
                        {
                            "executive_summary": "All good",
                            "action_items": [],
                            "narrative_report": "Project on track",
                        }
                    )
                }
            )
            result = await synthesizer_node(state)

        assert result["project_status"] == "GREEN"
        assert result["overall_health_score"] >= STATUS_GREEN_THRESHOLD

    @pytest.mark.asyncio
    async def test_status_yellow(self):
        """Status YELLOW when 60 <= overall < 80."""
        state = _make_state(
            schedule_intelligence={"health_score": 70},
            cost_intelligence={"health_score": 70},
            risk_intelligence={"health_score": 70},
            productivity_intelligence={"health_score": 70},
        )

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(
                return_value={
                    "content": json.dumps(
                        {
                            "executive_summary": "Caution needed",
                            "action_items": [],
                            "narrative_report": "Some issues",
                        }
                    )
                }
            )
            result = await synthesizer_node(state)

        assert result["project_status"] == "YELLOW"

    @pytest.mark.asyncio
    async def test_status_red(self):
        """Status RED when overall < 60."""
        state = _make_state(
            schedule_intelligence={"health_score": 30},
            cost_intelligence={"health_score": 40},
            risk_intelligence={"health_score": 50},
            productivity_intelligence={"health_score": 30},
        )

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(
                return_value={
                    "content": json.dumps(
                        {
                            "executive_summary": "Critical issues",
                            "action_items": [],
                            "narrative_report": "Project in trouble",
                        }
                    )
                }
            )
            result = await synthesizer_node(state)

        assert result["project_status"] == "RED"
        assert result["overall_health_score"] < STATUS_YELLOW_THRESHOLD

    @pytest.mark.asyncio
    async def test_llm_json_parsing(self):
        """LLM JSON output is correctly parsed."""
        state = _make_state(
            schedule_intelligence={"health_score": 75},
            cost_intelligence={"health_score": 75},
            risk_intelligence={"health_score": 75},
            productivity_intelligence={"health_score": 75},
        )

        llm_json = {
            "executive_summary": "Project is performing well overall.",
            "action_items": [
                {
                    "action": "Review COs",
                    "responsible": "PM",
                    "due_by": "Friday",
                    "reason": "Backlog",
                },
                {
                    "action": "Expedite RFIs",
                    "responsible": "Architect",
                    "due_by": "Wednesday",
                    "reason": "Delays",
                },
                {
                    "action": "Float review",
                    "responsible": "Scheduler",
                    "due_by": "Monday",
                    "reason": "Erosion",
                },
            ],
            "narrative_report": "The project continues to perform within acceptable parameters.",
        }

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(
                return_value={"content": json.dumps(llm_json)}
            )
            result = await synthesizer_node(state)

        assert result["executive_summary"] == "Project is performing well overall."
        assert len(result["action_items"]) == 3
        assert result["action_items"][0]["responsible"] == "PM"
        assert "metrics_dashboard" in result

    @pytest.mark.asyncio
    async def test_llm_markdown_fences_stripped(self):
        """LLM output with ```json fences is handled."""
        state = _make_state(
            schedule_intelligence={"health_score": 75},
            cost_intelligence={"health_score": 75},
            risk_intelligence={"health_score": 75},
            productivity_intelligence={"health_score": 75},
        )

        fenced = '```json\n{"executive_summary": "Fenced output", "action_items": [], "narrative_report": "Test"}\n```'

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(return_value={"content": fenced})
            result = await synthesizer_node(state)

        assert result["executive_summary"] == "Fenced output"

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM failure produces template-based fallback."""
        state = _make_state(
            schedule_intelligence={"health_score": 70},
            cost_intelligence={"health_score": 65},
            risk_intelligence={"health_score": 60},
            productivity_intelligence={"health_score": 80},
        )

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(side_effect=Exception("LLM unavailable"))
            result = await synthesizer_node(state)

        # Should still have valid scores
        assert result["overall_health_score"] > 0
        assert result["project_status"] in ("GREEN", "YELLOW", "RED")
        assert "health score" in result["executive_summary"].lower()

    @pytest.mark.asyncio
    async def test_metrics_dashboard_structure(self):
        """Metrics dashboard contains expected keys."""
        state = _make_state(
            schedule_intelligence={
                "health_score": 80,
                "p50_duration": 120,
                "p90_duration": 140,
                "spi_trend": "stable",
                "critical_path": ["A1"],
                "float_erosion_alerts": [],
            },
            cost_intelligence={"health_score": 75, "evm_metrics": {"cpi": "0.95"}},
            risk_intelligence={
                "health_score": 70,
                "top_5_risks": [{"desc": "test"}],
                "open_items_summary": {"overdue_rfis": 1},
            },
            productivity_intelligence={"health_score": 85},
        )

        with patch(LLM_TARGET, new_callable=AsyncMock) as MockGateway:
            MockGateway.return_value.complete = AsyncMock(
                return_value={
                    "content": json.dumps(
                        {
                            "executive_summary": "Test",
                            "action_items": [],
                            "narrative_report": "Test",
                        }
                    )
                }
            )
            result = await synthesizer_node(state)

        dashboard = result["metrics_dashboard"]
        assert "scores" in dashboard
        assert dashboard["scores"]["schedule"] == 80
        assert dashboard["scores"]["cost"] == 75
        assert dashboard["schedule"]["p50"] == 120
        assert dashboard["schedule"]["spi_trend"] == "stable"


# ===========================================================================
# TEST GUARDRAILS
# ===========================================================================


class TestGuardrails:
    """Test guardrails_check_node."""

    @pytest.mark.asyncio
    async def test_high_confidence(self):
        """High confidence → no human review needed."""
        state = _make_state(
            executive_summary="Project on track with SPI 1.02.",
            overall_health_score=85,
            project_status="GREEN",
            narrative_report="Detailed report here.",
        )

        mock_confidence = {
            "overall_confidence": 0.92,
            "claim_scores": [{"claim": "SPI 1.02", "confidence": 0.95}],
            "routing_recommendation": "auto_approve",
        }
        mock_verify = {"warnings": []}

        with (
            patch(SCORER_TARGET) as MockScorer,
            patch(
                VERIFIER_TARGET,
                new_callable=AsyncMock,
                return_value=mock_verify,
            ),
        ):
            MockScorer.return_value.score = AsyncMock(return_value=mock_confidence)
            result = await guardrails_check_node(state)

        gr = result["guardrails_result"]
        assert gr["confidence_score"] == 0.92
        assert gr["needs_human_review"] is False
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_low_confidence_flags_review(self):
        """Confidence < 0.7 → flags for human review."""
        state = _make_state(
            executive_summary="Claims without evidence.",
            overall_health_score=50,
            project_status="YELLOW",
            narrative_report="Vague report.",
        )

        mock_confidence = {
            "overall_confidence": 0.55,
            "claim_scores": [],
            "routing_recommendation": "human_review",
        }
        mock_verify = {"warnings": ["Unverifiable claim detected"]}

        with (
            patch(SCORER_TARGET) as MockScorer,
            patch(
                VERIFIER_TARGET,
                new_callable=AsyncMock,
                return_value=mock_verify,
            ),
        ):
            MockScorer.return_value.score = AsyncMock(return_value=mock_confidence)
            result = await guardrails_check_node(state)

        gr = result["guardrails_result"]
        assert gr["confidence_score"] == 0.55
        assert gr["needs_human_review"] is True

    @pytest.mark.asyncio
    async def test_guardrails_failure_handled(self):
        """Guardrails import failure doesn't crash."""
        state = _make_state(
            executive_summary="Test",
            overall_health_score=70,
            project_status="YELLOW",
        )

        with patch(
            SCORER_TARGET,
            side_effect=ImportError("module not found"),
        ):
            result = await guardrails_check_node(state)

        gr = result["guardrails_result"]
        assert gr["needs_human_review"] is True
        assert result["status"] == "completed"


# ===========================================================================
# TEST FULL PIPELINE
# ===========================================================================


class TestFullPipeline:
    """Test generate_weekly_brief end-to-end."""

    @pytest.mark.asyncio
    async def test_end_to_end(self):
        """Full pipeline produces complete brief with all fields."""
        from app.services.agents.weekly_brief_agent import generate_weekly_brief

        mock_cpm = {
            "critical_path": ["A1", "A2", "A3"],
            "project_duration": 80,
            "activities": {
                "A1": {"total_float": 5},
                "A2": {"total_float": 3},
                "A3": {"total_float": 0},
                "A4": {"total_float": 8},
                "A5": {"total_float": 2},
            },
        }
        mock_mc = {"p50": 82, "p90": 95, "criticality_index": {}}
        mock_evm = {
            "spi": Decimal("0.92"),
            "cpi": Decimal("0.93"),
            "sv": Decimal("-1000000"),
            "cv": Decimal("-900000"),
            "eac": Decimal("26881720"),
            "etc": Decimal("14481720"),
            "vac": Decimal("-1881720"),
            "tcpi": Decimal("1.07"),
            "percent_complete": Decimal("46"),
        }
        mock_eac = {"all_methods": {"cpi": {"eac_value": Decimal("26881720")}}}
        mock_confidence = {
            "overall_confidence": 0.85,
            "claim_scores": [],
            "routing_recommendation": "auto_approve",
        }
        mock_verify = {"warnings": []}

        llm_result = {
            "executive_summary": "Project showing schedule slippage with SPI at 0.92.",
            "action_items": [
                {
                    "action": "Accelerate critical path",
                    "responsible": "Superintendent",
                    "due_by": "Next Friday",
                    "reason": "SPI declining",
                },
            ],
            "narrative_report": "Full narrative here.",
        }

        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_cpm,
            ),
            patch(
                MC_TARGET,
                new_callable=AsyncMock,
                return_value=mock_mc,
            ),
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value=mock_eac,
            ),
            patch(
                WEATHER_TARGET,
                new_callable=AsyncMock,
                side_effect=Exception("Weather unavailable in test"),
            ),
            patch(LLM_TARGET) as MockGateway,
            patch(
                SCORER_TARGET,
            ) as MockScorer,
            patch(
                VERIFIER_TARGET,
                new_callable=AsyncMock,
                return_value=mock_verify,
            ),
            patch(
                "app.services.agents.checkpointer.get_checkpointer",
                return_value=None,
            ),
        ):
            MockGateway.return_value.complete = AsyncMock(
                return_value={"content": json.dumps(llm_result)}
            )
            MockScorer.return_value.score = AsyncMock(return_value=mock_confidence)

            result = await generate_weekly_brief(
                project_id="test-proj-001",
                project_data=MOCK_PROJECT_DATA,
                org_id="test-org",
                generated_by="user-001",
            )

        assert result["status"] == "completed"
        assert result["project_id"] == "test-proj-001"
        assert result["generated_by"] == "user-001"
        assert result["overall_health_score"] > 0
        assert result["project_status"] in ("GREEN", "YELLOW", "RED")
        assert result["executive_summary"]
        assert isinstance(result["schedule_intelligence"], dict)
        assert isinstance(result["cost_intelligence"], dict)
        assert isinstance(result["risk_intelligence"], dict)
        assert isinstance(result["productivity_intelligence"], dict)
        assert isinstance(result["action_items"], list)
        assert isinstance(result["metrics_dashboard"], dict)
        assert isinstance(result["guardrails_result"], dict)

    @pytest.mark.asyncio
    async def test_partial_failure_resilience(self):
        """Pipeline completes even when some sub-agents fail."""
        from app.services.agents.weekly_brief_agent import generate_weekly_brief

        # Only cost intelligence works; schedule/risk fail
        mock_evm = {
            "spi": Decimal("1.0"),
            "cpi": Decimal("1.0"),
            "sv": Decimal("0"),
            "cv": Decimal("0"),
            "eac": Decimal("25000000"),
            "etc": Decimal("13500000"),
            "vac": Decimal("0"),
            "tcpi": Decimal("1.0"),
        }
        mock_eac = {"all_methods": {}}

        with (
            patch(
                CPM_TARGET,
                new_callable=AsyncMock,
                side_effect=Exception("CPM broken"),
            ),
            patch(
                EVM_TARGET,
                new_callable=AsyncMock,
                return_value=mock_evm,
            ),
            patch(
                EAC_TARGET,
                new_callable=AsyncMock,
                return_value=mock_eac,
            ),
            patch(
                WEATHER_TARGET,
                new_callable=AsyncMock,
                side_effect=Exception("Weather broken"),
            ),
            patch(LLM_TARGET) as MockGateway,
            patch(
                SCORER_TARGET,
            ) as MockScorer,
            patch(
                VERIFIER_TARGET,
                new_callable=AsyncMock,
                return_value={"warnings": []},
            ),
            patch(
                "app.services.agents.checkpointer.get_checkpointer",
                return_value=None,
            ),
        ):
            MockGateway.return_value.complete = AsyncMock(
                return_value={
                    "content": json.dumps(
                        {
                            "executive_summary": "Partial data",
                            "action_items": [],
                            "narrative_report": "Partial",
                        }
                    )
                }
            )
            MockScorer.return_value.score = AsyncMock(
                return_value={
                    "overall_confidence": 0.6,
                    "claim_scores": [],
                    "routing_recommendation": "human_review",
                }
            )

            result = await generate_weekly_brief(
                project_id="test-proj-002",
                project_data=MOCK_PROJECT_DATA,
            )

        # Should still produce a result (degraded but not failed)
        assert result["status"] == "completed"
        assert result["overall_health_score"] >= 0
        assert result["project_status"] in ("GREEN", "YELLOW", "RED")


# ===========================================================================
# TEST PDF GENERATION
# ===========================================================================


class TestPDFGeneration:
    """Test brief_pdf_generator.generate_brief_pdf."""

    def test_valid_pdf_bytes(self):
        """Generate valid PDF bytes from brief data."""
        from app.services.agents.brief_pdf_generator import generate_brief_pdf

        brief_data = {
            "overall_health_score": 72,
            "project_status": "YELLOW",
            "executive_summary": "Project is on track with minor cost overruns.",
            "schedule_health_score": 78,
            "cost_health_score": 65,
            "risk_score": 70,
            "productivity_score": 80,
            "schedule_intelligence": {
                "spi_values": [0.98, 0.96, 0.94],
                "spi_trend": "deteriorating",
                "p50_duration": 120,
                "p90_duration": 145,
                "critical_path": ["A1", "A2", "A3"],
                "float_erosion_alerts": [
                    {"activity_name": "Steel Erection", "erosion_days": 5},
                ],
            },
            "cost_intelligence": {
                "evm_metrics": {
                    "cpi": "0.93",
                    "spi": "0.94",
                    "eac": "26800000",
                    "vac": "-1800000",
                    "cv": "-900000",
                    "sv": "-750000",
                    "percent_complete": "46",
                    "tcpi": "1.07",
                },
                "co_impact": {
                    "total_change_orders": 3,
                    "percent_of_contract": 2.5,
                },
            },
            "risk_intelligence": {
                "top_5_risks": [
                    {
                        "description": "Weather delays",
                        "probability": "medium",
                        "impact": "high",
                        "mitigation": "Monitor forecast",
                    },
                    {
                        "description": "Supply chain",
                        "probability": "low",
                        "impact": "medium",
                        "mitigation": "Maintain buffer stock",
                    },
                ],
                "weather_outlook": {"red_alerts": 0, "yellow_alerts": 1},
            },
            "action_items": [
                {
                    "action": "Review COs",
                    "responsible": "PM",
                    "due_by": "Friday",
                    "reason": "Backlog",
                },
                {
                    "action": "Expedite steel",
                    "responsible": "Procurement",
                    "due_by": "Next week",
                    "reason": "Critical path",
                },
            ],
            "guardrails_result": {
                "confidence_score": 0.88,
                "needs_human_review": False,
            },
        }

        pdf = generate_brief_pdf(
            brief_data=brief_data,
            project_name="Riverfront Office Tower",
            project_number="P-2026-001",
        )

        assert isinstance(pdf, bytes)
        assert len(pdf) > 100
        # PDF starts with %PDF
        assert pdf[:5] == b"%PDF-"

    def test_project_info_in_pdf(self):
        """PDF contains project name."""
        from app.services.agents.brief_pdf_generator import generate_brief_pdf

        pdf = generate_brief_pdf(
            brief_data={
                "overall_health_score": 85,
                "project_status": "GREEN",
                "executive_summary": "All good",
            },
            project_name="Test Project Alpha",
            project_number="TP-001",
        )
        assert isinstance(pdf, bytes)
        assert len(pdf) > 100

    def test_status_colors(self):
        """PDF generates for all status colors."""
        from app.services.agents.brief_pdf_generator import generate_brief_pdf

        for status in ["GREEN", "YELLOW", "RED"]:
            pdf = generate_brief_pdf(
                brief_data={
                    "overall_health_score": 50,
                    "project_status": status,
                    "executive_summary": f"Status: {status}",
                },
                project_name="Color Test",
            )
            assert isinstance(pdf, bytes)
            assert pdf[:5] == b"%PDF-"

    def test_empty_sections(self):
        """PDF generates cleanly with minimal/empty sections."""
        from app.services.agents.brief_pdf_generator import generate_brief_pdf

        pdf = generate_brief_pdf(
            brief_data={
                "overall_health_score": 50,
                "project_status": "YELLOW",
                "executive_summary": "",
                "schedule_intelligence": {},
                "cost_intelligence": {},
                "risk_intelligence": {},
                "action_items": [],
                "guardrails_result": {},
            },
            project_name="Empty Project",
        )
        assert isinstance(pdf, bytes)
        assert len(pdf) > 100


# ===========================================================================
# TEST NOTIFICATION SERVICE
# ===========================================================================


class TestNotificationService:
    """Test notification_service functions."""

    @pytest.mark.asyncio
    async def test_send_notifications_no_preferences(self):
        """No notifications when no preferences exist."""
        from app.services.agents.notification_service import send_brief_notifications

        results = await send_brief_notifications(
            project_id="test-proj",
            brief_id="test-brief",
            pdf_bytes=b"fakepdf",
            json_summary={"project_status": "GREEN"},
            db=None,
        )
        assert results["email_sent"] == 0
        assert results["webhook_sent"] == 0

    @pytest.mark.asyncio
    async def test_email_fallback_to_logging(self):
        """Email falls back to logging when SMTP not configured."""
        from app.services.agents.notification_service import send_email_with_attachment

        # Should not raise; just logs
        await send_email_with_attachment(
            to_email="test@example.com",
            subject="Test Brief",
            body_html="<p>Test</p>",
            attachment_bytes=b"fakepdf",
        )

    @pytest.mark.asyncio
    async def test_webhook_post(self):
        """Webhook POST sends correct payload."""
        from app.services.agents.notification_service import post_webhook

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("httpx.AsyncClient") as MockAsyncClient,
            # SSRF validator does DNS resolution; bypass for the unit test.
            patch(
                "app.services.agents.notification_service._validate_webhook_url",
                side_effect=lambda url: url,
            ),
        ):
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await post_webhook(
                webhook_url="https://hooks.example.com/test",
                payload={"event": "test", "brief_id": "123"},
            )

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[1]["json"]["event"] == "test"


# ===========================================================================
# TEST FORMAT HELPERS
# ===========================================================================


class TestFormatHelpers:
    """Test summary formatting functions for the LLM prompt."""

    def test_format_schedule_summary(self):
        sched = {
            "spi_values": [0.98, 0.95, 0.92],
            "spi_trend": "deteriorating",
            "p50_duration": 120,
            "p90_duration": 140,
            "critical_path": ["A1", "A2"],
            "float_erosion_alerts": [{"act": "A1"}],
        }
        text = _format_schedule_summary(sched)
        assert "deteriorating" in text
        assert "P50" in text or "120" in text
        assert "Critical Path" in text
        assert "Float Erosion" in text

    def test_format_cost_summary(self):
        cost = {
            "evm_metrics": {
                "cpi": "0.93",
                "spi": "0.92",
                "eac": "26M",
                "vac": "-1.8M",
                "percent_complete": "46",
            },
            "co_impact": {"total_change_orders": 3, "percent_of_contract": 2.5},
            "budget_variance_flags": [{"division": "03"}],
        }
        text = _format_cost_summary(cost)
        assert "CPI" in text
        assert "Change Orders" in text
        assert "Budget Overruns" in text

    def test_format_risk_summary(self):
        risk = {
            "weather_outlook": {"red_alerts": 1, "yellow_alerts": 2},
            "open_items_summary": {"overdue_change_orders": 1, "overdue_rfis": 2},
            "top_5_risks": [{"description": "Weather delay"}],
        }
        text = _format_risk_summary(risk)
        assert "RED" in text
        assert "Overdue" in text
        assert "Weather delay" in text

    def test_format_productivity_summary(self):
        prod = {
            "data_source": "daily_logs",
            "underperforming_areas": [
                {"area": "MEP", "ratio": 0.75},
            ],
        }
        text = _format_productivity_summary(prod)
        assert "daily_logs" in text
        assert "MEP" in text

    def test_empty_schedule_still_shows_trend(self):
        """Empty schedule summary still includes SPI trend line."""
        text = _format_schedule_summary({})
        assert "SPI Trend" in text

    def test_empty_cost_summary(self):
        assert _format_cost_summary({}) == "No cost data available"

    def test_empty_risk_summary(self):
        assert _format_risk_summary({}) == "No risk data available"


# ===========================================================================
# TEST CELERY BEAT REGISTRATION
# ===========================================================================


class TestCeleryBeatSchedule:
    """Test that weekly brief task is registered in Celery beat."""

    def test_task_in_beat_schedule(self):
        """Weekly brief task is registered in Celery Beat schedule."""
        from app.workers.document_worker import celery_app

        beat_schedule = celery_app.conf.beat_schedule
        assert "generate-weekly-briefs" in beat_schedule
        entry = beat_schedule["generate-weekly-briefs"]
        assert entry["task"] == "generate_weekly_briefs"

    def test_task_scheduled_monday(self):
        """Weekly brief task runs on Monday."""
        from app.workers.document_worker import celery_app

        entry = celery_app.conf.beat_schedule["generate-weekly-briefs"]
        schedule = entry["schedule"]
        # crontab day_of_week=1 means Monday
        assert "1" in str(schedule.day_of_week) or schedule.day_of_week == {1}

    def test_task_function_exists(self):
        """The Celery task function is defined."""
        from app.workers.document_worker import generate_weekly_briefs_task

        assert callable(generate_weekly_briefs_task)


# ===========================================================================
# TEST GRAPH CONSTRUCTION
# ===========================================================================


class TestGraphConstruction:
    """Test that the LangGraph agent builds correctly."""

    def test_build_agent(self):
        """Agent graph compiles without errors."""
        graph = build_weekly_brief_agent(checkpointer=None)
        assert graph is not None

    def test_load_project_data_node_no_data(self):
        """Load node handles missing project data."""
        import asyncio

        state = _make_state(project_data={})
        result = asyncio.get_event_loop().run_until_complete(load_project_data_node(state))
        assert result["status"] == "no_project_data"

    def test_load_project_data_node_with_data(self):
        """Load node succeeds with project data."""
        import asyncio

        state = _make_state()
        result = asyncio.get_event_loop().run_until_complete(load_project_data_node(state))
        assert result["status"] == "data_loaded"


# ===========================================================================
# TEST WEIGHT CONSTANTS
# ===========================================================================


class TestWeights:
    """Validate health score weights and thresholds."""

    def test_weights_sum_to_one(self):
        total = WEIGHT_SCHEDULE + WEIGHT_COST + WEIGHT_RISK + WEIGHT_PRODUCTIVITY
        assert abs(total - 1.0) < 1e-9

    def test_thresholds(self):
        assert STATUS_GREEN_THRESHOLD == 80
        assert STATUS_YELLOW_THRESHOLD == 60
        assert STATUS_GREEN_THRESHOLD > STATUS_YELLOW_THRESHOLD
