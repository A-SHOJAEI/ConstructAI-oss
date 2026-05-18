"""Punch list PDF export using ReportLab.

Generates a PDF report grouped by responsible subcontractor with item
details and photo thumbnails.  Suitable for handing to each sub.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


def generate_punch_list_pdf(
    items: list[Any],
    project_name: str = "Project",
    report_date: date | None = None,
) -> bytes:
    """Generate a punch list PDF grouped by responsible subcontractor.

    Each company gets its own section with a header, item table, and
    summary.  Items without a company are grouped under "Unassigned".

    Parameters
    ----------
    items : list
        PunchListItem ORM objects (or dicts with the same keys).
    project_name : str
        Name shown in the report header.
    report_date : date | None
        Date shown on the report; defaults to today.

    Returns
    -------
    bytes
        PDF file content.
    """
    report_date = report_date or date.today()
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PunchTitle", parent=styles["Heading1"], fontSize=14, alignment=TA_CENTER
    )
    subtitle_style = ParagraphStyle(
        "PunchSubtitle", parent=styles["Heading2"], fontSize=11, alignment=TA_LEFT
    )
    normal_style = ParagraphStyle("PunchNormal", parent=styles["Normal"], fontSize=8, leading=10)
    small_style = ParagraphStyle("PunchSmall", parent=styles["Normal"], fontSize=7, leading=9)

    elements: list = []

    # --- Title ---
    elements.append(Paragraph(f"Punch List Report — {project_name}", title_style))
    elements.append(Paragraph(f"Date: {report_date.isoformat()}", normal_style))
    elements.append(Spacer(1, 0.2 * inch))

    # --- Summary stats ---
    total = len(items)
    open_count = sum(1 for i in _iter(items) if _attr(i, "status") == "open")
    in_progress = sum(1 for i in _iter(items) if _attr(i, "status") == "in_progress")
    resolved = sum(1 for i in _iter(items) if _attr(i, "status") == "resolved")
    verified = sum(1 for i in _iter(items) if _attr(i, "status") == "verified")

    summary_data = [
        ["Total Items", "Open", "In Progress", "Resolved", "Verified"],
        [str(total), str(open_count), str(in_progress), str(resolved), str(verified)],
    ]
    summary_table = Table(summary_data, colWidths=[1.4 * inch] * 5)
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 0.3 * inch))

    # --- Group by company ---
    grouped: dict[str, list] = {}
    for item in _iter(items):
        company = _attr(item, "company") or "Unassigned"
        grouped.setdefault(company, []).append(item)

    for company in sorted(grouped.keys()):
        company_items = grouped[company]

        elements.append(Paragraph(f"{company} ({len(company_items)} items)", subtitle_style))
        elements.append(Spacer(1, 0.1 * inch))

        # Table header
        header = [
            "Item #",
            "Description",
            "Location",
            "Priority",
            "Status",
            "Due Date",
            "Drawing Ref",
            "Spec Section",
        ]
        col_widths = [
            0.65 * inch,
            2.2 * inch,
            1.1 * inch,
            0.65 * inch,
            0.7 * inch,
            0.75 * inch,
            0.7 * inch,
            0.75 * inch,
        ]

        data = [header]
        for item in sorted(company_items, key=lambda i: _attr(i, "item_number") or ""):
            data.append(
                [
                    _attr(item, "item_number") or "",
                    Paragraph(str(_attr(item, "description") or ""), small_style),
                    _attr(item, "location") or "",
                    _attr(item, "priority") or "",
                    _attr(item, "status") or "",
                    (_attr(item, "due_date").isoformat() if _attr(item, "due_date") else ""),
                    _attr(item, "drawing_reference") or "",
                    _attr(item, "spec_section") or "",
                ]
            )

        table = Table(data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        # Alternate row shading
        for row_idx in range(1, len(data)):
            if row_idx % 2 == 0:
                style_cmds.append(
                    ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#F1F5F9"))
                )

        table.setStyle(TableStyle(style_cmds))
        elements.append(table)
        elements.append(Spacer(1, 0.15 * inch))

        # Photo section: list items that have photos
        photo_items = [i for i in company_items if _attr(i, "photos")]
        if photo_items:
            elements.append(Paragraph("Photos:", small_style))
            for item in photo_items:
                photos = _attr(item, "photos") or []
                for photo in photos[:4]:  # max 4 photos per item in PDF
                    caption = ""
                    if isinstance(photo, dict):
                        caption = photo.get("caption") or photo.get("file_name") or ""
                    elements.append(
                        Paragraph(
                            f"  {_attr(item, 'item_number')}: {caption}",
                            small_style,
                        )
                    )
            elements.append(Spacer(1, 0.1 * inch))

        elements.append(PageBreak())

    # Remove trailing PageBreak
    if elements and isinstance(elements[-1], PageBreak):
        elements.pop()

    if not elements:
        elements.append(Paragraph("No punch list items.", normal_style))

    doc.build(elements)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Attribute access helpers (work with ORM objects and dicts)
# ---------------------------------------------------------------------------


def _attr(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _iter(items: list) -> list:
    return items if items else []
