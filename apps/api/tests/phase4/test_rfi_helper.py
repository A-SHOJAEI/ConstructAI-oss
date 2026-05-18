"""Tests for RFI response suggestion service."""

from __future__ import annotations

from app.services.communication.rfi_helper import (
    suggest_rfi_response,
)


class TestRFIHelper:
    async def test_basic_suggestion(self):
        result = await suggest_rfi_response(
            subject="Column reinforcement detail",
            question="What size rebar for column B3?",
        )
        assert "suggested_response" in result
        assert "references" in result
        assert "confidence" in result
        assert result["confidence"] > 0

    async def test_suggestion_with_context(self):
        result = await suggest_rfi_response(
            subject="Steel connection",
            question=("Clarify connection type at grid C4"),
            project_context={
                "specifications": ["03300", "05120"],
                "drawings": ["S-301", "S-302"],
            },
        )
        assert len(result["references"]) > 0
        assert result["confidence"] > 0.5

    async def test_suggestion_without_context(self):
        result = await suggest_rfi_response(
            subject="General question",
            question=("What is the floor load capacity?"),
        )
        assert result["confidence"] <= 0.5
        assert len(result["references"]) == 0

    async def test_response_mentions_subject(self):
        result = await suggest_rfi_response(
            subject="Waterproofing detail",
            question="Which membrane system?",
        )
        assert "Waterproofing detail" in (result["suggested_response"])
