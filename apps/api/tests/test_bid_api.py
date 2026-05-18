"""Tests for Bid/No-Bid API endpoints and CSV parser."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.estimating.bid_history_parser import (
    _decode_content,
    _map_headers,
    _parse_date,
    _parse_value,
    parse_bid_history_csv,
)

# ---------------------------------------------------------------------------
# CSV Parser Tests
# ---------------------------------------------------------------------------


class TestDecodeContent:
    def test_utf8(self):
        assert _decode_content(b"hello") == "hello"

    def test_utf8_bom(self):
        assert _decode_content(b"\xef\xbb\xbfhello") == "hello"

    def test_latin1_fallback(self):
        result = _decode_content(b"caf\xe9")
        assert "caf" in result


class TestMapHeaders:
    def test_standard_headers(self):
        headers = [
            "Name",
            "Project Type",
            "Delivery Method",
            "Estimated Value",
            "Location",
            "Outcome",
        ]
        mapping = _map_headers(headers)
        assert "name" in mapping
        assert "project_type" in mapping
        assert "delivery_method" in mapping
        assert "estimated_value" in mapping
        assert "location" in mapping
        assert "outcome" in mapping

    def test_alternative_headers(self):
        headers = ["project_name", "type", "method", "value", "city", "result"]
        mapping = _map_headers(headers)
        assert "name" in mapping
        assert "project_type" in mapping

    def test_case_insensitive(self):
        headers = ["NAME", "Project_Type", "OUTCOME"]
        mapping = _map_headers(headers)
        assert "name" in mapping
        assert "project_type" in mapping
        assert "outcome" in mapping


class TestParseValue:
    def test_plain_number(self):
        assert _parse_value("15000000") == 15000000.0

    def test_with_dollar_sign(self):
        assert _parse_value("$15,000,000") == 15000000.0

    def test_empty(self):
        assert _parse_value("") is None

    def test_invalid(self):
        assert _parse_value("not a number") is None


class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2026-01-15") == "2026-01-15"

    def test_us_format(self):
        assert _parse_date("01/15/2026") == "2026-01-15"

    def test_empty(self):
        assert _parse_date("") is None

    def test_invalid(self):
        assert _parse_date("not a date") is None


class TestParseCSV:
    @pytest.mark.asyncio
    async def test_valid_csv(self):
        csv_content = b"name,project_type,delivery_method,estimated_value,location,outcome\nOffice Tower,commercial,negotiated,$15000000,Austin TX,won\nWarehouse,industrial,hard_bid,$5000000,Dallas TX,lost\n"
        result = await parse_bid_history_csv(csv_content, "test-org-id")
        assert len(result.opportunities) == 2
        assert result.row_count == 2

        # Check first record
        first = result.opportunities[0]
        assert first["opportunity"]["name"] == "Office Tower"
        assert first["opportunity"]["project_type"] == "commercial"
        assert first["opportunity"]["delivery_method"] == "negotiated"
        assert first["opportunity"]["estimated_value"] == 15000000.0
        assert first["opportunity"]["outcome"] == "won"
        assert first["decision"]["human_decision"] == "pursue"

    @pytest.mark.asyncio
    async def test_missing_name_column(self):
        csv_content = b"type,outcome\ncommercial,won\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert len(result.errors) > 0
        assert result.errors[0].field == "name"

    @pytest.mark.asyncio
    async def test_blank_rows_skipped(self):
        csv_content = b"name,outcome\nProject A,won\n\n\nProject B,lost\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert len(result.opportunities) == 2

    @pytest.mark.asyncio
    async def test_semicolon_delimiter(self):
        csv_content = b"name;project_type;outcome\nProject A;commercial;won\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert len(result.opportunities) == 1
        assert result.opportunities[0]["opportunity"]["name"] == "Project A"

    @pytest.mark.asyncio
    async def test_invalid_outcome(self):
        csv_content = b"name,outcome\nProject A,maybe\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert len(result.opportunities) == 1
        assert any(e.field == "outcome" for e in result.errors)

    @pytest.mark.asyncio
    async def test_invalid_delivery_method(self):
        csv_content = b"name,delivery_method,outcome\nProject A,unknown_method,won\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert any(e.field == "delivery_method" for e in result.errors)
        # Parser stores "" (empty string) when the value can't be normalized,
        # not None — both convey "unset" but the model uses the string default.
        assert result.opportunities[0]["opportunity"]["delivery_method"] == ""

    @pytest.mark.asyncio
    async def test_empty_file(self):
        csv_content = b"name,outcome\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert len(result.opportunities) == 0

    @pytest.mark.asyncio
    async def test_no_outcome_column(self):
        csv_content = b"name,project_type\nProject A,commercial\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert any("outcome" in w.lower() for w in result.warnings)
        assert len(result.opportunities) == 1

    @pytest.mark.asyncio
    async def test_competitors_parsed(self):
        csv_content = b"name,outcome,competitors\nProject A,won,5\n"
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert result.opportunities[0]["opportunity"]["metadata_json"]["competitors"] == 5

    @pytest.mark.asyncio
    async def test_monetary_value_parsing(self):
        # Values with commas must be quoted in comma-delimited CSV
        csv_content = b'name,estimated_value,outcome\nProject A,"$1,500,000",won\n'
        result = await parse_bid_history_csv(csv_content, "test-org")
        assert result.opportunities[0]["opportunity"]["estimated_value"] == 1500000.0


# ---------------------------------------------------------------------------
# LangGraph Agent Tests (mocked)
# ---------------------------------------------------------------------------

BID_ENGINE_TARGET = "app.services.estimating.bid_decision_engine.BidDecisionEngine"
CHECKPOINTER_TARGET = "app.services.agents.checkpointer.get_checkpointer"
LLM_TARGET = "app.services.reliability.llm_gateway.LLMGateway"


class TestBidDecisionAgent:
    @pytest.mark.asyncio
    async def test_build_graph(self):
        """Graph should compile without errors."""
        from app.services.agents.bid_decision_agent import build_bid_decision_agent

        agent = build_bid_decision_agent()
        assert agent is not None

    @pytest.mark.asyncio
    async def test_score_bid_opportunity_end_to_end(self):
        """Full agent run with mocked dependencies."""
        from app.services.agents.bid_decision_agent import score_bid_opportunity

        mock_engine_result = {
            "composite_score": 72,
            "recommendation": "pursue",
            "win_probability": 0.35,
            "factor_scores": {
                "historical_win_rate": {
                    "score": 65,
                    "weight": 0.15,
                    "weighted_score": 9.75,
                    "reasoning": "test",
                }
            },
            "status": "scored",
        }

        with (
            patch(CHECKPOINTER_TARGET) as mock_cp,
            patch(f"{BID_ENGINE_TARGET}.score_opportunity", return_value=mock_engine_result),
            patch(LLM_TARGET) as mock_llm_cls,
        ):
            from langgraph.checkpoint.memory import MemorySaver

            mock_cp.return_value = MemorySaver()

            mock_gateway = AsyncMock()
            mock_gateway.complete = AsyncMock(
                return_value={"content": "This is a good opportunity."}
            )
            mock_llm_cls.return_value = mock_gateway

            # Mock the DB context loading
            with patch("app.services.agents.bid_decision_agent.load_context_node") as mock_load:
                mock_load.return_value = {
                    "org_context": {"bid_count": 10, "win_count": 3},
                    "errors": [],
                    "status": "context_loaded",
                }

                result = await score_bid_opportunity(
                    opportunity_id=str(uuid.uuid4()),
                    opportunity={
                        "name": "Test Project",
                        "project_type": "commercial",
                        "delivery_method": "negotiated",
                        "estimated_value": 10_000_000,
                    },
                    org_id=str(uuid.uuid4()),
                )

                assert result["composite_score"] == 72
                assert result["recommendation"] == "pursue"

    @pytest.mark.asyncio
    async def test_state_keys_not_node_names(self):
        """Verify node names don't collide with state keys (LangGraph bug prevention)."""
        from app.services.agents.bid_decision_agent import (
            BidDecisionState,
            build_bid_decision_agent,
        )

        set(BidDecisionState.__annotations__.keys())
        agent = build_bid_decision_agent()
        # The graph should compile without ValueError
        assert agent is not None
