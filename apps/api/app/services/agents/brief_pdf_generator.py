"""Professional PDF generation for intelligence briefs.

Uses ReportLab to produce a multi-page report with:
- ConstructAI header and project info
- Health score with color indicator
- Section summaries for schedule, cost, risk, productivity
- Action items table
"""

from __future__ import annotations

import io
import logging
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

CONSTRUCTAI_BLUE = colors.Color(0.102, 0.212, 0.365)  # #1a365d
CONSTRUCTAI_LIGHT = colors.Color(0.925, 0.937, 0.957)  # #eceff4
STATUS_GREEN = colors.Color(0.133, 0.545, 0.133)
STATUS_YELLOW = colors.Color(0.855, 0.647, 0.125)
STATUS_RED = colors.Color(0.804, 0.161, 0.161)
HEADER_BG = colors.Color(0.85, 0.85, 0.85)
ALT_ROW = colors.Color(0.97, 0.97, 0.97)


def _status_color(status: str) -> colors.Color:
    if status == "GREEN":
        return STATUS_GREEN
    if status == "YELLOW":
        return STATUS_YELLOW
    return STATUS_RED


def _score_bar(score: int, label: str, style: ParagraphStyle) -> list:
    """Create a simple text representation of a score."""
    filled = score // 10
    empty = 10 - filled
    bar = "\u2588" * filled + "\u2591" * empty
    return [Paragraph(f"<b>{label}</b>", style), Paragraph(f"{bar}  {score}/100", style)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_brief_pdf(
    brief_data: dict,
    project_name: str,
    project_number: str = "",
) -> bytes:
    """Generate a professional intelligence brief PDF.

    Parameters
    ----------
    brief_data: Complete brief output from generate_weekly_brief()
    project_name: Display name of the project
    project_number: Optional project number

    Returns
    -------
    PDF content as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "BriefTitle",
        parent=styles["Title"],
        fontSize=16,
        textColor=CONSTRUCTAI_BLUE,
        spaceAfter=4,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "BriefSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=12,
        textColor=colors.grey,
    )
    section_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=CONSTRUCTAI_BLUE,
        spaceBefore=16,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BriefBody",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        spaceAfter=4,
    )
    ParagraphStyle(
        "BriefBodyRight",
        parent=body_style,
        alignment=TA_RIGHT,
    )
    cell_style = ParagraphStyle(
        "BriefCell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
    )
    cell_bold = ParagraphStyle(
        "BriefCellBold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )

    story: list = []
    report_date = brief_data.get("report_date", date.today().isoformat())
    status = brief_data.get("project_status", "YELLOW")
    overall_score = brief_data.get("overall_health_score", 50)

    # ======================================================================
    # PAGE 1: Header + Executive Summary + Key Metrics
    # ======================================================================

    # Header banner
    header_data = [["CONSTRUCTAI", "PROJECT INTELLIGENCE BRIEF"]]
    header_table = Table(header_data, colWidths=[3.5 * inch, 3.5 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CONSTRUCTAI_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (0, 0), 14),
                ("FONTSIZE", (1, 0), (1, 0), 11),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 8))

    # Project info
    proj_label = project_name
    if project_number:
        proj_label += f" ({project_number})"
    story.append(Paragraph(proj_label, title_style))
    story.append(Paragraph(f"Report Date: {report_date}", subtitle_style))

    # Health score banner
    sc = _status_color(status)
    score_data = [
        [
            Paragraph(
                "<b>OVERALL HEALTH</b>", ParagraphStyle("", fontSize=11, textColor=colors.white)
            ),
            Paragraph(
                f"<b>{overall_score}</b>/100",
                ParagraphStyle("", fontSize=18, textColor=colors.white, alignment=TA_CENTER),
            ),
            Paragraph(
                f"<b>{status}</b>",
                ParagraphStyle("", fontSize=14, textColor=colors.white, alignment=TA_RIGHT),
            ),
        ]
    ]
    score_table = Table(score_data, colWidths=[2.5 * inch, 2.0 * inch, 2.5 * inch])
    score_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), sc),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(score_table)
    story.append(Spacer(1, 12))

    # Executive summary
    story.append(Paragraph("Executive Summary", section_style))
    summary = brief_data.get("executive_summary", "No summary available.")
    story.append(Paragraph(summary, body_style))
    story.append(Spacer(1, 8))

    # 4-quadrant scores
    sched_score = brief_data.get("schedule_health_score", 50)
    cost_score = brief_data.get("cost_health_score", 50)
    risk_val = brief_data.get("risk_score", 50)
    prod_score = brief_data.get("productivity_score", 50)

    quad_data = [
        [
            Paragraph("<b>Schedule</b>", cell_bold),
            Paragraph(f"{sched_score}/100", cell_style),
            Paragraph("<b>Cost</b>", cell_bold),
            Paragraph(f"{cost_score}/100", cell_style),
        ],
        [
            Paragraph("<b>Risk</b>", cell_bold),
            Paragraph(f"{risk_val}/100", cell_style),
            Paragraph("<b>Productivity</b>", cell_bold),
            Paragraph(f"{prod_score}/100", cell_style),
        ],
    ]
    quad_table = Table(quad_data, colWidths=[1.5 * inch, 2.0 * inch, 1.5 * inch, 2.0 * inch])
    quad_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), CONSTRUCTAI_LIGHT),
                ("BACKGROUND", (2, 0), (2, -1), CONSTRUCTAI_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(quad_table)
    story.append(Spacer(1, 16))

    # ======================================================================
    # SCHEDULE INTELLIGENCE
    # ======================================================================

    story.append(Paragraph("Schedule Intelligence", section_style))
    sched = brief_data.get("schedule_intelligence", {})
    sched_lines = []
    spi_vals = sched.get("spi_values", [])
    if spi_vals:
        sched_lines.append(
            f"Latest SPI: {spi_vals[-1]:.3f} | Trend: {sched.get('spi_trend', 'N/A')}"
        )
    p50 = sched.get("p50_duration")
    if p50:
        sched_lines.append(
            f"Monte Carlo: P50 = {p50} days, P90 = {sched.get('p90_duration', 'N/A')} days"
        )
    cp = sched.get("critical_path", [])
    if cp:
        sched_lines.append(f"Critical Path: {len(cp)} activities")
    erosion = sched.get("float_erosion_alerts", [])
    if erosion:
        sched_lines.append(f"Float Erosion: {len(erosion)} activities losing float")
        for e in erosion[:3]:
            sched_lines.append(
                f"  - {e.get('activity_name', '')}: lost {e.get('erosion_days', 0)} days"
            )
    warnings = sched.get("warnings", [])
    for w in warnings:
        sched_lines.append(f"Warning: {w}")
    if not sched_lines:
        sched_lines.append("No schedule data available")
    for line in sched_lines:
        story.append(Paragraph(f"\u2022 {line}", body_style))
    story.append(Spacer(1, 8))

    # ======================================================================
    # COST INTELLIGENCE
    # ======================================================================

    story.append(Paragraph("Cost Intelligence", section_style))
    cost = brief_data.get("cost_intelligence", {})
    evm = cost.get("evm_metrics", {})
    if evm:
        evm_data = [
            ["Metric", "Value", "Metric", "Value"],
            ["CPI", str(evm.get("cpi", "N/A")), "SPI", str(evm.get("spi", "N/A"))],
            ["EAC", f"${evm.get('eac', 'N/A')}", "VAC", f"${evm.get('vac', 'N/A')}"],
            ["CV", f"${evm.get('cv', 'N/A')}", "SV", f"${evm.get('sv', 'N/A')}"],
            [
                "% Complete",
                f"{evm.get('percent_complete', 'N/A')}%",
                "TCPI",
                str(evm.get("tcpi", "N/A")),
            ],
        ]
        evm_table = Table(evm_data, colWidths=[1.2 * inch, 2.3 * inch, 1.2 * inch, 2.3 * inch])
        evm_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    *[("BACKGROUND", (0, r), (-1, r), ALT_ROW) for r in range(2, 5, 2)],
                ]
            )
        )
        story.append(evm_table)

    co = cost.get("co_impact", {})
    if co.get("total_change_orders", 0) > 0:
        story.append(
            Paragraph(
                f"\u2022 Change Orders: {co['total_change_orders']} total, "
                f"{co.get('percent_of_contract', 0):.1f}% of contract value",
                body_style,
            )
        )
    flags = cost.get("budget_variance_flags", [])
    if flags:
        story.append(Paragraph(f"\u2022 Budget Overruns: {len(flags)} CSI divisions", body_style))
    story.append(Spacer(1, 8))

    # ======================================================================
    # RISK INTELLIGENCE
    # ======================================================================

    story.append(Paragraph("Risk Intelligence", section_style))
    risk = brief_data.get("risk_intelligence", {})
    risks = risk.get("top_5_risks", [])
    if risks:
        risk_table_data = [["#", "Risk", "Probability", "Impact", "Mitigation"]]
        for i, r in enumerate(risks[:5], 1):
            risk_table_data.append(
                [
                    str(i),
                    Paragraph(r.get("description", ""), cell_style),
                    r.get("probability", ""),
                    r.get("impact", ""),
                    Paragraph(r.get("mitigation", ""), cell_style),
                ]
            )
        risk_table = Table(
            risk_table_data,
            colWidths=[0.3 * inch, 2.2 * inch, 0.8 * inch, 0.7 * inch, 3.0 * inch],
        )
        risk_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    *[
                        ("BACKGROUND", (0, r), (-1, r), ALT_ROW)
                        for r in range(2, len(risk_table_data), 2)
                    ],
                ]
            )
        )
        story.append(risk_table)
    else:
        story.append(Paragraph("\u2022 No significant risks identified", body_style))

    weather = risk.get("weather_outlook", {})
    if weather:
        story.append(
            Paragraph(
                f"\u2022 Weather: {weather.get('red_alerts', 0)} RED, "
                f"{weather.get('yellow_alerts', 0)} YELLOW alerts this week",
                body_style,
            )
        )
    story.append(Spacer(1, 8))

    # ======================================================================
    # ACTION ITEMS
    # ======================================================================

    story.append(Paragraph("Action Items", section_style))
    action_items = brief_data.get("action_items", [])
    if action_items:
        ai_data = [["#", "Action", "Responsible", "Due By", "Reason"]]
        for i, ai in enumerate(action_items[:5], 1):
            ai_data.append(
                [
                    str(i),
                    Paragraph(str(ai.get("action", "")), cell_style),
                    str(ai.get("responsible", "")),
                    str(ai.get("due_by", "")),
                    Paragraph(str(ai.get("reason", "")), cell_style),
                ]
            )
        ai_table = Table(
            ai_data,
            colWidths=[0.3 * inch, 2.5 * inch, 1.2 * inch, 0.9 * inch, 2.1 * inch],
        )
        ai_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), CONSTRUCTAI_BLUE),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    *[("BACKGROUND", (0, r), (-1, r), ALT_ROW) for r in range(2, len(ai_data), 2)],
                ]
            )
        )
        story.append(ai_table)
    else:
        story.append(Paragraph("\u2022 No action items generated", body_style))

    story.append(Spacer(1, 16))

    # Footer
    guardrails = brief_data.get("guardrails_result", {})
    conf = guardrails.get("confidence_score", "N/A")
    review = "Yes" if guardrails.get("needs_human_review") else "No"
    footer_style = ParagraphStyle(
        "Footer", parent=styles["Normal"], fontSize=7, textColor=colors.grey
    )
    story.append(
        Paragraph(
            f"Generated by ConstructAI WeeklyBriefAgent | "
            f"Confidence: {conf} | Human Review Required: {review}",
            footer_style,
        )
    )

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
