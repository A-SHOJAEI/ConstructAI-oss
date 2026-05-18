"""Tests for BidDecisionEngine — pure computational scoring.

No database, no LLM, no I/O required. All tests use mock data.
"""

from __future__ import annotations

from app.services.estimating.bid_decision_engine import (
    DEFAULT_FACTOR_WEIGHTS,
    INDUSTRY_WIN_RATES,
    THRESHOLD_CONDITIONAL,
    THRESHOLD_PURSUE,
    THRESHOLD_STRONG_PURSUE,
    BidDecisionEngine,
    _blend_factor,
    _clamp,
    _haversine_distance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_OPPORTUNITY = {
    "name": "Downtown Office Tower",
    "project_type": "commercial",
    "delivery_method": "negotiated",
    "estimated_value": 15_000_000,
    "location": "Austin, TX",
    "latitude": 30.267,
    "longitude": -97.743,
    "owner_name": "Acme Development",
    "competitors": 4,
}

MOCK_ORG_CONTEXT = {
    "bid_count": 50,
    "win_count": 12,
    "bids_by_type": {
        "commercial": {"total": 30, "won": 8},
        "industrial": {"total": 15, "won": 4},
    },
    "bids_by_method": {
        "negotiated": {"total": 20, "won": 7},
        "hard_bid": {"total": 25, "won": 3},
    },
    "previous_owners": {
        "Acme Development": {"projects": 3, "satisfaction": 0.9},
    },
    "office_latitude": 30.267,
    "office_longitude": -97.743,
    "current_backlog": 40_000_000,
    "max_capacity": 100_000_000,
    "avg_project_size": 12_000_000,
    "min_project_size": 2_000_000,
    "max_project_size": 50_000_000,
    "estimating_capacity": 0.6,
    "bonding_capacity": 200_000_000,
    "bonding_used": 100_000_000,
    "strategic_sectors": ["commercial", "healthcare"],
}


# ---------------------------------------------------------------------------
# TestBlendFactor
# ---------------------------------------------------------------------------


class TestBlendFactor:
    def test_cold_start_zero_bids(self):
        """With 0 bids, should return pure industry value."""
        result = _blend_factor(org_value=0.30, industry_value=0.12, bid_count=0)
        assert result == 0.12

    def test_cold_start_under_min(self):
        """With < min_bids, should return pure industry value."""
        result = _blend_factor(org_value=0.30, industry_value=0.12, bid_count=3)
        assert result == 0.12

    def test_warm_full_bids(self):
        """With >= full_bids, should return pure org value."""
        result = _blend_factor(org_value=0.30, industry_value=0.12, bid_count=20)
        assert result == 0.30

    def test_warm_above_full(self):
        """With > full_bids, should still return pure org value."""
        result = _blend_factor(org_value=0.30, industry_value=0.12, bid_count=100)
        assert result == 0.30

    def test_partial_midpoint(self):
        """With bids at midpoint (12.5), should be ~50/50 blend."""
        # min=5, full=20, so 12 bids → ratio = 7/15 ≈ 0.467
        result = _blend_factor(org_value=0.30, industry_value=0.10, bid_count=12)
        ratio = (12 - 5) / (20 - 5)
        expected = 0.10 * (1 - ratio) + 0.30 * ratio
        assert abs(result - expected) < 0.001

    def test_at_min_boundary(self):
        """At exactly min_bids, should start blending (ratio=0 → industry)."""
        result = _blend_factor(org_value=0.30, industry_value=0.12, bid_count=5)
        assert result == 0.12

    def test_custom_bounds(self):
        """Custom min_bids and full_bids."""
        result = _blend_factor(
            org_value=0.50, industry_value=0.10, bid_count=15, min_bids=10, full_bids=30
        )
        ratio = (15 - 10) / (30 - 10)
        expected = 0.10 * (1 - ratio) + 0.50 * ratio
        assert abs(result - expected) < 0.001


# ---------------------------------------------------------------------------
# TestHaversine
# ---------------------------------------------------------------------------


class TestHaversine:
    def test_same_point(self):
        """Same point should be 0 km."""
        assert _haversine_distance(30.0, -97.0, 30.0, -97.0) == 0.0

    def test_known_distance(self):
        """Austin to Dallas is ~300 km."""
        d = _haversine_distance(30.267, -97.743, 32.776, -96.797)
        assert 270 < d < 310

    def test_intercontinental(self):
        """NYC to London is ~5500 km."""
        d = _haversine_distance(40.7128, -74.0060, 51.5074, -0.1278)
        assert 5500 < d < 5600


# ---------------------------------------------------------------------------
# TestClamp
# ---------------------------------------------------------------------------


class TestClamp:
    def test_within_range(self):
        assert _clamp(50.0) == 50.0

    def test_below_min(self):
        assert _clamp(-10.0) == 0.0

    def test_above_max(self):
        assert _clamp(150.0) == 100.0


# ---------------------------------------------------------------------------
# TestScoringFactors
# ---------------------------------------------------------------------------


class TestHistoricalWinRate:
    def test_high_win_rate(self):
        engine = BidDecisionEngine()
        ctx = dict(MOCK_ORG_CONTEXT)
        ctx["bids_by_type"]["commercial"]["won"] = 25  # high win rate
        fs = engine._score_historical_win_rate(MOCK_OPPORTUNITY, ctx)
        assert fs.score > 60

    def test_zero_bids(self):
        engine = BidDecisionEngine()
        ctx = {"bid_count": 0, "win_count": 0, "bids_by_type": {}}
        fs = engine._score_historical_win_rate(MOCK_OPPORTUNITY, ctx)
        # Should use industry rate (negotiated=25% → score ~78)
        assert 20 <= fs.score <= 100


class TestOwnerRelationship:
    def test_known_owner(self):
        engine = BidDecisionEngine()
        fs = engine._score_owner_relationship(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score > 60  # Acme has 3 projects, 0.9 satisfaction

    def test_unknown_owner(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, owner_name="Unknown Corp")
        fs = engine._score_owner_relationship(opp, MOCK_ORG_CONTEXT)
        assert fs.score == 30.0

    def test_no_owner(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, owner_name="")
        fs = engine._score_owner_relationship(opp, MOCK_ORG_CONTEXT)
        assert fs.score == 50.0


class TestBacklogCapacity:
    def test_low_backlog(self):
        engine = BidDecisionEngine()
        ctx = dict(MOCK_ORG_CONTEXT, current_backlog=20_000_000)
        fs = engine._score_backlog_capacity(MOCK_OPPORTUNITY, ctx)
        assert fs.score >= 80  # < 50% utilization

    def test_over_capacity(self):
        engine = BidDecisionEngine()
        ctx = dict(MOCK_ORG_CONTEXT, current_backlog=90_000_000)
        fs = engine._score_backlog_capacity(MOCK_OPPORTUNITY, ctx)
        assert fs.score < 40  # would exceed capacity

    def test_no_capacity_data(self):
        engine = BidDecisionEngine()
        ctx = {"max_capacity": 0}
        fs = engine._score_backlog_capacity(MOCK_OPPORTUNITY, ctx)
        assert fs.score == 50.0


class TestGeographicFamiliarity:
    def test_same_city(self):
        engine = BidDecisionEngine()
        fs = engine._score_geographic_familiarity(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score >= 90  # 0 km

    def test_far_away(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, latitude=47.6, longitude=-122.3)  # Seattle
        fs = engine._score_geographic_familiarity(opp, MOCK_ORG_CONTEXT)
        assert fs.score < 50

    def test_no_coords(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, latitude=None, longitude=None)
        ctx = dict(MOCK_ORG_CONTEXT, familiar_locations=["Austin"])
        fs = engine._score_geographic_familiarity(opp, ctx)
        assert fs.score == 75.0  # familiar location match


class TestProjectSizeFit:
    def test_within_range(self):
        engine = BidDecisionEngine()
        fs = engine._score_project_size_fit(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score > 60  # $15M vs avg $12M

    def test_too_small(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, estimated_value=500_000)
        fs = engine._score_project_size_fit(opp, MOCK_ORG_CONTEXT)
        assert fs.score < 50

    def test_no_history(self):
        engine = BidDecisionEngine()
        ctx = {"avg_project_size": 0}
        fs = engine._score_project_size_fit(MOCK_OPPORTUNITY, ctx)
        assert fs.score == 50.0


class TestCompetitionLevel:
    def test_low_competition(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, competitors=2)
        fs = engine._score_competition_level(opp, {})
        assert fs.score == 90.0

    def test_high_competition(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, competitors=10)
        fs = engine._score_competition_level(opp, {})
        assert fs.score < 30

    def test_unknown_competition(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY)
        del opp["competitors"]
        fs = engine._score_competition_level(opp, {})
        assert fs.score == 50.0


class TestEstimatingAvailability:
    def test_high_capacity(self):
        engine = BidDecisionEngine()
        ctx = {"estimating_capacity": 0.8}
        fs = engine._score_estimating_availability(MOCK_OPPORTUNITY, ctx)
        assert fs.score >= 85

    def test_low_capacity(self):
        engine = BidDecisionEngine()
        ctx = {"estimating_capacity": 0.1}
        fs = engine._score_estimating_availability(MOCK_OPPORTUNITY, ctx)
        assert fs.score < 40


class TestBondingImpact:
    def test_healthy_bonding(self):
        engine = BidDecisionEngine()
        fs = engine._score_bonding_impact(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score > 60

    def test_over_bonding(self):
        engine = BidDecisionEngine()
        ctx = dict(MOCK_ORG_CONTEXT, bonding_used=195_000_000)
        fs = engine._score_bonding_impact(MOCK_OPPORTUNITY, ctx)
        assert fs.score < 20


class TestStrategicAlignment:
    def test_strategic_match(self):
        engine = BidDecisionEngine()
        fs = engine._score_strategic_alignment(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score == 90.0  # commercial is strategic

    def test_non_strategic(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, project_type="residential")
        fs = engine._score_strategic_alignment(opp, MOCK_ORG_CONTEXT)
        assert fs.score == 40.0


class TestRiskProfile:
    def test_low_risk(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, delivery_method="negotiated")
        fs = engine._score_risk_profile(opp, MOCK_ORG_CONTEXT)
        assert fs.score >= 80

    def test_high_risk(self):
        engine = BidDecisionEngine()
        opp = dict(
            MOCK_OPPORTUNITY,
            delivery_method="hard_bid",
            estimated_value=100_000_000,
            bid_due_date="2026-03-10",
        )
        fs = engine._score_risk_profile(opp, MOCK_ORG_CONTEXT)
        assert fs.score < 60


class TestMarginPotential:
    def test_negotiated_margin(self):
        engine = BidDecisionEngine()
        fs = engine._score_margin_potential(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert fs.score > 50  # negotiated has ~6% margin

    def test_hard_bid_margin(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, delivery_method="hard_bid")
        fs = engine._score_margin_potential(opp, MOCK_ORG_CONTEXT)
        assert fs.score < 60  # hard bid has ~3% margin


# ---------------------------------------------------------------------------
# TestCompositeScore
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_full_scoring(self):
        """Full scoring with mock data should produce a score 0-100."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert 0 <= result["composite_score"] <= 100

    def test_all_factors_present(self):
        """All 12 factors should appear in results."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert len(result["factor_scores"]) == 12

    def test_weights_sum_to_one(self):
        """Default weights should sum to 1.0."""
        total = sum(DEFAULT_FACTOR_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_custom_weights(self):
        """Custom weights should be applied and renormalized."""
        custom = {"historical_win_rate": 0.50}
        engine = BidDecisionEngine(custom_weights=custom)
        total = sum(engine.weights.values())
        assert abs(total - 1.0) < 0.001
        assert engine.weights["historical_win_rate"] > DEFAULT_FACTOR_WEIGHTS["historical_win_rate"]


# ---------------------------------------------------------------------------
# TestRecommendation
# ---------------------------------------------------------------------------


class TestRecommendation:
    def test_strong_pursue(self):
        engine = BidDecisionEngine()
        assert engine._generate_recommendation(75) == "strong_pursue"
        assert engine._generate_recommendation(100) == "strong_pursue"

    def test_pursue(self):
        engine = BidDecisionEngine()
        assert engine._generate_recommendation(55) == "pursue"
        assert engine._generate_recommendation(74) == "pursue"

    def test_conditional(self):
        engine = BidDecisionEngine()
        assert engine._generate_recommendation(40) == "conditional"
        assert engine._generate_recommendation(54) == "conditional"

    def test_decline(self):
        engine = BidDecisionEngine()
        assert engine._generate_recommendation(39) == "decline"
        assert engine._generate_recommendation(0) == "decline"

    def test_threshold_boundaries(self):
        engine = BidDecisionEngine()
        assert engine._generate_recommendation(THRESHOLD_STRONG_PURSUE) == "strong_pursue"
        assert engine._generate_recommendation(THRESHOLD_STRONG_PURSUE - 1) == "pursue"
        assert engine._generate_recommendation(THRESHOLD_PURSUE) == "pursue"
        assert engine._generate_recommendation(THRESHOLD_PURSUE - 1) == "conditional"
        assert engine._generate_recommendation(THRESHOLD_CONDITIONAL) == "conditional"
        assert engine._generate_recommendation(THRESHOLD_CONDITIONAL - 1) == "decline"


# ---------------------------------------------------------------------------
# TestWinProbability
# ---------------------------------------------------------------------------


class TestWinProbability:
    def test_score_50_equals_base_rate(self):
        """Score of 50 should produce industry base rate."""
        engine = BidDecisionEngine()
        prob = engine._calibrate_win_probability(50, "negotiated")
        assert abs(prob - INDUSTRY_WIN_RATES["negotiated"]) < 0.01

    def test_score_100_capped(self):
        """Score of 100 should not exceed 0.95."""
        engine = BidDecisionEngine()
        prob = engine._calibrate_win_probability(100, "negotiated")
        assert prob <= 0.95

    def test_score_0_low(self):
        """Score of 0 should be very low probability."""
        engine = BidDecisionEngine()
        prob = engine._calibrate_win_probability(0, "hard_bid")
        assert prob < 0.05

    def test_delivery_method_affects_probability(self):
        """Different delivery methods should give different probabilities."""
        engine = BidDecisionEngine()
        prob_hard = engine._calibrate_win_probability(60, "hard_bid")
        prob_neg = engine._calibrate_win_probability(60, "negotiated")
        assert prob_neg > prob_hard

    def test_probability_bounds(self):
        """Probability should be between 0.01 and 0.95."""
        engine = BidDecisionEngine()
        for score in range(0, 101, 10):
            for method in INDUSTRY_WIN_RATES:
                prob = engine._calibrate_win_probability(score, method)
                assert 0.01 <= prob <= 0.95


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_org_context(self):
        """Empty org context should use defaults/industry values."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity(MOCK_OPPORTUNITY, {})
        assert 0 <= result["composite_score"] <= 100
        assert result["recommendation"] in {"strong_pursue", "pursue", "conditional", "decline"}

    def test_minimal_opportunity(self):
        """Minimal opportunity with just a name should still score."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity({"name": "Test"}, {})
        assert 0 <= result["composite_score"] <= 100

    def test_zero_estimated_value(self):
        engine = BidDecisionEngine()
        opp = dict(MOCK_OPPORTUNITY, estimated_value=0)
        result = engine.score_opportunity(opp, MOCK_ORG_CONTEXT)
        assert 0 <= result["composite_score"] <= 100

    def test_result_structure(self):
        """Verify result dict has all expected keys."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        assert "composite_score" in result
        assert "recommendation" in result
        assert "win_probability" in result
        assert "factor_scores" in result
        assert "status" in result
        assert result["status"] == "scored"

    def test_factor_score_structure(self):
        """Each factor score should have score, weight, weighted_score, reasoning."""
        engine = BidDecisionEngine()
        result = engine.score_opportunity(MOCK_OPPORTUNITY, MOCK_ORG_CONTEXT)
        for _name, fs in result["factor_scores"].items():
            assert "score" in fs
            assert "weight" in fs
            assert "weighted_score" in fs
            assert "reasoning" in fs
