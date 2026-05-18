"""Phase 2: Contract risk analysis tests.

Tests for the LLM-based contract risk scoring and comparison services.
All LLM calls are mocked so that no real API requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.procurement.contract_risk import (
    compare_contract_terms,
    score_contract_risk,
)
from tests.fixtures.precon_mock_responses import (
    MOCK_LLM_CONTRACT_COMPARISON_RESPONSE,
    MOCK_LLM_CONTRACT_RISK_RESPONSE,
)

# `_llm_invoke` calls the LLM gateway; patching `get_llm_gateway` returns a
# stub gateway whose `complete()` yields our mock response. Patching
# `ChatOpenAI` directly does nothing because the gateway path is taken
# first — the ChatOpenAI fallback is only used on ImportError.


def _stub_gateway(content: str):
    gateway = AsyncMock()
    gateway.complete = AsyncMock(return_value={"content": content})
    return gateway


class TestContractRisk:
    """Tests for the contract risk analysis service."""

    async def test_score_contract_risk(self):
        """Should return risk score and risk items from LLM analysis."""
        gateway = _stub_gateway(MOCK_LLM_CONTRACT_RISK_RESPONSE)
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            result = await score_contract_risk("Contract text with LD clause...", "commercial")
        assert "overall_risk_score" in result
        assert "risk_items" in result
        assert len(result["risk_items"]) > 0
        assert result["overall_risk_score"] == 65

    async def test_contract_risk_severity_levels(self):
        """Each risk item should have a valid severity level."""
        gateway = _stub_gateway(MOCK_LLM_CONTRACT_RISK_RESPONSE)
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            result = await score_contract_risk("Contract text...", "commercial")
        for item in result["risk_items"]:
            assert item["severity"] in ("high", "medium", "low", "critical")

    async def test_contract_risk_recommendations(self):
        """Result should include strategic recommendations."""
        gateway = _stub_gateway(MOCK_LLM_CONTRACT_RISK_RESPONSE)
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            result = await score_contract_risk("Contract text...", "commercial")
        assert "recommendations" in result
        assert len(result["recommendations"]) > 0

    async def test_compare_contracts(self):
        """Should return comparison and recommendation for two contracts."""
        gateway = _stub_gateway(MOCK_LLM_CONTRACT_COMPARISON_RESPONSE)
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            result = await compare_contract_terms("Contract A text", "Contract B text")
        assert "comparison" in result
        assert "recommendation" in result
        assert len(result["comparison"]) > 0

    async def test_score_empty_contract(self):
        """Empty contract text should return zero score."""
        result = await score_contract_risk("", "commercial")
        assert result["overall_risk_score"] == 0.0
        assert result["risk_items"] == []
        assert result["model_used"] == "none"

    async def test_compare_empty_contracts(self):
        """Empty contract texts should return no comparison."""
        result = await compare_contract_terms("", "Contract B")
        assert result["comparison"] == []
        assert result["model_used"] == "none"
