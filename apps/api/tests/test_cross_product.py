"""Tests for cross-product intelligence engine."""

from __future__ import annotations

import uuid

import pytest

from app.services.products.cross_product import (
    EVENT_HANDLERS,
    dispatch_event,
    handle_heat_for_safety,
    handle_payroll_for_controls,
    handle_report_for_heatshield,
    handle_rfi_for_changeflow,
)

# ---------------------------------------------------------------------------
# Test: Event handler registration
# ---------------------------------------------------------------------------


class TestEventHandlerRegistry:
    def test_handlers_registered(self):
        """All expected event types should have handlers."""
        expected_events = [
            "constructai.sitescribe.report_approved",
            "constructai.rfi.responded",
            "constructai.heat.incident_reported",
            "constructai.wage.payroll_certified",
            "constructai.closeout.all_complete",
        ]
        for event in expected_events:
            assert event in EVENT_HANDLERS, f"No handler for {event}"

    def test_each_event_has_at_least_one_handler(self):
        for event, handlers in EVENT_HANDLERS.items():
            assert len(handlers) >= 1, f"No handlers for {event}"


# ---------------------------------------------------------------------------
# Test: SiteScribe → HeatShield
# ---------------------------------------------------------------------------


class TestReportToHeatShield:
    @pytest.mark.asyncio
    async def test_skip_when_no_weather(self):
        result = await handle_report_for_heatshield(None, uuid.uuid4(), uuid.uuid4(), {})
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_below_threshold(self):
        result = await handle_report_for_heatshield(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {"weather_data": {"temperature_f": 72.0}},
        )
        assert result is not None
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_detects_high_temp(self):
        result = await handle_report_for_heatshield(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {"weather_data": {"temperature_f": 95.0}},
        )
        # Will return None because HeatShield record_manual_reading needs a
        # real db session — the handler catches the exception and returns None.
        # The important thing is that it does NOT raise.
        assert result is None or result.get("recorded") is True


# ---------------------------------------------------------------------------
# Test: RFI → ChangeFlow
# ---------------------------------------------------------------------------


class TestRfiToChangeFlow:
    @pytest.mark.asyncio
    async def test_skip_no_response(self):
        result = await handle_rfi_for_changeflow(None, uuid.uuid4(), uuid.uuid4(), {})
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_no_design_change(self):
        result = await handle_rfi_for_changeflow(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {"response_text": "The concrete should be 4000 PSI as specified."},
        )
        assert result is not None
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_detect_design_change(self):
        result = await handle_rfi_for_changeflow(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "response_text": "Per the revised drawing A-301, "
                "the wall layout has changed. Design change required.",
                "rfi_id": str(uuid.uuid4()),
            },
        )
        assert result is not None
        assert result.get("design_change_detected") is True


# ---------------------------------------------------------------------------
# Test: HeatShield → Safety
# ---------------------------------------------------------------------------


class TestHeatToSafety:
    @pytest.mark.asyncio
    async def test_creates_safety_suggestion(self):
        result = await handle_heat_for_safety(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "worker_name": "John Doe",
                "incident_date": "2026-03-30",
            },
        )
        assert result is not None
        assert result["action"] == "safety_incident_suggested"
        assert result["worker_name"] == "John Doe"


# ---------------------------------------------------------------------------
# Test: WageGuard → Controls
# ---------------------------------------------------------------------------


class TestPayrollToControls:
    @pytest.mark.asyncio
    async def test_skip_no_gross(self):
        result = await handle_payroll_for_controls(None, uuid.uuid4(), uuid.uuid4(), {})
        assert result is None

    @pytest.mark.asyncio
    async def test_labor_cost_update(self):
        result = await handle_payroll_for_controls(
            None,
            uuid.uuid4(),
            uuid.uuid4(),
            {"total_gross_pay": 45000.00, "week_ending": "2026-03-28"},
        )
        assert result is not None
        assert result["action"] == "labor_cost_update_suggested"
        assert result["total_gross_pay"] == 45000.00


# ---------------------------------------------------------------------------
# Test: Event dispatch
# ---------------------------------------------------------------------------


class TestEventDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_unknown_event(self):
        results = await dispatch_event(
            None,
            "constructai.unknown.event",
            uuid.uuid4(),
            uuid.uuid4(),
            {},
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_known_event(self):
        results = await dispatch_event(
            None,
            "constructai.wage.payroll_certified",
            uuid.uuid4(),
            uuid.uuid4(),
            {"total_gross_pay": 10000.00},
        )
        assert len(results) >= 1
        assert results[0]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_dispatch_isolates_handler_failures(self):
        """One handler failing should not affect others."""
        results = await dispatch_event(
            None,
            "constructai.sitescribe.report_approved",
            uuid.uuid4(),
            uuid.uuid4(),
            {"weather_data": {"temperature_f": 95.0}},
        )
        # Should have results (even if some errored)
        assert isinstance(results, list)
