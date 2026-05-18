"""Tests for daily report generator private helpers.

Pin the SPI/CPI threshold logic in ``_progress_status`` and the
markdown section structure in ``_build_markdown``.
"""

from __future__ import annotations

from datetime import date

from app.services.communication.report_generator import (
    _build_markdown,
    _progress_status,
)

# =========================================================================
# _progress_status — pin SPI/CPI thresholds
# =========================================================================


def test_progress_on_track_when_both_at_or_above_0_95():
    """[business invariant] Both SPI >= 0.95 AND CPI >= 0.95 -> 'On Track'.
    Pin so a refactor doesn't relax the threshold (would mask schedule slip)."""
    assert _progress_status({"spi": 0.95, "cpi": 0.95}) == "On Track"
    assert _progress_status({"spi": 1.0, "cpi": 1.0}) == "On Track"
    assert _progress_status({"spi": 1.05, "cpi": 1.02}) == "On Track"


def test_progress_monitor_when_in_band():
    """[boundary] SPI/CPI in [0.9, 0.95) -> 'Monitor' (warning band
    but not 'At Risk' yet)."""
    assert _progress_status({"spi": 0.94, "cpi": 0.94}) == "Monitor"
    assert _progress_status({"spi": 0.92, "cpi": 0.91}) == "Monitor"
    assert _progress_status({"spi": 0.9, "cpi": 0.9}) == "Monitor"


def test_progress_at_risk_when_either_below_0_9():
    """[business invariant] Either SPI < 0.9 OR CPI < 0.9 -> 'At Risk'.
    Pin: a single failing metric triggers escalation. Refactor must NOT
    require BOTH to fail."""
    # Just SPI under 0.9:
    assert _progress_status({"spi": 0.85, "cpi": 0.95}) == "At Risk"
    # Just CPI under 0.9:
    assert _progress_status({"spi": 0.95, "cpi": 0.85}) == "At Risk"
    # Both under:
    assert _progress_status({"spi": 0.7, "cpi": 0.6}) == "At Risk"


def test_progress_default_values_one_one_returns_on_track():
    """[fallback] Empty EVM dict -> defaults SPI=1, CPI=1 -> 'On Track'.
    Pin: missing data should not trigger a false 'At Risk' alert."""
    assert _progress_status({}) == "On Track"


def test_progress_string_values_coerced_to_float():
    """[robustness] String SPI/CPI from JSON deserialization
    (e.g. Decimal-as-string) -> coerced to float."""
    assert _progress_status({"spi": "0.95", "cpi": "0.95"}) == "On Track"
    assert _progress_status({"spi": "0.85", "cpi": "1.0"}) == "At Risk"


# =========================================================================
# _build_markdown — section structure
# =========================================================================


def test_markdown_has_dated_header():
    out = _build_markdown({}, date(2026, 4, 26))
    assert "# Daily Construction Report - 2026-04-26" in out


def test_markdown_has_all_4_canonical_sections():
    """[contract] Every report has Weather + Workforce + Progress +
    Safety section headers, even when data is missing. Pin: refactor
    must NOT silently drop a section (UI rendering depends on this)."""
    out = _build_markdown({}, date(2026, 1, 1))
    assert "## Weather" in out
    assert "## Workforce" in out
    assert "## Project Progress" in out
    assert "## Safety" in out


def test_markdown_weather_section():
    sections = {"weather": {"conditions": "Sunny", "temperature_f": 72}}
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Conditions: Sunny" in out
    assert "Temperature: 72F" in out


def test_markdown_weather_no_temperature():
    """Missing temperature -> no Temperature line (don't show 'NoneF')."""
    sections = {"weather": {"conditions": "Cloudy"}}
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Conditions: Cloudy" in out
    assert "Temperature:" not in out


def test_markdown_weather_default_na_for_missing_conditions():
    """[fallback] Missing weather entirely -> Conditions: N/A."""
    out = _build_markdown({}, date(2026, 1, 1))
    assert "Conditions: N/A" in out


def test_markdown_workforce_section():
    sections = {
        "workforce": {
            "crew_count": 25,
            "work_hours": 200,
            "activities": ["Foundation pour", "Rebar install"],
        }
    }
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Crew Count: 25" in out
    assert "Work Hours: 200" in out
    assert "Activities Completed" in out
    assert "Foundation pour" in out
    assert "Rebar install" in out


def test_markdown_workforce_zero_defaults():
    """[fallback] Empty workforce dict -> Crew Count: 0, Work Hours: 0
    (don't show None)."""
    sections = {"workforce": {}}
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Crew Count: 0" in out
    assert "Work Hours: 0" in out


def test_markdown_workforce_activities_dict_format():
    """[contract] Activities can be dicts with 'description' key."""
    sections = {
        "workforce": {
            "crew_count": 10,
            "activities": [{"description": "Concrete pour", "trade": "concrete"}],
        }
    }
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Concrete pour" in out


def test_markdown_workforce_no_activities_section_when_empty():
    """No activities -> no 'Activities Completed' subsection."""
    sections = {"workforce": {"crew_count": 5, "activities": []}}
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Activities Completed" not in out


def test_markdown_progress_section():
    sections = {
        "progress": {
            "percent_complete": 45.5,
            "spi": 0.98,
            "cpi": 1.02,
            "status": "On Track",
        }
    }
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Completion: 45.5" in out
    assert "SPI: 0.98" in out
    assert "CPI: 1.02" in out
    assert "Status: On Track" in out


def test_markdown_progress_default_na_when_missing():
    """[fallback] All progress fields default to N/A (don't show None)."""
    out = _build_markdown({}, date(2026, 1, 1))
    assert "Completion: N/A" in out
    assert "SPI: N/A" in out
    assert "CPI: N/A" in out
    assert "Status: N/A" in out


def test_markdown_safety_section():
    sections = {"safety": {"incidents": 3}}
    out = _build_markdown(sections, date(2026, 1, 1))
    assert "Incidents: 3" in out


def test_markdown_safety_no_incidents_default_zero():
    """[fallback] Missing safety -> Incidents: 0 (NOT 'N/A' — count
    zero is a meaningful value, NOT 'unknown')."""
    out = _build_markdown({}, date(2026, 1, 1))
    assert "Incidents: 0" in out


def test_markdown_section_ordering():
    """[contract] Sections appear in canonical order: Weather,
    Workforce, Progress, Safety. Pin: refactor must NOT reorder
    (printed reports depend on consistent layout)."""
    out = _build_markdown({}, date(2026, 1, 1))
    weather_idx = out.index("## Weather")
    workforce_idx = out.index("## Workforce")
    progress_idx = out.index("## Project Progress")
    safety_idx = out.index("## Safety")
    assert weather_idx < workforce_idx < progress_idx < safety_idx


def test_markdown_returns_joined_string():
    """[contract] Output is a single string (newline-joined), not a
    list. Pin so callers don't need to .join() themselves."""
    out = _build_markdown({}, date(2026, 1, 1))
    assert isinstance(out, str)
    assert "\n" in out  # multi-line
