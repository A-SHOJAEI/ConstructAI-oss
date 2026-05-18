"""Phase 2: Vendor management and scoring tests.

Tests for the vendor evaluation and bid comparison service.
No external API calls; all scoring is done locally.
"""

from __future__ import annotations

from app.services.procurement.vendor_manager import evaluate_bid, score_vendor


class TestVendorManager:
    """Tests for the vendor scoring service."""

    async def test_score_vendor_returns_overall_score(self):
        """Should compute an overall score between 0 and 100."""
        vendor_data = {
            "name": "Test Vendor",
            "vendor_id": "v1",
            "past_projects": 50,
            "on_time_delivery_pct": 92.0,
            "quality_rating": 4.2,
            "safety_record": 0.85,
            "financial_stability": "strong",
            "references": 10,
            "bonding_capacity": 5000000,
            "price_competitiveness": 0.8,
        }
        result = await score_vendor(vendor_data)
        assert "overall_score" in result
        assert 0 <= result["overall_score"] <= 100

    async def test_score_vendor_recommendation(self):
        """Should provide a recommendation category."""
        vendor_data = {
            "vendor_id": "v1",
            "on_time_delivery_pct": 92.0,
            "quality_rating": 4.2,
            "safety_record": 0.85,
            "financial_stability": "strong",
            "past_projects": 50,
            "references": 10,
            "price_competitiveness": 0.8,
        }
        result = await score_vendor(vendor_data)
        assert result["recommendation"] in (
            "highly_recommended",
            "recommended",
            "conditional",
            "not_recommended",
        )

    async def test_score_vendor_risk_flags(self):
        """Poor vendor metrics should produce risk flags."""
        weak_vendor = {
            "vendor_id": "v2",
            "on_time_delivery_pct": 60.0,
            "quality_rating": 2.0,
            "safety_record": 1.5,
            "financial_stability": "weak",
            "past_projects": 1,
            "references": 0,
            "price_competitiveness": 0.3,
        }
        result = await score_vendor(weak_vendor)
        assert len(result["risk_flags"]) > 0

    async def test_score_vendor_criteria_scores(self):
        """Should return individual criteria scores with weights."""
        vendor_data = {
            "vendor_id": "v1",
            "on_time_delivery_pct": 90.0,
            "quality_rating": 4.0,
            "safety_record": 0.9,
            "financial_stability": "moderate",
            "past_projects": 20,
            "references": 5,
            "price_competitiveness": 0.7,
        }
        result = await score_vendor(vendor_data)
        assert "criteria_scores" in result
        assert "on_time_delivery" in result["criteria_scores"]
        assert "quality" in result["criteria_scores"]

    async def test_evaluate_bids(self):
        """Should rank bids by total weighted score."""
        bids = [
            {
                "vendor_id": "v1",
                "vendor_name": "Vendor A",
                "bid_amount": 1000000,
                "schedule_days": 120,
                "qualifications": {
                    "past_projects": 20,
                    "quality_rating": 4.0,
                },
            },
            {
                "vendor_id": "v2",
                "vendor_name": "Vendor B",
                "bid_amount": 900000,
                "schedule_days": 150,
                "qualifications": {
                    "past_projects": 10,
                    "quality_rating": 3.5,
                },
            },
        ]
        result = await evaluate_bid(bids)
        assert "ranked_bids" in result
        assert len(result["ranked_bids"]) == 2
        assert result["ranked_bids"][0]["rank"] == 1
