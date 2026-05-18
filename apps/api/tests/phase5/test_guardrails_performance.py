"""Performance test for guardrails pipeline."""

from __future__ import annotations

import json
import time

from app.services.guardrails.pipeline import run_guardrails


class TestGuardrailsPerformance:
    async def test_guardrails_latency(self):
        """Full 6-stage pipeline should complete in <2000ms."""
        raw = json.dumps(
            {
                "unit_cost": 250.0,
                "csi_code": "03 30 00",
                "total_cost": 50000.0,
                "quantity": 200,
                "description": "Cast-in-place concrete",
            }
        )
        start = time.monotonic()
        result = await run_guardrails(raw, "cost_estimate")
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 2000
        assert result["routing_decision"] is not None

    async def test_pipeline_internal_latency(self):
        """Internal latency tracking should be reasonable."""
        raw = json.dumps({"data": "test", "value": 42})
        result = await run_guardrails(raw, "daily_report")
        assert result["latency_ms"] < 2000
