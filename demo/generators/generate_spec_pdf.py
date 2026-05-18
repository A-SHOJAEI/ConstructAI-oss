"""
Generate realistic construction specification PDFs using ReportLab.

Each spec follows CSI MasterFormat structure:
- Part 1 - General (references, submittals, quality assurance)
- Part 2 - Products (manufacturers, materials, performance requirements)
- Part 3 - Execution (preparation, installation, field quality control)

Usage:
    python -m demo.generators.generate_spec_pdf [output_dir]
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)
from reportlab.lib import colors


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "normal": base["Normal"],
        "part": ParagraphStyle(
            "PartHeader", parent=base["Heading1"],
            fontSize=14, spaceAfter=8, spaceBefore=16,
        ),
        "section": ParagraphStyle(
            "SectionHeader", parent=base["Heading2"],
            fontSize=12, spaceAfter=6, spaceBefore=12,
        ),
        "body": ParagraphStyle(
            "SpecBody", parent=base["Normal"],
            fontSize=10, spaceAfter=4, leftIndent=24,
        ),
    }


def _header(story, s, section_num, section_title, project="Riverside Mixed-Use Development"):
    story.append(Paragraph(f"SECTION {section_num}", s["title"]))
    story.append(Paragraph(section_title, s["title"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(project, s["normal"]))
    story.append(Paragraph("Project No. RMD-2025-001", s["normal"]))
    story.append(Spacer(1, 0.3 * inch))


def _table(data, col_widths=None):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.Color(0.95, 0.95, 0.95)]),
    ]))
    return t


def _bullets(story, s, items):
    for item in items:
        story.append(Paragraph(f"&bull; {item}", s["body"]))


# ---------------------------------------------------------------------------
# Division 03 - Cast-in-Place Concrete
# ---------------------------------------------------------------------------
def generate_concrete_spec(out: Path) -> Path:
    fp = out / "spec_033000_concrete.pdf"
    doc = SimpleDocTemplate(str(fp), pagesize=letter, topMargin=inch, bottomMargin=inch)
    s = _styles()
    story = []

    _header(story, s, "03 30 00", "CAST-IN-PLACE CONCRETE")

    # Part 1
    story.append(Paragraph("PART 1 - GENERAL", s["part"]))
    story.append(Paragraph("1.01 REFERENCES", s["section"]))
    _bullets(story, s, [
        "ACI 301 - Specifications for Structural Concrete",
        "ACI 318 - Building Code Requirements for Structural Concrete",
        "ASTM C31 - Making and Curing Concrete Test Specimens in the Field",
        "ASTM C39 - Compressive Strength of Cylindrical Concrete Specimens",
        "ASTM C94 - Specification for Ready-Mixed Concrete",
        "ASTM C150 - Specification for Portland Cement",
        "ASTM C260 - Air-Entraining Admixtures for Concrete",
    ])

    story.append(Paragraph("1.02 SUBMITTALS", s["section"]))
    story.append(Paragraph("A. Mix design submittals for each concrete class, including 28-day compressive strength test results from an independent testing laboratory.", s["body"]))
    story.append(Paragraph("B. Material certificates for cement, aggregates, admixtures, and reinforcing steel.", s["body"]))
    story.append(Paragraph("C. Concrete placement plan for each major pour, including sequence, joint locations, and curing method.", s["body"]))

    story.append(Paragraph("1.03 QUALITY ASSURANCE", s["section"]))
    story.append(Paragraph("A. Concrete supplier shall be certified by the National Ready Mixed Concrete Association (NRMCA).", s["body"]))
    story.append(Paragraph("B. Testing laboratory shall be accredited per ASTM C1077.", s["body"]))
    story.append(Paragraph("C. Finisher qualification: minimum 5 years experience on similar commercial projects.", s["body"]))

    story.append(Paragraph("1.04 DELIVERY, STORAGE, AND HANDLING", s["section"]))
    story.append(Paragraph("A. Deliver concrete in truck mixers complying with ASTM C94.", s["body"]))
    story.append(Paragraph("B. Maintain delivery tickets for each load showing batch time, water added, admixtures, and truck number.", s["body"]))
    story.append(Paragraph("C. Reject any concrete that has exceeded 90-minute haul time or 300 drum revolutions.", s["body"]))

    # Part 2
    story.append(PageBreak())
    story.append(Paragraph("PART 2 - PRODUCTS", s["part"]))
    story.append(Paragraph("2.01 CONCRETE MIX DESIGN", s["section"]))

    mix_data = [
        ["Class", "f'c (PSI)", "Slump (in)", "Air", "Max W/C", "Application"],
        ["A", "5,000", "4 +/- 1", "5-7%", "0.40", "Foundations, SOG"],
        ["B", "4,000", "4 +/- 1", "5-7%", "0.45", "Elevated slabs, beams"],
        ["C", "6,000", "4 +/- 1", "5-7%", "0.35", "Columns, shear walls"],
        ["D", "3,000", "6 +/- 1", "4-6%", "0.50", "Fill, non-structural"],
    ]
    story.append(_table(mix_data, [0.6*inch, 0.8*inch, 0.8*inch, 0.7*inch, 0.8*inch, 2.0*inch]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("2.02 MATERIALS", s["section"]))
    story.append(Paragraph("A. Portland Cement: ASTM C150, Type I/II. Manufacturer: Lehigh Hanson or approved equal.", s["body"]))
    story.append(Paragraph("B. Aggregates: ASTM C33, Size No. 57 coarse aggregate. Fine aggregate: natural sand, FM 2.5-3.0.", s["body"]))
    story.append(Paragraph("C. Water: Potable, free from oils, acids, and organic matter.", s["body"]))
    story.append(Paragraph("D. Admixtures: Air-entraining ASTM C260 MasterAir AE 200; Water-reducing ASTM C494 Type A MasterGlenium 7920; Accelerating (cold weather) ASTM C494 Type C.", s["body"]))

    story.append(Paragraph("2.03 REINFORCING STEEL", s["section"]))
    story.append(Paragraph("A. Deformed bars: ASTM A615, Grade 60.", s["body"]))
    story.append(Paragraph("B. Welded wire reinforcement: ASTM A185.", s["body"]))
    story.append(Paragraph("C. Bar supports: CRSI Class 1 interior, Class 2 exterior.", s["body"]))
    story.append(Paragraph("D. Lap splices per ACI 318 Chapter 25.", s["body"]))

    # Part 3
    story.append(PageBreak())
    story.append(Paragraph("PART 3 - EXECUTION", s["part"]))
    story.append(Paragraph("3.01 PREPARATION", s["section"]))
    story.append(Paragraph("A. Verify formwork dimensions, alignment, and adequacy of shoring before placing concrete.", s["body"]))
    story.append(Paragraph("B. Moisten subgrade and forms before placement. Remove standing water.", s["body"]))

    story.append(Paragraph("3.02 PLACEMENT", s["section"]))
    story.append(Paragraph("A. Place concrete within 90 minutes of batching, or within 300 drum revolutions.", s["body"]))
    story.append(Paragraph("B. Maximum free-fall height: 5 feet. Use tremie or pump for greater heights.", s["body"]))
    story.append(Paragraph("C. Consolidate with internal vibrators at 18-inch spacing.", s["body"]))
    story.append(Paragraph("D. Cold weather: maintain concrete temperature above 50 deg F for minimum 7 days.", s["body"]))

    story.append(Paragraph("3.03 CURING", s["section"]))
    story.append(Paragraph("A. Begin curing immediately after finishing operations.", s["body"]))
    story.append(Paragraph("B. Wet cure for minimum 7 days using water-saturated burlap and polyethylene sheeting.", s["body"]))
    story.append(Paragraph("C. Curing compound alternative: ASTM C309, Type 1, Class B.", s["body"]))

    story.append(Paragraph("3.04 FIELD QUALITY CONTROL", s["section"]))
    story.append(Paragraph("A. Sample and test per ASTM C31: minimum one set of 4 cylinders per 50 CY or per day of placement.", s["body"]))
    story.append(Paragraph("B. Slump test per ASTM C143 for each truckload.", s["body"]))
    story.append(Paragraph("C. Air content test per ASTM C231 for each truckload.", s["body"]))
    story.append(Paragraph("D. Compressive strength tests per ASTM C39 at 7 and 28 days.", s["body"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("END OF SECTION 03 30 00", s["normal"]))

    doc.build(story)
    return fp


# ---------------------------------------------------------------------------
# Division 05 - Structural Steel Framing
# ---------------------------------------------------------------------------
def generate_steel_spec(out: Path) -> Path:
    fp = out / "spec_051200_steel.pdf"
    doc = SimpleDocTemplate(str(fp), pagesize=letter, topMargin=inch, bottomMargin=inch)
    s = _styles()
    story = []

    _header(story, s, "05 12 00", "STRUCTURAL STEEL FRAMING")

    # Part 1
    story.append(Paragraph("PART 1 - GENERAL", s["part"]))
    story.append(Paragraph("1.01 REFERENCES", s["section"]))
    _bullets(story, s, [
        "AISC 360 - Specification for Structural Steel Buildings",
        "AISC 341 - Seismic Provisions for Structural Steel Buildings",
        "AWS D1.1 - Structural Welding Code - Steel",
        "ASTM A992 - Structural Steel Shapes",
        "RCSC - Specification for Structural Joints Using High-Strength Bolts",
        "ASTM A500 - Cold-Formed Welded Structural Hollow Sections",
        "SSPC - Surface Preparation Standards",
    ])

    story.append(Paragraph("1.02 SUBMITTALS", s["section"]))
    story.append(Paragraph("A. Shop drawings showing member sizes, connection details, bolt patterns, and weld symbols.", s["body"]))
    story.append(Paragraph("B. Mill certificates for all structural steel shapes and plates.", s["body"]))
    story.append(Paragraph("C. Welding procedure specifications (WPS) per AWS D1.1.", s["body"]))
    story.append(Paragraph("D. Bolt installation procedures including pre-installation verification testing.", s["body"]))
    story.append(Paragraph("E. Erection plan including crane locations, pick sequences, and temporary bracing.", s["body"]))

    story.append(Paragraph("1.03 QUALITY ASSURANCE", s["section"]))
    story.append(Paragraph("A. Fabricator: AISC certified, Category STD (Standard for Steel Building Structures).", s["body"]))
    story.append(Paragraph("B. Erector: AISC certified, Category CSE (Certified Steel Erector).", s["body"]))
    story.append(Paragraph("C. Welders: AWS certified per D1.1 for required positions and processes.", s["body"]))

    # Part 2
    story.append(PageBreak())
    story.append(Paragraph("PART 2 - PRODUCTS", s["part"]))
    story.append(Paragraph("2.01 STRUCTURAL STEEL", s["section"]))

    steel_data = [
        ["Shape Type", "ASTM Standard", "Grade", "Fy (ksi)", "Fu (ksi)"],
        ["Wide Flange", "A992", "50", "50", "65"],
        ["HSS Round/Rect", "A500", "Grade C", "50", "62"],
        ["Plates", "A572", "Grade 50", "50", "65"],
        ["Angles/Channels", "A36", "-", "36", "58"],
        ["Anchor Rods", "F1554", "Grade 55", "55", "75"],
    ]
    story.append(_table(steel_data, [1.2*inch, 1.2*inch, 1.0*inch, 0.8*inch, 0.8*inch]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("All steel to be domestic (melted and manufactured in USA).", s["body"]))

    story.append(Paragraph("2.02 BOLTS AND CONNECTORS", s["section"]))
    story.append(Paragraph("A. High-strength bolts: ASTM F3125, Grade A325 or A490.", s["body"]))
    story.append(Paragraph("B. Shear studs: ASTM A108, AWS D1.1 Type B, 3/4-inch diameter x 4-1/2 inch.", s["body"]))
    story.append(Paragraph("C. Direct tension indicators: ASTM F959.", s["body"]))
    story.append(Paragraph("D. All connections slip-critical unless noted otherwise on drawings.", s["body"]))

    story.append(Paragraph("2.03 WELDING MATERIALS", s["section"]))
    story.append(Paragraph("A. Electrodes: AWS A5.1 E70XX (SMAW); AWS A5.20 E71T-1 (FCAW).", s["body"]))
    story.append(Paragraph("B. Weld metal: matching or overmatching base metal strength.", s["body"]))
    story.append(Paragraph("C. Preheat per AWS D1.1 Table 3.2 based on thickness and grade.", s["body"]))

    story.append(Paragraph("2.04 FIREPROOFING", s["section"]))
    story.append(Paragraph("A. SFRM: spray-applied fire-resistive material, UL listed.", s["body"]))
    story.append(Paragraph("B. Columns: 2-hour fire rating. Beams and girders: 1.5-hour fire rating.", s["body"]))
    story.append(Paragraph("C. Manufacturer: Carboline Pyrocrete 241 or approved equal.", s["body"]))
    story.append(Paragraph("D. Density: minimum 15 pcf for columns, 12 pcf for beams.", s["body"]))

    story.append(Paragraph("2.05 SHOP PRIMER", s["section"]))
    story.append(Paragraph("A. Surface preparation: SSPC-SP6 Commercial Blast Cleaning.", s["body"]))
    story.append(Paragraph("B. Primer: organic zinc-rich per SSPC Paint 20, 3.0 mils DFT.", s["body"]))
    story.append(Paragraph("C. Do not prime surfaces to receive SFRM or concrete.", s["body"]))

    # Part 3
    story.append(PageBreak())
    story.append(Paragraph("PART 3 - EXECUTION", s["part"]))
    story.append(Paragraph("3.01 FABRICATION", s["section"]))
    story.append(Paragraph("A. Fabricate per AISC Code of Standard Practice.", s["body"]))
    story.append(Paragraph("B. Tolerances: AISC Code of Standard Practice, Chapter 6.", s["body"]))
    story.append(Paragraph("C. Mill scale removal at faying surfaces of slip-critical connections.", s["body"]))

    story.append(Paragraph("3.02 ERECTION", s["section"]))
    story.append(Paragraph("A. Erect per AISC Code of Standard Practice.", s["body"]))
    story.append(Paragraph("B. Plumbing tolerance: 1/500 of column height, maximum 1 inch.", s["body"]))
    story.append(Paragraph("C. Temporary bracing per erection engineer's design.", s["body"]))
    story.append(Paragraph("D. Do not field weld without approval of structural engineer of record.", s["body"]))

    story.append(Paragraph("3.03 BOLTING", s["section"]))
    story.append(Paragraph("A. Slip-critical connections: turn-of-nut method per RCSC.", s["body"]))
    story.append(Paragraph("B. Pre-installation verification testing required for each bolt lot.", s["body"]))
    story.append(Paragraph("C. Inspection: verify 10% of bolts per connection using calibrated wrench.", s["body"]))

    story.append(Paragraph("3.04 QUALITY CONTROL", s["section"]))
    story.append(Paragraph("A. Special inspection per IBC Section 1705.", s["body"]))
    story.append(Paragraph("B. Ultrasonic testing of 100% CJP welds per AWS D1.1.", s["body"]))
    story.append(Paragraph("C. Visual inspection of all welds per AWS D1.1.", s["body"]))
    story.append(Paragraph("D. High-strength bolt installation verification per RCSC.", s["body"]))
    story.append(Paragraph("E. Survey control: verify member elevations at each level before proceeding.", s["body"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("END OF SECTION 05 12 00", s["normal"]))

    doc.build(story)
    return fp


# ---------------------------------------------------------------------------
# Division 07 - Modified Bituminous Membrane Roofing
# ---------------------------------------------------------------------------
def generate_envelope_spec(out: Path) -> Path:
    fp = out / "spec_075200_membrane_roofing.pdf"
    doc = SimpleDocTemplate(str(fp), pagesize=letter, topMargin=inch, bottomMargin=inch)
    s = _styles()
    story = []

    _header(story, s, "07 52 00", "MODIFIED BITUMINOUS MEMBRANE ROOFING")

    # Part 1
    story.append(Paragraph("PART 1 - GENERAL", s["part"]))
    story.append(Paragraph("1.01 REFERENCES", s["section"]))
    _bullets(story, s, [
        "ASTM D4586 - Asphalt Roof Cement",
        "ASTM D6162 - SBS Modified Bituminous Sheet Materials",
        "ASTM D6163 - APP Modified Bituminous Sheet Materials",
        "FM Global Loss Prevention Data Sheet 1-29",
        "NRCA Roofing Manual: Membrane Roof Systems",
        "ASTM C1289 - Faced Rigid Cellular Polyisocyanurate Thermal Insulation Board",
    ])

    story.append(Paragraph("1.02 SYSTEM DESCRIPTION", s["section"]))
    story.append(Paragraph("A. Modified bituminous membrane roofing system with minimum 20-year warranty.", s["body"]))
    story.append(Paragraph("B. Total system thickness: minimum 240 mils.", s["body"]))
    story.append(Paragraph("C. Wind uplift rating: FM 1-90 minimum.", s["body"]))
    story.append(Paragraph("D. Fire classification: UL Class A.", s["body"]))

    story.append(Paragraph("1.03 WARRANTY", s["section"]))
    story.append(Paragraph("A. Manufacturer's 20-year NDL (No Dollar Limit) warranty including materials and labor.", s["body"]))
    story.append(Paragraph("B. Warranty to cover leaks and material defects.", s["body"]))
    story.append(Paragraph("C. Installer must be manufacturer-approved applicator.", s["body"]))

    # Part 2
    story.append(PageBreak())
    story.append(Paragraph("PART 2 - PRODUCTS", s["part"]))
    story.append(Paragraph("2.01 MEMBRANE", s["section"]))
    story.append(Paragraph("A. Base sheet: ASTM D6163, Type I, Grade S. Siplast Paradiene 20 or approved equal.", s["body"]))
    story.append(Paragraph("B. Cap sheet: ASTM D6162, granule-surfaced. Siplast Paradiene 30 or approved equal.", s["body"]))

    story.append(Paragraph("2.02 INSULATION", s["section"]))
    insulation_data = [
        ["Layer", "Material", "Thickness", "R-Value", "Standard"],
        ["Bottom", "Polyiso", "3 inch", "R-17.4", "ASTM C1289"],
        ["Top", "Polyiso", "2.5 inch", "R-14.5", "ASTM C1289"],
        ["Cover Board", "DensDeck Prime", "1/4 inch", "N/A", "ASTM C1177"],
    ]
    story.append(_table(insulation_data, [0.8*inch, 1.2*inch, 1.0*inch, 0.8*inch, 1.2*inch]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Total assembly R-value: minimum R-30.", s["body"]))

    story.append(Paragraph("2.03 FLASHING AND ACCESSORIES", s["section"]))
    story.append(Paragraph("A. Metal flashing: 24 ga galvanized steel, prefinished.", s["body"]))
    story.append(Paragraph("B. Membrane flashing: reinforced modified bitumen, self-adhering.", s["body"]))
    story.append(Paragraph("C. Sealant: polyurethane, ASTM C920, Type S, Grade NS, Class 25.", s["body"]))
    story.append(Paragraph("D. Drains: cast iron, Zurn Z100 or approved equal, 4-inch diameter.", s["body"]))

    # Part 3
    story.append(PageBreak())
    story.append(Paragraph("PART 3 - EXECUTION", s["part"]))
    story.append(Paragraph("3.01 EXAMINATION", s["section"]))
    story.append(Paragraph("A. Verify deck is clean, dry, and free of projections.", s["body"]))
    story.append(Paragraph("B. Confirm positive drainage slope: minimum 1/4 inch per foot.", s["body"]))

    story.append(Paragraph("3.02 INSULATION INSTALLATION", s["section"]))
    story.append(Paragraph("A. Install in minimum two layers with staggered joints.", s["body"]))
    story.append(Paragraph("B. Mechanically fasten bottom layer; adhere top layer.", s["body"]))
    story.append(Paragraph("C. Taper insulation for positive drainage where required.", s["body"]))

    story.append(Paragraph("3.03 MEMBRANE APPLICATION", s["section"]))
    story.append(Paragraph("A. Apply per manufacturer's instructions and FM Global requirements.", s["body"]))
    story.append(Paragraph("B. Do not apply when ambient temperature below 40 deg F.", s["body"]))
    story.append(Paragraph("C. Torch-applied method with appropriate safety precautions.", s["body"]))
    story.append(Paragraph("D. Lap seams: minimum 4 inches side lap, 6 inches end lap.", s["body"]))

    story.append(Paragraph("3.04 QUALITY CONTROL", s["section"]))
    story.append(Paragraph("A. Flood test completed areas for 48 hours minimum.", s["body"]))
    story.append(Paragraph("B. Electronic leak detection per ASTM D7877.", s["body"]))
    story.append(Paragraph("C. Core cuts at 5 locations per 10,000 SF.", s["body"]))
    story.append(Paragraph("D. Infrared scan after 30 days to detect moisture infiltration.", s["body"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("END OF SECTION 07 52 00", s["normal"]))

    doc.build(story)
    return fp


# ---------------------------------------------------------------------------
# Division 09 - Gypsum Board
# ---------------------------------------------------------------------------
def generate_finishes_spec(out: Path) -> Path:
    fp = out / "spec_092900_gypsum_board.pdf"
    doc = SimpleDocTemplate(str(fp), pagesize=letter, topMargin=inch, bottomMargin=inch)
    s = _styles()
    story = []

    _header(story, s, "09 29 00", "GYPSUM BOARD")

    # Part 1
    story.append(Paragraph("PART 1 - GENERAL", s["part"]))
    story.append(Paragraph("1.01 REFERENCES", s["section"]))
    _bullets(story, s, [
        "ASTM C36 - Specification for Gypsum Wallboard",
        "ASTM C840 - Application and Finishing of Gypsum Board",
        "ASTM C645 - Steel Studs and Track for Screw Application",
        "GA-216 - Application and Finishing of Gypsum Panel Products",
        "UL Fire Resistance Ratings: Design Nos. U419, U465, U411",
        "ASTM C1396 - Gypsum Board",
        "ASTM C1629 - Abuse-Resistant Gypsum Board",
    ])

    story.append(Paragraph("1.02 SUBMITTALS", s["section"]))
    story.append(Paragraph("A. Product data sheets for all gypsum board products.", s["body"]))
    story.append(Paragraph("B. Fire-rated assembly documentation with UL listing.", s["body"]))
    story.append(Paragraph("C. Sound transmission class (STC) test reports for rated assemblies.", s["body"]))
    story.append(Paragraph("D. Installer qualifications: minimum 3 years commercial experience.", s["body"]))

    story.append(Paragraph("1.03 FIRE-RATED ASSEMBLIES", s["section"]))
    fire_data = [
        ["UL Design", "Rating", "Assembly Description", "STC"],
        ["U419", "1-Hour", "3-5/8 steel stud, 1-layer 5/8 Type X ea. side", "45"],
        ["U465", "2-Hour", "3-5/8 steel stud, 2-layer 5/8 Type X ea. side", "55"],
        ["U411", "1-Hour", "Floor-ceiling, 1-1/2 metal deck, 5/8 Type X ceiling", "50"],
    ]
    story.append(_table(fire_data, [0.9*inch, 0.8*inch, 2.8*inch, 0.5*inch]))
    story.append(Spacer(1, 0.15 * inch))

    # Part 2
    story.append(PageBreak())
    story.append(Paragraph("PART 2 - PRODUCTS", s["part"]))
    story.append(Paragraph("2.01 GYPSUM BOARD", s["section"]))
    story.append(Paragraph("A. Standard: ASTM C36, 5/8-inch Type X for fire-rated assemblies.", s["body"]))
    story.append(Paragraph("B. Moisture resistant: ASTM C1396, Type X, for wet areas.", s["body"]))
    story.append(Paragraph("C. Abuse resistant: ASTM C1629, Level 3 for corridors and public areas.", s["body"]))
    story.append(Paragraph("D. Manufacturer: Georgia-Pacific, CertainTeed, or National Gypsum.", s["body"]))

    story.append(Paragraph("2.02 FRAMING", s["section"]))
    story.append(Paragraph("A. Steel studs: ASTM C645, 20 gauge min, 3-5/8 inch depth.", s["body"]))
    story.append(Paragraph("B. Maximum spacing: 16 inches o.c. for single-layer 5/8-inch.", s["body"]))
    story.append(Paragraph("C. Deflection track at top of partitions for structural movement.", s["body"]))
    story.append(Paragraph("D. Resilient channel: 25 gauge, for STC-rated assemblies.", s["body"]))

    story.append(Paragraph("2.03 ACCESSORIES", s["section"]))
    story.append(Paragraph("A. Joint compound: ASTM C475, setting type first coat, drying type finish.", s["body"]))
    story.append(Paragraph("B. Joint tape: paper tape per ASTM C475.", s["body"]))
    story.append(Paragraph("C. Corner bead: galvanized steel or vinyl.", s["body"]))
    story.append(Paragraph("D. Control joints: zinc or PVC, maximum 30-foot spacing.", s["body"]))

    # Part 3
    story.append(PageBreak())
    story.append(Paragraph("PART 3 - EXECUTION", s["part"]))
    story.append(Paragraph("3.01 FRAMING INSTALLATION", s["section"]))
    story.append(Paragraph("A. Install studs plumb and true, secure to track at top and bottom.", s["body"]))
    story.append(Paragraph("B. Frame openings with double studs and headers per design.", s["body"]))
    story.append(Paragraph("C. Install backing for wall-mounted items (grab bars, cabinets).", s["body"]))

    story.append(Paragraph("3.02 BOARD APPLICATION", s["section"]))
    story.append(Paragraph("A. Install board with long dimension perpendicular to framing.", s["body"]))
    story.append(Paragraph("B. Screw spacing: 12 inches o.c. field, 8 inches o.c. edges.", s["body"]))
    story.append(Paragraph("C. Stagger joints between layers in multi-layer assemblies.", s["body"]))
    story.append(Paragraph("D. Leave 1/4-inch gap at floor; fire-stop per assembly requirements.", s["body"]))

    story.append(Paragraph("3.03 FINISHING", s["section"]))
    story.append(Paragraph("A. Level 4 finish per GA-216 for painted surfaces.", s["body"]))
    story.append(Paragraph("B. Level 5 finish for critical lighting and gloss paint.", s["body"]))
    story.append(Paragraph("C. Apply joint tape and three coats of joint compound.", s["body"]))
    story.append(Paragraph("D. Sand smooth between coats; final surface free of tool marks.", s["body"]))

    story.append(Paragraph("3.04 PAINTING", s["section"]))
    story.append(Paragraph("A. Primer: one coat latex primer-sealer, Sherwin-Williams ProMar 200.", s["body"]))
    story.append(Paragraph("B. Finish: two coats latex eggshell, Sherwin-Williams ProMar 200.", s["body"]))
    story.append(Paragraph("C. Colors: per architect's color schedule.", s["body"]))
    story.append(Paragraph("D. Minimum DFT: 1.5 mils per coat.", s["body"]))

    story.append(Paragraph("3.05 QUALITY CONTROL", s["section"]))
    story.append(Paragraph("A. Flatness: 1/8 inch in 10 feet for board surfaces.", s["body"]))
    story.append(Paragraph("B. Sound flanking test after installation per ASTM E336.", s["body"]))
    story.append(Paragraph("C. Fire stopping inspection at all penetrations and joints.", s["body"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("END OF SECTION 09 29 00", s["normal"]))

    doc.build(story)
    return fp


# ---------------------------------------------------------------------------
# Division 26 - Common Work Results for Electrical
# ---------------------------------------------------------------------------
def generate_electrical_spec(out: Path) -> Path:
    fp = out / "spec_260500_electrical.pdf"
    doc = SimpleDocTemplate(str(fp), pagesize=letter, topMargin=inch, bottomMargin=inch)
    s = _styles()
    story = []

    _header(story, s, "26 05 00", "COMMON WORK RESULTS FOR ELECTRICAL")

    # Part 1
    story.append(Paragraph("PART 1 - GENERAL", s["part"]))
    story.append(Paragraph("1.01 REFERENCES", s["section"]))
    _bullets(story, s, [
        "NFPA 70 - National Electrical Code (NEC) 2023 Edition",
        "NFPA 72 - National Fire Alarm and Signaling Code",
        "IEEE C2 - National Electrical Safety Code",
        "UL 67 - Panelboards",
        "UL 891 - Switchboards",
        "IEEE 1584 - Arc-Flash Hazard Calculations",
        "NFPA 70E - Standard for Electrical Safety in the Workplace",
    ])

    story.append(Paragraph("1.02 SUBMITTALS", s["section"]))
    story.append(Paragraph("A. Shop drawings for switchboards, panelboards, and motor control centers.", s["body"]))
    story.append(Paragraph("B. Load calculations for each panel and switchboard.", s["body"]))
    story.append(Paragraph("C. Short circuit and coordination study.", s["body"]))
    story.append(Paragraph("D. Arc flash hazard analysis per IEEE 1584 and NFPA 70E.", s["body"]))
    story.append(Paragraph("E. Equipment cut sheets with catalog numbers.", s["body"]))

    story.append(Paragraph("1.03 QUALITY ASSURANCE", s["section"]))
    story.append(Paragraph("A. Electrical contractor: licensed in Commonwealth of Virginia.", s["body"]))
    story.append(Paragraph("B. All equipment UL listed or labeled.", s["body"]))
    story.append(Paragraph("C. Arc flash labels per NFPA 70E on all equipment rated 50V or more.", s["body"]))

    # Part 2
    story.append(PageBreak())
    story.append(Paragraph("PART 2 - PRODUCTS", s["part"]))
    story.append(Paragraph("2.01 POWER DISTRIBUTION", s["section"]))

    elec_data = [
        ["Equipment", "Rating", "Voltage", "Config", "Manufacturer"],
        ["Main Switchboard", "2000A", "480/277V", "3PH 4W", "Square D / Eaton / Siemens"],
        ["Distribution Panel", "400A", "480/277V", "Bolt-on", "Square D / Eaton / Siemens"],
        ["Branch Panel", "225A", "208/120V", "Bolt-on", "Square D / Eaton / Siemens"],
        ["Transformer", "150 kVA", "480-208/120V", "Dry Type", "Square D / Eaton / Siemens"],
    ]
    story.append(_table(elec_data, [1.2*inch, 0.7*inch, 0.9*inch, 0.8*inch, 2.0*inch]))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("2.02 CONDUCTORS", s["section"]))

    wire_data = [
        ["Application", "Min Size", "Material", "Insulation", "Voltage"],
        ["Branch circuits", "#12 AWG", "Copper", "THHN/THWN-2", "600V"],
        ["20A over 100 ft", "#10 AWG", "Copper", "THHN/THWN-2", "600V"],
        ["Feeders <= 1/0", "Per calc", "Copper", "THHN/THWN-2", "600V"],
        ["Feeders > 1/0", "Per calc", "Aluminum", "XHHW-2", "600V"],
    ]
    story.append(_table(wire_data, [1.2*inch, 0.8*inch, 0.8*inch, 1.2*inch, 0.7*inch]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Color coding: black/red/blue phases, white neutral, green ground.", s["body"]))

    story.append(Paragraph("2.03 RACEWAYS", s["section"]))
    story.append(Paragraph("A. EMT: ANSI C80.3, for concealed dry locations.", s["body"]))
    story.append(Paragraph("B. IMC: ANSI C80.6, for exposed and outdoor locations.", s["body"]))
    story.append(Paragraph("C. Rigid PVC: Schedule 40 underground, Schedule 80 exposed.", s["body"]))
    story.append(Paragraph("D. Cable tray: aluminum, ladder type, 12-inch minimum width.", s["body"]))
    story.append(Paragraph("E. Minimum conduit size: 3/4-inch trade size.", s["body"]))

    story.append(Paragraph("2.04 LIGHTING", s["section"]))
    story.append(Paragraph("A. LED throughout, minimum DLC Premium listed.", s["body"]))
    story.append(Paragraph("B. Office: recessed 2x4 troffer, 4000K, 40+ lumens/watt, 0-10V dimming.", s["body"]))
    story.append(Paragraph("C. Corridors: recessed linear, 3500K, integral emergency battery.", s["body"]))
    story.append(Paragraph("D. Parking: high-bay LED, 5000K, IP65, occupancy sensor.", s["body"]))

    story.append(Paragraph("2.05 FIRE ALARM", s["section"]))
    story.append(Paragraph("A. Addressable fire alarm system per NFPA 72.", s["body"]))
    story.append(Paragraph("B. FACP: Notifier NFS2-3030 or approved equal.", s["body"]))
    story.append(Paragraph("C. Smoke detectors: photoelectric, addressable, integral sounder base.", s["body"]))
    story.append(Paragraph("D. Pull stations: double-action, addressable, at all required exits.", s["body"]))
    story.append(Paragraph("E. Notification: horn/strobe, wall-mounted, candela per room size.", s["body"]))

    # Part 3
    story.append(PageBreak())
    story.append(Paragraph("PART 3 - EXECUTION", s["part"]))
    story.append(Paragraph("3.01 INSTALLATION", s["section"]))
    story.append(Paragraph("A. Install per NEC and manufacturer's instructions.", s["body"]))
    story.append(Paragraph("B. All splices in accessible junction boxes; no splices in raceways.", s["body"]))
    story.append(Paragraph("C. Conductor fill: maximum 40% of conduit area per NEC Chapter 9.", s["body"]))
    story.append(Paragraph("D. Minimum 12-inch separation between power and low-voltage raceways.", s["body"]))

    story.append(Paragraph("3.02 GROUNDING", s["section"]))
    story.append(Paragraph("A. Equipment grounding: copper, sized per NEC Table 250.122.", s["body"]))
    story.append(Paragraph("B. Ground bus in each panelboard and switchboard.", s["body"]))
    story.append(Paragraph("C. Grounding electrode system per NEC Article 250.", s["body"]))
    story.append(Paragraph("D. Lightning protection: UL 96A master-labeled system.", s["body"]))

    story.append(Paragraph("3.03 TESTING AND COMMISSIONING", s["section"]))
    story.append(Paragraph("A. Megger test all feeders: minimum 100 megohms at 1000V DC.", s["body"]))
    story.append(Paragraph("B. Ground resistance: maximum 5 ohms per electrode.", s["body"]))
    story.append(Paragraph("C. Circuit continuity test on 100% of branch circuits.", s["body"]))
    story.append(Paragraph("D. Functional test of all fire alarm devices and sequences.", s["body"]))
    story.append(Paragraph("E. Phase rotation verification on all 3-phase equipment.", s["body"]))
    story.append(Paragraph("F. Thermal scan under load of all panelboard and switchboard connections.", s["body"]))

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph("END OF SECTION 26 05 00", s["normal"]))

    doc.build(story)
    return fp


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_all_specs(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return [
        generate_concrete_spec(output_dir),
        generate_steel_spec(output_dir),
        generate_envelope_spec(output_dir),
        generate_finishes_spec(output_dir),
        generate_electrical_spec(output_dir),
    ]


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/specs")
    files = generate_all_specs(out)
    for f in files:
        print(f"Generated: {f}")
