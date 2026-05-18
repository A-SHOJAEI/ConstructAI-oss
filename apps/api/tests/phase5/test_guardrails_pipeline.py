"""Tests for the six-stage guardrails pipeline."""

from __future__ import annotations

import json

from app.services.guardrails.pipeline import (
    build_guardrails_pipeline,
    run_guardrails,
)


class TestGuardrailsPipeline:
    async def test_valid_json_passes(self):
        raw = json.dumps(
            {
                "unit_cost": 250.0,
                "csi_code": "03 30 00",
                "total_cost": 50000.0,
            }
        )
        result = await run_guardrails(raw, "cost_estimate")
        assert result["parsed_output"] is not None
        assert result["routing_decision"] is not None

    async def test_empty_output_fails(self):
        result = await run_guardrails("", "cost_estimate")
        assert result["passed"] is False
        assert len(result["validation_errors"]) > 0

    async def test_markdown_json_parsed(self):
        raw = '```json\n{"value": 42}\n```'
        result = await run_guardrails(raw, "daily_report")
        assert result["parsed_output"] is not None
        assert result["parsed_output"]["value"] == 42

    async def test_unstructured_text_wraps(self):
        raw = "This is plain text output from an agent."
        result = await run_guardrails(raw, "daily_report")
        assert result["parsed_output"] is not None
        assert "raw_text" in result["parsed_output"]

    async def test_pipeline_has_confidence(self):
        raw = json.dumps({"status": "ok", "score": 0.95})
        result = await run_guardrails(raw, "daily_report")
        assert result["confidence_score"] is not None
        assert 0.0 <= result["confidence_score"] <= 1.0

    async def test_pipeline_has_routing(self):
        raw = json.dumps({"status": "ok"})
        result = await run_guardrails(raw, "daily_report")
        assert result["routing_decision"] in (
            "auto_approve",
            "human_review",
            "expert_escalation",
        )

    def test_build_pipeline(self):
        pipeline = build_guardrails_pipeline()
        assert pipeline is not None

    async def test_latency_tracked(self):
        raw = json.dumps({"data": "test"})
        result = await run_guardrails(raw, "daily_report")
        assert result["latency_ms"] >= 0
