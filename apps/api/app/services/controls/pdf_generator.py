"""AIA G702/G703 PDF generation using ReportLab.

G702: Application and Certificate for Payment (single-page summary)
G703: Continuation Sheet (multi-page, columns A-I)
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
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


def _format_money(value: Decimal) -> str:
    """Format a Decimal as $1,234,567.89 (parentheses for negatives)."""
    if value < 0:
        return f"(${abs(value):,.2f})"
    return f"${value:,.2f}"


def _format_pct(value: Decimal) -> str:
    """Format a Decimal percentage as 95.50%."""
    return f"{value:.2f}%"


def generate_g702_pdf(
    pay_app_data: dict,
    project_name: str,
    contractor_name: str = "",
    architect_name: str = "",
) -> bytes:
    """Generate AIA G702 Application and Certificate for Payment.

    Returns bytes of the PDF content.
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
    title_style = ParagraphStyle(
        "G702Title",
        parent=styles["Title"],
        fontSize=14,
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "G702Subtitle",
        parent=styles["Normal"],
        fontSize=9,
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    story = []

    # --- Header ---
    story.append(Paragraph("AIA Document G702", title_style))
    story.append(Paragraph("APPLICATION AND CERTIFICATE FOR PAYMENT", subtitle_style))
    story.append(Spacer(1, 6))

    # --- Project Info ---
    app_num = pay_app_data.get("application_number", "")
    period_to = pay_app_data.get("period_to", "")
    info_data = [
        ["PROJECT:", project_name, "APPLICATION NO:", str(app_num)],
        ["CONTRACTOR:", contractor_name, "PERIOD TO:", str(period_to)],
        ["ARCHITECT:", architect_name, "", ""],
    ]
    info_table = Table(info_data, colWidths=[1.2 * inch, 2.8 * inch, 1.4 * inch, 1.6 * inch])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 16))

    # --- Summary Table (Lines 1-9) ---
    original = Decimal(str(pay_app_data.get("original_contract_sum", "0")))
    net_change = Decimal(str(pay_app_data.get("net_change_by_cos", "0")))
    contract_to_date = Decimal(str(pay_app_data.get("contract_sum_to_date", "0")))
    total_completed = Decimal(str(pay_app_data.get("total_completed_and_stored", "0")))
    ret_work = Decimal(str(pay_app_data.get("retainage_work_completed", "0")))
    ret_stored = Decimal(str(pay_app_data.get("retainage_stored_materials", "0")))
    total_ret = Decimal(str(pay_app_data.get("total_retainage", "0")))
    earned_less_ret = Decimal(str(pay_app_data.get("total_earned_less_retainage", "0")))
    prev_certs = Decimal(str(pay_app_data.get("less_previous_certificates", "0")))
    current_due = Decimal(str(pay_app_data.get("current_payment_due", "0")))
    balance = Decimal(str(pay_app_data.get("balance_to_finish_including_retainage", "0")))
    ret_pct = pay_app_data.get("retainage_pct", Decimal("10"))

    summary_data = [
        ["", "CONTRACTOR'S APPLICATION FOR PAYMENT", ""],
        ["1.", "ORIGINAL CONTRACT SUM", _format_money(original)],
        ["2.", "Net change by Change Orders", _format_money(net_change)],
        ["3.", "CONTRACT SUM TO DATE (Line 1 + 2)", _format_money(contract_to_date)],
        ["4.", "TOTAL COMPLETED & STORED TO DATE", _format_money(total_completed)],
        ["", "(Column G on G703)", ""],
        [
            "5.",
            f"RETAINAGE\n"
            f"  a. {ret_pct}% of Completed Work {_format_money(ret_work)}\n"
            f"  b. {ret_pct}% of Stored Material {_format_money(ret_stored)}",
            _format_money(total_ret),
        ],
        ["6.", "TOTAL EARNED LESS RETAINAGE", _format_money(earned_less_ret)],
        ["", "(Line 4 Less Line 5 Total)", ""],
        ["7.", "LESS PREVIOUS CERTIFICATES FOR PAYMENT", _format_money(prev_certs)],
        ["", "(Line 6 from prior Certificate)", ""],
        ["8.", "CURRENT PAYMENT DUE", _format_money(current_due)],
        ["9.", "BALANCE TO FINISH, INCLUDING RETAINAGE", _format_money(balance)],
        ["", "(Line 3 less Line 4, plus Line 5 Total)", ""],
    ]

    summary_table = Table(
        summary_data,
        colWidths=[0.4 * inch, 4.2 * inch, 2.0 * inch],
    )
    summary_table.setStyle(
        TableStyle(
            [
                # Header row
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("SPAN", (1, 0), (2, 0)),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                # All rows
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                # Line number column bold
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                # Key totals bold
                ("FONTNAME", (1, 3), (2, 3), "Helvetica-Bold"),  # Line 3
                ("FONTNAME", (1, 11), (2, 11), "Helvetica-Bold"),  # Line 8
                # Borders around key amounts
                ("LINEABOVE", (2, 11), (2, 11), 1, colors.black),  # above current payment
                ("LINEBELOW", (2, 11), (2, 11), 2, colors.black),  # double-underline
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 24))

    # --- Signature blocks ---
    sig_data = [
        ["CONTRACTOR:", "_" * 40, "DATE:", "_" * 20],
        ["", "", "", ""],
        ["ARCHITECT:", "_" * 40, "DATE:", "_" * 20],
    ]
    sig_table = Table(sig_data, colWidths=[1.2 * inch, 2.8 * inch, 0.8 * inch, 2.2 * inch])
    sig_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(sig_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_g703_pdf(
    line_items: list[dict],
    pay_app_data: dict,
    project_name: str,
) -> bytes:
    """Generate AIA G703 Continuation Sheet.

    Columns A-I with auto-pagination. Totals row on last page.
    Returns bytes of the PDF content.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "G703Title",
        parent=styles["Title"],
        fontSize=12,
        spaceAfter=4,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "G703Subtitle",
        parent=styles["Normal"],
        fontSize=8,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "G703Cell",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
    )
    cell_right = ParagraphStyle(
        "G703CellRight",
        parent=cell_style,
        alignment=TA_RIGHT,
    )

    story = []

    # Header
    app_num = pay_app_data.get("application_number", "")
    period_to = pay_app_data.get("period_to", "")
    story.append(Paragraph("AIA Document G703 - CONTINUATION SHEET", title_style))
    story.append(
        Paragraph(
            f"Project: {project_name} | Application No: {app_num} | Period To: {period_to}",
            subtitle_style,
        )
    )

    # Column headers
    col_widths = [
        0.5 * inch,  # A: Item No
        2.5 * inch,  # B: Description
        1.1 * inch,  # C: Scheduled Value
        1.1 * inch,  # D: Previous
        1.1 * inch,  # E: This Period
        1.0 * inch,  # F: Stored
        1.1 * inch,  # G: Total
        0.7 * inch,  # H: %
        1.1 * inch,  # I: Balance
    ]

    headers = [
        Paragraph("<b>A</b><br/>ITEM<br/>NO.", cell_style),
        Paragraph("<b>B</b><br/>DESCRIPTION OF WORK", cell_style),
        Paragraph("<b>C</b><br/>SCHEDULED<br/>VALUE", cell_right),
        Paragraph("<b>D</b><br/>WORK COMPLETED<br/>FROM PREVIOUS<br/>APPLICATION", cell_right),
        Paragraph("<b>E</b><br/>WORK COMPLETED<br/>THIS PERIOD", cell_right),
        Paragraph("<b>F</b><br/>MATERIALS<br/>PRESENTLY<br/>STORED", cell_right),
        Paragraph("<b>G</b><br/>TOTAL<br/>COMPLETED<br/>AND STORED<br/>(D+E+F)", cell_right),
        Paragraph("<b>H</b><br/>%<br/>(G/C)", cell_right),
        Paragraph("<b>I</b><br/>BALANCE<br/>TO FINISH<br/>(C-G)", cell_right),
    ]

    # Build data rows
    table_data = [headers]
    total_c = total_d = total_e = total_f = total_g = total_i = Decimal("0")

    for li in line_items:
        c = Decimal(str(li.get("scheduled_value", "0")))
        d = Decimal(str(li.get("work_completed_previous", "0")))
        e = Decimal(str(li.get("work_completed_this_period", "0")))
        f = Decimal(str(li.get("materials_presently_stored", "0")))
        g = Decimal(str(li.get("total_completed_and_stored", "0")))
        h = Decimal(str(li.get("percent_complete", "0")))
        i_val = Decimal(str(li.get("balance_to_finish", "0")))

        total_c += c
        total_d += d
        total_e += e
        total_f += f
        total_g += g
        total_i += i_val

        row = [
            Paragraph(str(li.get("item_number", "")), cell_style),
            Paragraph(str(li.get("description_of_work", "")), cell_style),
            Paragraph(_format_money(c), cell_right),
            Paragraph(_format_money(d), cell_right),
            Paragraph(_format_money(e), cell_right),
            Paragraph(_format_money(f), cell_right),
            Paragraph(_format_money(g), cell_right),
            Paragraph(_format_pct(h), cell_right),
            Paragraph(_format_money(i_val), cell_right),
        ]
        table_data.append(row)

    # Totals row
    total_pct = (total_g / total_c * Decimal("100")) if total_c else Decimal("0")
    totals_row = [
        Paragraph("", cell_style),
        Paragraph("<b>GRAND TOTAL</b>", cell_style),
        Paragraph(f"<b>{_format_money(total_c)}</b>", cell_right),
        Paragraph(f"<b>{_format_money(total_d)}</b>", cell_right),
        Paragraph(f"<b>{_format_money(total_e)}</b>", cell_right),
        Paragraph(f"<b>{_format_money(total_f)}</b>", cell_right),
        Paragraph(f"<b>{_format_money(total_g)}</b>", cell_right),
        Paragraph(f"<b>{_format_pct(total_pct)}</b>", cell_right),
        Paragraph(f"<b>{_format_money(total_i)}</b>", cell_right),
    ]
    table_data.append(totals_row)

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                # Header row
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.85, 0.85, 0.85)),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                # Grid
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                # Data rows
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                # Totals row
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.black),
                ("BACKGROUND", (0, -1), (-1, -1), colors.Color(0.92, 0.92, 0.92)),
                # Alternating row shading
                *[
                    ("BACKGROUND", (0, r), (-1, r), colors.Color(0.97, 0.97, 0.97))
                    for r in range(2, len(table_data) - 1, 2)
                ],
            ]
        )
    )

    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
