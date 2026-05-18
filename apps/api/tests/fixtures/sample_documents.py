"""Helper functions to generate sample document files for testing."""

from __future__ import annotations

import csv
import io


def create_sample_pdf() -> bytes:
    """Generate a simple PDF with construction specification content.

    Uses reportlab to create a PDF containing:
    - A title in large font
    - Section headings in medium font
    - Body text about concrete specifications (Section 03 30 00)
    - A simple materials/quantities table

    Returns:
        Raw PDF bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    # Custom styles with different font sizes for heading detection
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=24,
        spaceAfter=20,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading1"],
        fontSize=16,
        spaceAfter=12,
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=11,
        spaceAfter=8,
    )

    story = []

    # Title
    story.append(Paragraph("Test Construction Specification", title_style))
    story.append(Spacer(1, 0.2 * inch))

    # Section heading
    story.append(Paragraph("Section 03 30 00 - Cast-in-Place Concrete", heading_style))
    story.append(Spacer(1, 0.1 * inch))

    # Body paragraphs
    story.append(
        Paragraph(
            "PART 1 - GENERAL. This section includes cast-in-place concrete for "
            "foundations, slabs on grade, elevated structural slabs, and concrete "
            "walls and columns. All work shall comply with ACI 301 and ACI 318.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "PART 2 - PRODUCTS. Cement shall be Portland cement conforming to "
            "ASTM C150, Type I/II, manufactured by LafargeHolcim or approved equal. "
            "Aggregates shall conform to ASTM C33. The concrete mix design shall "
            "achieve a minimum compressive strength of 4,000 psi at 28 days with a "
            "maximum water-cement ratio of 0.45.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "PART 3 - EXECUTION. Perform slump tests per ASTM C143 for each load. "
            "Cast and cure test cylinders per ASTM C31. Test cylinders at 7 and 28 "
            "days per ASTM C39. Place concrete within 90 minutes of batching.",
            body_style,
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    # Table of materials/quantities
    story.append(Paragraph("Materials Schedule", heading_style))
    table_data = [
        ["Material", "Specification", "Quantity"],
        ["Portland Cement", "ASTM C150 Type I/II", "500 tons"],
        ["Coarse Aggregate", "ASTM C33 #57 Stone", "1200 tons"],
        ["Fine Aggregate", "ASTM C33 Natural Sand", "800 tons"],
        ["Admixture", "ASTM C260 Air-Entraining", "200 gal"],
    ]
    table = Table(table_data, colWidths=[2 * inch, 2.5 * inch, 1.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return buffer.getvalue()


def create_multi_page_pdf(num_pages: int = 3) -> bytes:
    """Generate a multi-page PDF for testing page count extraction.

    Args:
        num_pages: Number of pages to generate.

    Returns:
        Raw PDF bytes.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    for i in range(num_pages):
        story.append(Paragraph(f"Page {i + 1} Content", styles["Title"]))
        story.append(
            Paragraph(
                f"This is page {i + 1} of the test document. "
                "It contains sample construction specification text.",
                styles["Normal"],
            )
        )
        if i < num_pages - 1:
            story.append(PageBreak())

    doc.build(story)
    return buffer.getvalue()


def create_corrupted_pdf() -> bytes:
    """Return bytes that are not a valid PDF.

    Returns:
        Invalid bytes that will cause PDF parsers to fail.
    """
    return b"not a pdf - this is corrupted content that cannot be parsed"


def create_sample_csv() -> bytes:
    """Return a simple CSV with construction task columns.

    Returns:
        UTF-8 encoded CSV bytes.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Task ID", "Task Name", "Start Date", "End Date", "Duration", "Status"])
    writer.writerow(["T-001", "Foundation Excavation", "2025-03-01", "2025-03-15", "10d", "Done"])
    writer.writerow(["T-002", "Rebar Installation", "2025-03-16", "2025-03-25", "8d", "Active"])
    writer.writerow(
        ["T-003", "Concrete Pour - Footings", "2025-03-26", "2025-03-30", "3d", "Pending"]
    )
    writer.writerow(["T-004", "Formwork Stripping", "2025-04-01", "2025-04-05", "3d", "Pending"])
    writer.writerow(["T-005", "Waterproofing", "2025-04-06", "2025-04-12", "5d", "Not Started"])
    return buffer.getvalue().encode("utf-8")
