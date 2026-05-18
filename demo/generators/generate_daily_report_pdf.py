"""
Generate a sample daily construction report PDF using ReportLab.

Usage:
    python -m demo.generators.generate_daily_report_pdf [output_path]
"""
import sys
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors


def generate_daily_report(output_path: Path, report_date: date | None = None) -> Path:
    report_date = report_date or date.today()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(str(output_path), pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    header = ParagraphStyle("Header", parent=styles["Heading1"], fontSize=16, spaceAfter=4)
    sub = ParagraphStyle("Sub", parent=styles["Heading2"], fontSize=12, spaceAfter=4)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, spaceAfter=3)

    story = []

    # Header
    story.append(Paragraph("DAILY CONSTRUCTION REPORT", header))
    story.append(Paragraph("Riverside Mixed-Use Development - RMD-2025-001", body))
    story.append(Paragraph(f"Date: {report_date.strftime('%B %d, %Y')}", body))
    story.append(Paragraph("Prepared by: Mike Rodriguez, Superintendent", body))
    story.append(Spacer(1, 0.2*inch))

    # Weather
    story.append(Paragraph("WEATHER CONDITIONS", sub))
    weather = [
        ["Time", "Temp (F)", "Conditions", "Wind (mph)", "Precip"],
        ["7:00 AM", "38", "Partly Cloudy", "5-10 NW", "None"],
        ["12:00 PM", "48", "Sunny", "8-12 NW", "None"],
        ["5:00 PM", "42", "Clear", "5-8 W", "None"],
    ]
    t = Table(weather, colWidths=[1.0*inch, 0.8*inch, 1.2*inch, 1.0*inch, 0.8*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.15*inch))

    # Workforce
    story.append(Paragraph("WORKFORCE", sub))
    workforce = [
        ["Trade", "Company", "Workers", "Hours"],
        ["Concrete", "BuildRight / River City Concrete", "8", "64"],
        ["Structural Steel", "Iron Mountain Erectors", "6", "48"],
        ["Electrical", "Valley Electric Co.", "5", "40"],
        ["Plumbing", "Blue Ridge Mechanical", "4", "32"],
        ["Drywall", "ProFinish Interiors", "7", "56"],
        ["General Labor", "BuildRight Construction", "12", "96"],
        ["Supervision", "BuildRight Construction", "3", "24"],
        ["TOTAL", "", "45", "360"],
    ]
    t = Table(workforce, colWidths=[1.2*inch, 2.2*inch, 0.8*inch, 0.8*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, -1), (-1, -1), colors.Color(0.9, 0.9, 0.9)),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.15*inch))

    # Work performed
    story.append(Paragraph("WORK PERFORMED", sub))
    work_items = [
        "Structural steel erection Level 2, bays 5-8 completed. All bolted connections torqued and inspected.",
        "Electrical rough-in Level 1 corridors C-1 through C-4. Conduit and junction boxes installed.",
        "Foundation waterproofing south wall completed. Protection board and drainage mat applied.",
        "Concrete on metal deck Level 1 west wing - 85 CY placed, finished, and covered for curing.",
        "Curtain wall anchor embeds installed at Level 3 east and south facades.",
    ]
    for item in work_items:
        story.append(Paragraph(f"- {item}", body))
    story.append(Spacer(1, 0.1*inch))

    # Equipment
    story.append(Paragraph("EQUIPMENT ON SITE", sub))
    equip = [
        ["Equipment", "ID", "Hours", "Status"],
        ["Tower Crane", "EQ-CR01", "9.5", "Operational"],
        ["Excavator", "EQ-EX01", "6.0", "Operational"],
        ["Concrete Pump", "EQ-CP01", "4.5", "Operational"],
        ["Aerial Lift", "EQ-AL01", "3.0", "Operational"],
    ]
    t = Table(equip, colWidths=[1.2*inch, 0.8*inch, 0.8*inch, 1.0*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.15*inch))

    # Safety
    story.append(Paragraph("SAFETY", sub))
    story.append(Paragraph("- Morning safety briefing conducted: 45 attendees", body))
    story.append(Paragraph("- Topic: Fall protection requirements for Level 2+ work", body))
    story.append(Paragraph("- 1 safety observation: Worker without hard hat at north gate (corrected on site)", body))
    story.append(Paragraph("- Zero recordable incidents", body))

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Report approved by: Sarah Chen, Project Manager", body))

    doc.build(story)
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/daily_report.pdf")
    p = generate_daily_report(out)
    print(f"Generated: {p}")
