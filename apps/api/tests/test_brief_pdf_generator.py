"""Tests for the intelligence brief PDF generator.

Pin status-color mapping, score-bar formatting, and the
``generate_brief_pdf`` happy-path + missing-data tolerance — the
report goes to clients, so a crash on missing fields would block
the whole notification flow.
"""

from __future__ import annotations

from app.services.agents.brief_pdf_generator import (
    CONSTRUCTAI_BLUE,
    STATUS_GREEN,
    STATUS_RED,
    STATUS_YELLOW,
    _score_bar,
    _status_color,
    generate_brief_pdf,
)

# =========================================================================
# _status_color — pin GREEN / YELLOW / RED mapping
# =========================================================================


def test_status_color_green():
    assert _status_color("GREEN") == STATUS_GREEN


def test_status_color_yellow():
    assert _status_color("YELLOW") == STATUS_YELLOW


def test_status_color_red():
    assert _status_color("RED") == STATUS_RED


def test_status_color_unknown_falls_back_to_red():
    """Unknown status (e.g., 'PURPLE', '') -> RED. Pin: never silently
    pick GREEN for an unknown — that would mislead clients reading the
    PDF."""
    assert _status_color("UNKNOWN") == STATUS_RED
    assert _status_color("") == STATUS_RED
    assert _status_color("green") == STATUS_RED  # case-sensitive


# =========================================================================
# _score_bar — pin filled/empty character math
# =========================================================================


def test_score_bar_zero():
    """0/100 -> 0 filled blocks, 10 empty."""
    from reportlab.lib.styles import ParagraphStyle

    style = ParagraphStyle("X")
    out = _score_bar(0, "Schedule", style)
    # Returns [Paragraph(label), Paragraph(bar+score)]
    assert len(out) == 2
    bar_text = out[1].text
    assert "0/100" in bar_text
    # 10 empty blocks (light shade), 0 full blocks
    assert bar_text.count("░") == 10
    assert bar_text.count("█") == 0


def test_score_bar_full():
    """100/100 -> 10 filled, 0 empty."""
    from reportlab.lib.styles import ParagraphStyle

    style = ParagraphStyle("X")
    out = _score_bar(100, "Schedule", style)
    bar_text = out[1].text
    assert "100/100" in bar_text
    assert bar_text.count("█") == 10
    assert bar_text.count("░") == 0


def test_score_bar_mid():
    """50/100 -> 5 filled, 5 empty."""
    from reportlab.lib.styles import ParagraphStyle

    style = ParagraphStyle("X")
    out = _score_bar(50, "Cost", style)
    bar_text = out[1].text
    assert "50/100" in bar_text
    assert bar_text.count("█") == 5
    assert bar_text.count("░") == 5


def test_score_bar_truncation():
    """75/100 -> 7 filled (integer division), 3 empty."""
    from reportlab.lib.styles import ParagraphStyle

    style = ParagraphStyle("X")
    out = _score_bar(75, "Risk", style)
    bar_text = out[1].text
    assert bar_text.count("█") == 7
    assert bar_text.count("░") == 3


def test_score_bar_label_passes_through():
    from reportlab.lib.styles import ParagraphStyle

    style = ParagraphStyle("X")
    out = _score_bar(50, "Productivity", style)
    assert "Productivity" in out[0].text


# =========================================================================
# generate_brief_pdf — happy path
# =========================================================================


def _full_brief() -> dict:
    """A representative brief with all sections populated."""
    return {
        "report_date": "2026-04-26",
        "project_status": "GREEN",
        "overall_health_score": 82,
        "executive_summary": "Project on track with minor cost variance.",
        "schedule_health_score": 80,
        "cost_health_score": 75,
        "risk_score": 30,
        "productivity_score": 85,
        "schedule_intelligence": {
            "spi_values": [0.95, 0.97, 1.02],
            "spi_trend": "improving",
            "p50_duration": 120,
            "p90_duration": 145,
            "critical_path": ["A", "B", "C"],
            "float_erosion_alerts": [
                {"activity_name": "Foundation", "erosion_days": 3},
            ],
            "warnings": ["Weather delay possible"],
        },
        "cost_intelligence": {
            "evm_metrics": {
                "cpi": 1.02,
                "spi": 0.98,
                "eac": 1500000,
                "vac": -25000,
                "cv": 5000,
                "sv": -10000,
                "percent_complete": 45,
                "tcpi": 1.01,
            },
            "co_impact": {"total_change_orders": 4, "percent_of_contract": 2.3},
            "budget_variance_flags": [{"division": "03"}, {"division": "05"}],
        },
        "risk_intelligence": {
            "top_5_risks": [
                {
                    "description": "Concrete supply delay",
                    "probability": "Medium",
                    "impact": "High",
                    "mitigation": "Backup supplier identified",
                }
            ],
            "weather_outlook": {"red_alerts": 1, "yellow_alerts": 3},
        },
        "action_items": [
            {
                "action": "Confirm backup concrete supplier",
                "responsible": "PM",
                "due_by": "2026-04-30",
                "reason": "SPI dipped 2 weeks ago",
            }
        ],
        "guardrails_result": {"confidence_score": 0.85, "needs_human_review": False},
    }


def test_generate_brief_pdf_returns_bytes():
    out = generate_brief_pdf(_full_brief(), "Test Project", "PRJ-001")
    assert isinstance(out, bytes)
    assert len(out) > 0


def test_generate_brief_pdf_starts_with_pdf_magic():
    """[contract] Output must be a valid PDF (starts with %PDF-)."""
    out = generate_brief_pdf(_full_brief(), "Test Project")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_yellow_status():
    brief = _full_brief()
    brief["project_status"] = "YELLOW"
    out = generate_brief_pdf(brief, "Test Project")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_red_status():
    brief = _full_brief()
    brief["project_status"] = "RED"
    out = generate_brief_pdf(brief, "Test Project")
    assert out.startswith(b"%PDF-")


# =========================================================================
# Missing-data tolerance — must NOT crash on partial briefs
# =========================================================================


def test_generate_brief_pdf_minimal_brief():
    """Empty brief dict still produces a valid PDF — defaults kick in."""
    out = generate_brief_pdf({}, "Bare Project")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_no_evm_metrics():
    """Cost section without EVM metrics still renders (skips table)."""
    brief = _full_brief()
    brief["cost_intelligence"] = {"co_impact": {"total_change_orders": 0}}
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_no_risks():
    """No top-5 risks -> renders the "No significant risks" line."""
    brief = _full_brief()
    brief["risk_intelligence"] = {"top_5_risks": []}
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_no_action_items():
    """No action items -> renders the "No action items generated" line."""
    brief = _full_brief()
    brief["action_items"] = []
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_no_schedule_data():
    """Empty schedule dict -> renders the "No schedule data" line."""
    brief = _full_brief()
    brief["schedule_intelligence"] = {}
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_no_project_number():
    """Project number is optional — title should fall back to just
    the name (no parentheses)."""
    out = generate_brief_pdf(_full_brief(), "Tower 42")
    # Just verify no crash:
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_human_review_required():
    """Footer note flips when guardrails request human review."""
    brief = _full_brief()
    brief["guardrails_result"] = {"confidence_score": 0.45, "needs_human_review": True}
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


# =========================================================================
# Many-items truncation
# =========================================================================


def test_generate_brief_pdf_truncates_to_top_5_risks():
    """Risk table truncates to top 5 — 100 risks should NOT crash and
    should still produce a single PDF."""
    brief = _full_brief()
    brief["risk_intelligence"]["top_5_risks"] = [
        {
            "description": f"Risk {i}",
            "probability": "Low",
            "impact": "Low",
            "mitigation": "TBD",
        }
        for i in range(100)
    ]
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


def test_generate_brief_pdf_truncates_action_items():
    brief = _full_brief()
    brief["action_items"] = [
        {"action": f"Item {i}", "responsible": "X", "due_by": "2026-05-01", "reason": "x"}
        for i in range(50)
    ]
    out = generate_brief_pdf(brief, "Test")
    assert out.startswith(b"%PDF-")


# =========================================================================
# Color palette pin — the brand palette is referenced in a few places
# =========================================================================


def test_constructai_blue_is_brand_color():
    """Pin: brand blue is #1a365d (RGB 26/53/93). Refactor must NOT
    swap the color — it appears on the header and in headings."""
    # 0x1a / 255 ≈ 0.102, 0x36 / 255 ≈ 0.212, 0x5d / 255 ≈ 0.365
    assert abs(CONSTRUCTAI_BLUE.red - 0.102) < 0.01
    assert abs(CONSTRUCTAI_BLUE.green - 0.212) < 0.01
    assert abs(CONSTRUCTAI_BLUE.blue - 0.365) < 0.01
