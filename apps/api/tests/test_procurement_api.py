"""Phase 2: Procurement API endpoint tests.

Tests for the procurement REST API endpoints including price forecasting,
contract risk analysis, and vendor scoring. LLM calls and service functions
are mocked to avoid external dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio

from app.models.project import Project
from tests.fixtures.precon_mock_responses import MOCK_LLM_CONTRACT_RISK_RESPONSE


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for procurement API tests."""
    project = Project(name="Procurement Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestProcurementApi:
    """Tests for the procurement API endpoints."""

    @patch("app.services.procurement.price_forecaster.forecast_prices")
    async def test_forecast_prices(self, mock_forecast, client, auth_headers):
        """POST /api/v1/procurement/price-forecast should return forecasts."""
        mock_forecast.return_value = {
            "forecasts": [],
            "model_used": "linear_trend",
            "rmse": 2.5,
            "trend": "rising",
            "summary": "Prices are rising.",
        }

        response = await client.post(
            "/api/v1/procurement/price-forecast",
            json={
                "material_category": "concrete",
                "series_id": "PCU236211236211",
                "horizon_months": 3,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "model_used" in data
        assert data["trend"] == "rising"

    async def test_contract_risk(self, client, auth_headers):
        """POST /api/v1/procurement/contract-risk should return risk analysis."""
        # The contract-risk service routes through the LLM gateway first,
        # only falling back to ChatOpenAI on ImportError. Patching the
        # gateway factory captures the real call path; without it, all
        # gateway models fail to authenticate in CI and the service
        # returns ``overall_risk_score=None`` which fails the response
        # model (float).
        gateway = AsyncMock()
        gateway.complete = AsyncMock(return_value={"content": MOCK_LLM_CONTRACT_RISK_RESPONSE})
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            response = await client.post(
                "/api/v1/procurement/contract-risk",
                json={
                    "contract_text": "Sample contract with LD clause at $5000/day",
                    "project_type": "commercial",
                },
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert "overall_risk_score" in data
        assert "risk_items" in data

    async def test_contract_risk_empty_text(self, client, auth_headers):
        """POST with empty contract text should return 422."""
        response = await client.post(
            "/api/v1/procurement/contract-risk",
            json={
                "contract_text": "",
                "project_type": "commercial",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    @patch("app.services.procurement.vendor_manager.score_vendor")
    async def test_vendor_score(self, mock_score, client, auth_headers):
        """POST /api/v1/procurement/vendor-score should return vendor scores."""
        mock_score.return_value = {
            "vendor_id": "v1",
            "overall_score": 85.5,
            "criteria_scores": {
                "on_time_delivery": {"score": 92.0, "weighted_score": 18.4, "weight": 0.2},
                "quality": {"score": 84.0, "weighted_score": 16.8, "weight": 0.2},
            },
            "recommendation": "recommended",
            "risk_flags": [],
        }

        response = await client.post(
            "/api/v1/procurement/vendor-score",
            json={
                "vendor_id": "v1",
                "criteria": {},
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "overall_score" in data
        assert "vendor_id" in data

    async def test_forecast_prices_missing_fields(self, client, auth_headers):
        """POST with missing required fields should return 422."""
        response = await client.post(
            "/api/v1/procurement/price-forecast",
            json={"material_category": "concrete"},
            headers=auth_headers,
        )
        assert response.status_code == 422
