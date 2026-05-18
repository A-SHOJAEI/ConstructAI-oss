"""Daily log / daily report PDF export using ReportLab.

Generates a single-page (or multi-page) daily construction report with
sections: Weather, Manpower, Work Description, Safety, Delays, Deliveries,
Equipment, Visitors, and Photos.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
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


def generate_daily_log_pdf(
    log: dict[str, Any],
    project_name: str = "Project",
) -> bytes:
    """Generate a daily construction report PDF.

    Parameters
    ----------
    log : dict
        Daily log data (from ``_log_to_dict()``).
    project_name : str
        Name shown in the report header.

    Returns
    -------
    bytes
        PDF file content.
    """
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DLTitle", parent=styles["Heading1"], fontSize=14, alignment=TA_CENTER
    )
    section_style = ParagraphStyle(
        "DLSection",
        parent=styles["Heading2"],
        fontSize=11,
        textColor=colors.HexColor("#1E3A5F"),
        spaceBefore=8,
        spaceAfter=4,
    )
    normal = ParagraphStyle("DLNormal", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("DLSmall", parent=styles["Normal"], fontSize=8, leading=10)

    elements: list = []

    log_date = log.get("log_date", "")
    status = log.get("status", "draft").upper()

    # --- Header ---
    elements.append(Paragraph(f"Daily Construction Report — {project_name}", title_style))
    elements.append(Paragraph(f"Date: {log_date}    Status: {status}", normal))
    elements.append(Spacer(1, 0.15 * inch))

    # --- Weather ---
    weather = log.get("weather") or {}
    if weather:
        elements.append(Paragraph("Weather Conditions", section_style))
        w_data = [
            ["High", "Low", "Precip (mm)", "Wind (max)", "Conditions"],
            [
                str(weather.get("temperature_high", "—")),
                str(weather.get("temperature_low", "—")),
                str(weather.get("precipitation_mm", "—")),
                str(weather.get("wind_speed_max", "—")),
                str(weather.get("conditions", "—")),
            ],
        ]
        delay_hrs = log.get("weather_delay_hours")
        if delay_hrs is not None:
            w_data[0].append("Delay (hrs)")
            w_data[1].append(str(delay_hrs))

        w_table = Table(w_data, colWidths=[1.1 * inch] * len(w_data[0]))
        w_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(w_table)
        elements.append(Spacer(1, 0.1 * inch))

    # --- Manpower ---
    manpower = log.get("manpower_by_trade") or []
    crew_count = log.get("crew_count", 0)
    work_hours = log.get("work_hours", 0)
    elements.append(Paragraph("Workforce", section_style))
    elements.append(
        Paragraph(f"Total Crew: {crew_count}    Total Work Hours: {work_hours}", normal)
    )
    if manpower:
        mp_data = [["Trade", "Headcount", "Hours"]]
        for entry in manpower:
            if isinstance(entry, dict):
                mp_data.append(
                    [
                        entry.get("trade", ""),
                        str(entry.get("headcount", 0)),
                        str(entry.get("hours", 0)),
                    ]
                )
        mp_table = Table(mp_data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch])
        mp_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(mp_table)
    elements.append(Spacer(1, 0.1 * inch))

    # --- Work Description ---
    narrative = log.get("work_narrative")
    if narrative:
        elements.append(Paragraph("Work Description", section_style))
        elements.append(Paragraph(narrative, normal))
        elements.append(Spacer(1, 0.1 * inch))

    activities = log.get("activities_completed") or []
    if activities:
        elements.append(Paragraph("Activities Completed", section_style))
        for act in activities:
            desc = act.get("description", str(act)) if isinstance(act, dict) else str(act)
            elements.append(Paragraph(f"• {desc}", small))
        elements.append(Spacer(1, 0.1 * inch))

    # --- Safety ---
    safety_incidents = log.get("safety_incidents")
    safety_topic = log.get("safety_topic_discussed")
    if safety_incidents or safety_topic:
        elements.append(Paragraph("Safety", section_style))
        if safety_topic:
            elements.append(Paragraph(f"<b>Toolbox Talk Topic:</b> {safety_topic}", normal))
        if safety_incidents:
            elements.append(Paragraph(f"<b>Incidents:</b> {safety_incidents}", normal))
        else:
            elements.append(Paragraph("No incidents reported.", small))
        elements.append(Spacer(1, 0.1 * inch))

    # --- Delays ---
    delays = log.get("delays") or []
    if delays:
        elements.append(Paragraph("Delays", section_style))
        dl_data = [["Description", "Hours Lost"]]
        for d in delays:
            if isinstance(d, dict):
                dl_data.append(
                    [
                        d.get("description", ""),
                        str(d.get("hours_lost", d.get("hours", "—"))),
                    ]
                )
        dl_table = Table(dl_data, colWidths=[4.5 * inch, 1.5 * inch])
        dl_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FEF3C7")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(dl_table)
        elements.append(Spacer(1, 0.1 * inch))

    # --- Deliveries ---
    deliveries = log.get("deliveries") or []
    if deliveries:
        elements.append(Paragraph("Deliveries", section_style))
        dv_data = [["Description", "Supplier", "Tracking #", "Received By"]]
        for dv in deliveries:
            if isinstance(dv, dict):
                dv_data.append(
                    [
                        dv.get("description", ""),
                        dv.get("supplier", ""),
                        dv.get("tracking_number", ""),
                        dv.get("received_by", ""),
                    ]
                )
        dv_table = Table(dv_data, colWidths=[2.0 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
        dv_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(dv_table)
        elements.append(Spacer(1, 0.1 * inch))

    # --- Equipment ---
    equipment = log.get("equipment_entries") or []
    if equipment:
        elements.append(Paragraph("Equipment On Site", section_style))
        eq_data = [["Type", "ID", "Hours Used", "Notes"]]
        for eq in equipment:
            if isinstance(eq, dict):
                eq_data.append(
                    [
                        eq.get("equipment_type", ""),
                        eq.get("equipment_id", ""),
                        str(eq.get("hours_used", "")),
                        eq.get("notes", ""),
                    ]
                )
        eq_table = Table(eq_data, colWidths=[1.8 * inch, 1.5 * inch, 1.0 * inch, 2.2 * inch])
        eq_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(eq_table)
        elements.append(Spacer(1, 0.1 * inch))

    # --- Visitors ---
    visitors = log.get("visitors") or []
    if visitors:
        elements.append(Paragraph("Visitors", section_style))
        vi_data = [["Name", "Company", "Purpose", "Time In", "Time Out"]]
        for v in visitors:
            if isinstance(v, dict):
                vi_data.append(
                    [
                        v.get("name", ""),
                        v.get("company", ""),
                        v.get("purpose", ""),
                        v.get("time_in", ""),
                        v.get("time_out", ""),
                    ]
                )
        vi_table = Table(vi_data, colWidths=[1.3 * inch] * 5)
        vi_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(vi_table)
        elements.append(Spacer(1, 0.1 * inch))

    # --- Notes ---
    notes = log.get("notes")
    if notes:
        elements.append(Paragraph("General Notes", section_style))
        elements.append(Paragraph(notes, normal))
        elements.append(Spacer(1, 0.1 * inch))

    # --- Photos ---
    photos = log.get("photos") or []
    if photos:
        elements.append(Paragraph(f"Photos ({len(photos)})", section_style))
        for p in photos[:20]:  # cap at 20 in PDF
            if isinstance(p, dict):
                caption = p.get("caption") or p.get("file_name") or ""
                fname = p.get("file_name") or p.get("file_path") or ""
                elements.append(Paragraph(f"• {fname}: {caption}", small))

    if not elements:
        elements.append(Paragraph("No data recorded.", normal))

    doc.build(elements)
    return buf.getvalue()
