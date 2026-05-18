"""Tests for predictive safety risk engine.

Covers realistic construction scenarios:
- Hot summer day with roofing activity (heat illness risk)
- Excavation day after heavy rain (cave-in risk)
- Steel erection on a windy day (fall + crane risk)
- First week of a new project (elevated risk for new workers)
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.safety.predictive_risk import (
    DailyRiskResult,
    PredictiveRiskEngine,
    RiskCategory,
    _clamp,
    _heat_index,
    _score_label,
    store_risk_score,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_project(**overrides) -> dict:
    """Create a minimal project dict."""
    base = {
        "name": "Test Construction Project",
        "type": "commercial",
        "address": "123 Main St, Austin, TX 78701",
        "start_date": (date.today() - timedelta(days=90)).isoformat(),
        "naics_code": "2362",
    }
    base.update(overrides)
    return base


def _make_weather(
    temp_max: float = 72,
    temp_min: float = 55,
    wind: float = 5,
    precip: float = 0,
    humidity: float = 50,
) -> list[dict]:
    """Create a weather forecast list."""
    return [
        {
            "date": date.today().isoformat(),
            "temperature_max": temp_max,
            "temperature_min": temp_min,
            "wind_speed_max": wind,
            "precipitation_mm": precip,
            "humidity": humidity,
            "weather_code": 0,
        }
    ]


def _make_activities(*names: str) -> list[dict]:
    """Create schedule activity dicts."""
    return [
        {"name": name, "activity_code": f"A{i}", "is_critical": False, "status": "in_progress"}
        for i, name in enumerate(names)
    ]


def _make_daily_log(crew_count: int = 25) -> dict:
    return {"crew_count": crew_count, "manpower_by_trade": {}}


def _empty_osha() -> dict:
    """OSHA query result with no violations."""
    return {
        "total_inspections": 0,
        "total_violations": 0,
        "violation_rate": 0.0,
        "top_standards": [],
        "category_rates": {},
    }


def _high_fall_osha() -> dict:
    """OSHA data showing high fall violation rate."""
    return {
        "total_inspections": 500,
        "total_violations": 400,
        "violation_rate": 0.8,
        "top_standards": [
            {
                "standard": "1926.501",
                "count": 200,
                "serious_count": 150,
                "willful_count": 5,
                "avg_gravity": 7.0,
            },
            {
                "standard": "1926.451",
                "count": 100,
                "serious_count": 80,
                "willful_count": 2,
                "avg_gravity": 6.0,
            },
        ],
        "category_rates": {
            "fall_risk": 0.6,
            "struck_by_risk": 0.1,
            "electrical_risk": 0.05,
            "excavation_risk": 0.05,
            "heat_illness_risk": 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------


class TestHeatIndex:
    """Test NWS heat index calculation."""

    def test_below_80_returns_temp(self):
        assert _heat_index(75, 50) == 75

    def test_hot_humid(self):
        hi = _heat_index(95, 80)
        assert hi > 110  # Should be significantly above temperature

    def test_hot_dry(self):
        hi = _heat_index(95, 20)
        assert 90 < hi < 100  # Dry heat is less dangerous

    def test_moderate(self):
        hi = _heat_index(85, 50)
        assert 80 < hi < 95


class TestScoreLabel:
    def test_low(self):
        assert _score_label(10) == "low"

    def test_moderate(self):
        assert _score_label(25) == "moderate"

    def test_elevated(self):
        assert _score_label(45) == "elevated"

    def test_high(self):
        assert _score_label(65) == "high"

    def test_critical(self):
        assert _score_label(85) == "critical"


class TestClamp:
    def test_within_range(self):
        assert _clamp(50) == 50

    def test_below(self):
        assert _clamp(-10) == 0

    def test_above(self):
        assert _clamp(150) == 100


# ---------------------------------------------------------------------------
# Feature gathering
# ---------------------------------------------------------------------------


class TestFeatureGathering:
    """Test _gather_features with various inputs."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_basic_features(self):
        project = _make_project()
        features = self.engine._gather_features(
            project,
            None,
            None,
            None,
            date.today(),
        )
        assert features["project_type"] == "commercial"
        assert features["state"] == "TX"
        assert features["month"] == date.today().month

    def test_new_project_age(self):
        project = _make_project(
            start_date=(date.today() - timedelta(days=15)).isoformat(),
        )
        features = self.engine._gather_features(
            project,
            None,
            None,
            None,
            date.today(),
        )
        assert features["project_age_months"] < 1.0

    def test_weather_features(self):
        weather = _make_weather(temp_max=95, humidity=80, wind=25)
        features = self.engine._gather_features(
            _make_project(),
            weather,
            None,
            None,
            date.today(),
        )
        assert features["temp_high"] == 95
        assert features["wind_speed"] == 25
        assert features["heat_index"] > 100

    def test_activity_detection(self):
        activities = _make_activities("Steel Erection Phase 2", "Roof Sheathing")
        features = self.engine._gather_features(
            _make_project(),
            None,
            activities,
            None,
            date.today(),
        )
        assert features["has_steel"]
        assert features["has_roof"]
        assert "fall_risk" in features["active_hazards"]

    def test_excavation_detection(self):
        activities = _make_activities("Excavation for Foundation", "Trench Utilities")
        features = self.engine._gather_features(
            _make_project(),
            None,
            activities,
            None,
            date.today(),
        )
        assert features["has_excavat"]
        assert features["has_trench"]
        assert "excavation_risk" in features["active_hazards"]

    def test_no_weather_defaults(self):
        features = self.engine._gather_features(
            _make_project(),
            None,
            None,
            None,
            date.today(),
        )
        assert features["temp_high"] == 72
        assert features["wind_speed"] == 5

    def test_worker_count_from_daily_log(self):
        log = _make_daily_log(crew_count=42)
        features = self.engine._gather_features(
            _make_project(),
            None,
            None,
            log,
            date.today(),
        )
        assert features["num_workers"] == 42


# ---------------------------------------------------------------------------
# Individual scoring functions
# ---------------------------------------------------------------------------


class TestFallRiskScoring:
    """Test fall risk scoring in isolation."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_baseline_low(self):
        features = self.engine._gather_features(
            _make_project(),
            _make_weather(),
            None,
            None,
            date.today(),
        )
        result = self.engine._score_fall_risk(features, _empty_osha())
        assert result.score <= 20
        assert result.label in ("low", "moderate")

    def test_roofing_increases_score(self):
        activities = _make_activities("Roof Installation")
        features = self.engine._gather_features(
            _make_project(),
            _make_weather(),
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_fall_risk(features, _empty_osha())
        assert result.score >= 30
        assert len(result.factors) >= 1
        assert any("height" in f.lower() for f in result.factors)

    def test_high_wind_plus_heights(self):
        weather = _make_weather(wind=35)
        activities = _make_activities("Steel Erection")
        features = self.engine._gather_features(
            _make_project(),
            weather,
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_fall_risk(features, _empty_osha())
        assert result.score >= 55
        assert any("wind" in f.lower() for f in result.factors)
        assert any("suspend" in m.lower() for m in result.mitigations)

    def test_osha_high_fall_rate(self):
        features = self.engine._gather_features(
            _make_project(),
            _make_weather(),
            None,
            None,
            date.today(),
        )
        result = self.engine._score_fall_risk(features, _high_fall_osha())
        assert result.score >= 20
        assert any("violation rate" in f.lower() for f in result.factors)

    def test_new_project_bonus(self):
        project = _make_project(
            start_date=(date.today() - timedelta(days=5)).isoformat(),
        )
        features = self.engine._gather_features(
            project,
            _make_weather(),
            None,
            None,
            date.today(),
        )
        result = self.engine._score_fall_risk(features, _empty_osha())
        assert any("first month" in f.lower() for f in result.factors)


class TestStruckByScoring:
    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_crane_operations(self):
        activities = _make_activities("Crane Pick - Steel Columns")
        features = self.engine._gather_features(
            _make_project(),
            _make_weather(),
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_struck_by_risk(features, _empty_osha())
        assert result.score >= 25
        assert any("crane" in f.lower() for f in result.factors)

    def test_crane_plus_wind(self):
        weather = _make_weather(wind=30)
        activities = _make_activities("Crane Operations - Precast")
        features = self.engine._gather_features(
            _make_project(),
            weather,
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_struck_by_risk(features, _empty_osha())
        assert result.score >= 45
        assert any("wind" in f.lower() for f in result.factors)


class TestExcavationScoring:
    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_excavation_after_heavy_rain(self):
        """Excavation day after heavy rain should flag cave-in risk."""
        weather = _make_weather(precip=25)  # Heavy rain
        activities = _make_activities("Excavation for Grade Beam")
        features = self.engine._gather_features(
            _make_project(),
            weather,
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_excavation_risk(features, _empty_osha())
        assert result.score >= 55
        assert result.label in ("high", "critical")
        assert any("saturated" in f.lower() or "cave-in" in f.lower() for f in result.factors)
        assert any("re-inspect" in m.lower() for m in result.mitigations)

    def test_dry_excavation(self):
        activities = _make_activities("Excavation for Footings")
        features = self.engine._gather_features(
            _make_project(),
            _make_weather(),
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_excavation_risk(features, _empty_osha())
        # Should still be elevated but not critical
        assert 25 <= result.score <= 50
        assert any("competent person" in m.lower() for m in result.mitigations)


class TestHeatRiskScoring:
    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_hot_summer_roofing(self):
        """Hot summer day with roofing should flag heat illness risk."""
        weather = _make_weather(temp_max=98, temp_min=78, humidity=75)
        activities = _make_activities("Roof Membrane Installation")
        features = self.engine._gather_features(
            _make_project(),
            weather,
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_heat_risk(features, _empty_osha())
        assert result.score >= 40
        assert any("heat" in f.lower() for f in result.factors)
        assert any(
            "water" in m.lower() or "break" in m.lower() or "halt" in m.lower()
            for m in result.mitigations
        )

    def test_extreme_heat(self):
        weather = _make_weather(temp_max=110, humidity=40)
        features = self.engine._gather_features(
            _make_project(),
            weather,
            None,
            None,
            date.today(),
        )
        result = self.engine._score_heat_risk(features, _empty_osha())
        assert result.score >= 50

    def test_cold_weather(self):
        weather = _make_weather(temp_max=25, temp_min=10)
        features = self.engine._gather_features(
            _make_project(),
            weather,
            None,
            None,
            date.today(),
        )
        result = self.engine._score_heat_risk(features, _empty_osha())
        assert any("freezing" in f.lower() or "cold" in f.lower() for f in result.factors)


class TestElectricalScoring:
    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    def test_electrical_work_in_rain(self):
        weather = _make_weather(precip=10)
        activities = _make_activities("Electrical Rough-In")
        features = self.engine._gather_features(
            _make_project(),
            weather,
            activities,
            None,
            date.today(),
        )
        result = self.engine._score_electrical_risk(features, _empty_osha())
        assert result.score >= 40
        assert any("wet" in f.lower() or "shock" in f.lower() for f in result.factors)


# ---------------------------------------------------------------------------
# Full risk score calculation
# ---------------------------------------------------------------------------


class TestDailyRiskScore:
    """Test the full calculate_daily_risk_score pipeline."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    @pytest.mark.asyncio
    async def test_steel_erection_windy_day(self):
        """Steel erection on a windy day should flag fall + crane risk."""
        db = AsyncMock()
        # Mock OSHA query to return empty data
        db.execute = AsyncMock(
            return_value=MagicMock(
                mappings=MagicMock(
                    return_value=MagicMock(
                        all=MagicMock(return_value=[]),
                        first=MagicMock(
                            return_value={"total_inspections": 0, "total_violations": 0}
                        ),
                    )
                )
            )
        )

        weather = _make_weather(wind=35)
        activities = _make_activities("Steel Erection Phase 1", "Crane Operations - Setting Steel")

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=db,
                project_id=str(uuid.uuid4()),
                project=_make_project(),
                weather=weather,
                today_activities=activities,
            )

        assert isinstance(result, DailyRiskResult)
        assert result.overall_score >= 30  # weighted avg across all 5 categories
        assert result.category_scores["fall_risk"] >= 50
        assert result.category_scores["struck_by_risk"] >= 40
        assert len(result.top_risks) >= 2
        assert len(result.recommended_mitigations) >= 2

    @pytest.mark.asyncio
    async def test_new_project_elevated_baseline(self):
        """First week of a new project should note elevated risk."""
        project = _make_project(
            start_date=(date.today() - timedelta(days=3)).isoformat(),
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=project,
                weather=_make_weather(),
                today_activities=_make_activities("Site Mobilization"),
            )

        assert result.project_factors["project_age_months"] < 1.0
        # Should have new-project factor in at least one category
        all_factors = []
        for cat in result.categories:
            all_factors.extend(cat.factors)
        assert any("first month" in f.lower() for f in all_factors)

    @pytest.mark.asyncio
    async def test_benign_conditions_low_score(self):
        """Normal conditions with no risky activities → low overall score."""
        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=_make_project(),
                weather=_make_weather(temp_max=70, wind=5, precip=0),
                today_activities=_make_activities("Interior Drywall"),
            )

        assert result.overall_score <= 30
        assert _score_label(result.overall_score) in ("low", "moderate")

    @pytest.mark.asyncio
    async def test_excavation_heavy_rain(self):
        """Excavation after heavy rain should score high on excavation risk."""
        weather = _make_weather(precip=30)
        activities = _make_activities(
            "Foundation Excavation",
            "Trench for Utilities",
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=_make_project(),
                weather=weather,
                today_activities=activities,
            )

        assert result.category_scores["excavation_risk"] >= 55
        assert any("excavation" in r["category"] for r in result.top_risks)

    @pytest.mark.asyncio
    async def test_hot_roofing_day(self):
        """Hot summer day with roofing → heat illness + fall risk."""
        weather = _make_weather(temp_max=100, humidity=80)
        activities = _make_activities("Roof Sheathing", "Roof Membrane Install")

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=_make_project(),
                weather=weather,
                today_activities=activities,
            )

        assert result.category_scores["heat_illness_risk"] >= 40
        assert result.category_scores["fall_risk"] >= 30


# ---------------------------------------------------------------------------
# Safety briefing generation
# ---------------------------------------------------------------------------


class TestSafetyBriefing:
    """Test briefing generation (template fallback)."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    @pytest.mark.asyncio
    async def test_template_briefing_fallback(self):
        """Should generate template briefing when LLM is unavailable."""
        risk = DailyRiskResult(
            project_id=str(uuid.uuid4()),
            score_date=date.today(),
            overall_score=65,
            category_scores={"fall_risk": 70, "heat_illness_risk": 50},
            categories=[
                RiskCategory(
                    "fall_risk",
                    70,
                    "high",
                    ["High wind speeds", "Roofing scheduled"],
                    ["Verify harnesses"],
                ),
                RiskCategory(
                    "heat_illness_risk",
                    50,
                    "elevated",
                    ["Heat index 105°F"],
                    ["Provide water stations"],
                ),
            ],
            top_risks=[
                {
                    "category": "fall_risk",
                    "score": 70,
                    "label": "high",
                    "factors": ["High wind speeds"],
                },
            ],
            recommended_mitigations=["Verify harnesses", "Provide water stations"],
            weather_factors={},
            schedule_factors={},
            project_factors={},
            osha_factors={},
        )

        with patch.dict("sys.modules", {"app.services.reliability.llm_gateway": None}):
            briefing = await self.engine.generate_safety_briefing(
                risk_result=risk,
                project=_make_project(),
                weather=_make_weather(temp_max=100, humidity=80),
                today_activities=_make_activities("Roofing"),
            )

        assert "SAFETY BRIEFING" in briefing
        assert "Fall Risk" in briefing
        assert "Verify harnesses" in briefing
        assert "near-misses" in briefing.lower()


# ---------------------------------------------------------------------------
# OSHA pattern queries
# ---------------------------------------------------------------------------


class TestOshaPatternQueries:
    """Test OSHA data query handling."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    @pytest.mark.asyncio
    async def test_query_with_results(self):
        """Test OSHA query returns structured data."""
        mock_db = AsyncMock()

        # Mock violation stats query
        violations_result = MagicMock()
        violations_result.mappings.return_value.all.return_value = [
            {
                "standard": "1926.501",
                "count": 150,
                "serious_count": 100,
                "willful_count": 3,
                "avg_gravity": Decimal("7.5"),
            },
            {
                "standard": "1926.451",
                "count": 80,
                "serious_count": 60,
                "willful_count": 1,
                "avg_gravity": Decimal("6.0"),
            },
        ]

        # Mock totals query
        totals_result = MagicMock()
        totals_result.mappings.return_value.first.return_value = {
            "total_inspections": 500,
            "total_violations": 400,
        }

        mock_db.execute = AsyncMock(side_effect=[violations_result, totals_result])

        data = await self.engine._query_osha_patterns(mock_db, "TX", "2362")

        assert data["total_inspections"] == 500
        assert data["total_violations"] == 400
        assert data["violation_rate"] == 0.8
        assert len(data["top_standards"]) == 2
        assert "category_rates" in data

    @pytest.mark.asyncio
    async def test_query_handles_db_error(self):
        """Should return empty dict on database error."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB connection failed"))

        data = await self.engine._query_osha_patterns(mock_db, "TX", "2362")

        assert data["total_inspections"] == 0
        assert data["total_violations"] == 0
        assert data["violation_rate"] == 0.0


# ---------------------------------------------------------------------------
# Store risk score
# ---------------------------------------------------------------------------


class TestStoreRiskScore:
    @pytest.mark.asyncio
    async def test_store_creates_record(self):
        """store_risk_score should add a DailyRiskScore to the session."""
        mock_db = AsyncMock()

        result = DailyRiskResult(
            project_id=str(uuid.uuid4()),
            score_date=date.today(),
            overall_score=42,
            category_scores={"fall_risk": 50},
            categories=[],
            top_risks=[{"category": "fall_risk", "score": 50, "label": "elevated", "factors": []}],
            recommended_mitigations=["Check harnesses"],
            weather_factors={"temp_high": 85},
            schedule_factors={"activity_count": 5},
            project_factors={"state": "TX"},
            osha_factors={"total_inspections": 100},
        )

        await store_risk_score(mock_db, result)
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.overall_score == 42
        assert added.score_date == date.today()


# ---------------------------------------------------------------------------
# Ingestion script tests
# ---------------------------------------------------------------------------


class TestIngestOshaData:
    """Test OSHA CSV parsing functions from the ingestion script."""

    def test_parse_standard(self):
        """Canonical CFR form drops leading zeros from the section
        component — must match osha_lookup.parse_standard exactly,
        otherwise ingested data won't match lookups."""
        from scripts.ingest_osha_data import parse_standard

        assert parse_standard("19260501") == "1926.501"
        assert parse_standard("19100134") == "1910.134"
        assert parse_standard("") is None
        assert parse_standard("123") is None

    def test_normalize_name(self):
        from scripts.ingest_osha_data import normalize_name

        assert normalize_name("ABC CONST., INC.") == "abc const inc"
        assert normalize_name("  Test  Company  ") == "test company"

    def test_is_construction(self):
        from scripts.ingest_osha_data import is_construction

        assert is_construction("2362", None)
        assert is_construction("2361", None)
        assert is_construction(None, "1521")
        assert not is_construction("3111", None)
        assert not is_construction(None, "2011")

    def test_safe_int(self):
        from scripts.ingest_osha_data import _safe_int

        assert _safe_int("42") == 42
        assert _safe_int("") is None
        assert _safe_int("abc") is None

    def test_safe_bool(self):
        from scripts.ingest_osha_data import _safe_bool

        assert _safe_bool("X") is True
        assert _safe_bool("1") is True
        assert _safe_bool("") is False
        assert _safe_bool("0") is False


# ---------------------------------------------------------------------------
# Realistic multi-hazard scenarios
# ---------------------------------------------------------------------------


class TestRealisticScenarios:
    """End-to-end scenario tests with realistic construction conditions."""

    def setup_method(self):
        self.engine = PredictiveRiskEngine()

    @pytest.mark.asyncio
    async def test_scenario_hot_summer_roofing(self):
        """Scenario: July roofing in Houston, TX. 98°F, 80% humidity.

        Expected: High heat illness risk, elevated fall risk.
        """
        project = _make_project(
            address="456 Commerce St, Houston, TX 77002",
            start_date=(date.today() - timedelta(days=120)).isoformat(),
        )
        weather = _make_weather(temp_max=98, temp_min=78, humidity=80, wind=8)
        activities = _make_activities(
            "Roof Membrane Installation",
            "Roof Flashing",
            "Parapet Cap Installation",
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=project,
                weather=weather,
                today_activities=activities,
            )

        # Heat illness should be elevated (heat index > 103)
        assert result.category_scores["heat_illness_risk"] >= 40
        # Fall risk elevated due to roofing
        assert result.category_scores["fall_risk"] >= 30
        # Should have heat-related mitigations (at extreme heat index, mitigations
        # mention halting work or buddy system rather than water/shade)
        assert any(
            "heat" in m.lower()
            or "halt" in m.lower()
            or "buddy" in m.lower()
            or "water" in m.lower()
            or "shade" in m.lower()
            for m in result.recommended_mitigations
        )

    @pytest.mark.asyncio
    async def test_scenario_excavation_after_storm(self):
        """Scenario: Excavation work the day after 2 inches of rain.

        Expected: Critical excavation risk (cave-in), elevated struck-by.
        """
        project = _make_project(
            address="789 Industrial Pkwy, Columbus, OH 43215",
        )
        weather = _make_weather(temp_max=65, precip=50, wind=10)  # 50mm = ~2 inches
        activities = _make_activities(
            "Foundation Excavation - Building A",
            "Trench Excavation for Storm Sewer",
            "Grading - Parking Lot",
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=project,
                weather=weather,
                today_activities=activities,
            )

        assert result.category_scores["excavation_risk"] >= 60
        assert any(
            "cave-in" in f.lower() or "saturated" in f.lower()
            for r in result.top_risks
            for f in r.get("factors", [])
        )
        assert any("re-inspect" in m.lower() for m in result.recommended_mitigations)

    @pytest.mark.asyncio
    async def test_scenario_steel_erection_windy(self):
        """Scenario: Steel erection day with 35 mph wind gusts.

        Expected: Critical fall risk, high struck-by risk (crane suspension).
        """
        project = _make_project(
            address="100 Tower Dr, Chicago, IL 60601",
        )
        weather = _make_weather(temp_max=55, wind=35)
        activities = _make_activities(
            "Steel Erection - Level 5",
            "Crane Operations - Steel Setting",
            "Rigging and Hoisting - Steel Beams",
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=project,
                weather=weather,
                today_activities=activities,
            )

        assert result.category_scores["fall_risk"] >= 55
        assert result.category_scores["struck_by_risk"] >= 45
        assert result.overall_score >= 30  # weighted avg across all 5 categories
        # Should recommend suspending operations
        assert any("suspend" in m.lower() for m in result.recommended_mitigations)

    @pytest.mark.asyncio
    async def test_scenario_first_week_new_project(self):
        """Scenario: First week of a new commercial project.

        Expected: Elevated baseline risk across categories due to new workers.
        """
        project = _make_project(
            start_date=(date.today() - timedelta(days=3)).isoformat(),
        )
        weather = _make_weather()
        activities = _make_activities(
            "Site Mobilization",
            "Temporary Fencing Installation",
            "Construction Entrance Grading",
        )

        with patch.object(self.engine, "_query_osha_patterns", return_value=_empty_osha()):
            result = await self.engine.calculate_daily_risk_score(
                db=AsyncMock(),
                project_id=str(uuid.uuid4()),
                project=project,
                weather=weather,
                today_activities=activities,
            )

        assert result.project_factors["project_age_months"] < 1.0
        # Should have orientation-related factor
        all_factors = [f for cat in result.categories for f in cat.factors]
        assert any("first month" in f.lower() or "orientation" in f.lower() for f in all_factors)
