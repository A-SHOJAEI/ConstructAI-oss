"""
Upload synthetic specification PDFs to MinIO and create document + chunk records.

Creates 5 specification documents:
  - Division 03: Cast-in-Place Concrete
  - Division 05: Structural Steel
  - Division 07: Thermal & Moisture Protection
  - Division 09: Finishes
  - Division 26: Electrical

Each document gets 10 pre-embedded chunks with realistic text content
and random unit-vector embeddings (1024-dim, matching Voyage AI output size).
"""
import hashlib
import random
from pathlib import Path

import numpy as np

from app.database import async_session
from app.models import Document, DocumentChunk, DocumentEmbedding

random.seed(42)
np.random.seed(42)

# Spec metadata: (filename, title, csi_division, page_count, discipline)
SPECS = [
    ("spec_033000_concrete.pdf", "Section 03 30 00 - Cast-in-Place Concrete",
     "03", 12, "structural"),
    ("spec_051200_steel.pdf", "Section 05 12 00 - Structural Steel Framing",
     "05", 10, "structural"),
    ("spec_075200_membrane_roofing.pdf", "Section 07 52 00 - Modified Bituminous Membrane Roofing",
     "07", 9, "architectural"),
    ("spec_092900_gypsum_board.pdf", "Section 09 29 00 - Gypsum Board",
     "09", 8, "architectural"),
    ("spec_260500_electrical.pdf", "Section 26 05 00 - Common Work Results for Electrical",
     "26", 11, "mep"),
]

# Representative chunk content per division (10 chunks each)
CHUNK_CONTENT = {
    "03": [
        "1.01 REFERENCES\nA. ACI 301 - Specifications for Structural Concrete\nB. ACI 318 - Building Code Requirements for Structural Concrete\nC. ASTM C31 - Practice for Making and Curing Concrete Test Specimens in the Field\nD. ASTM C39 - Test Method for Compressive Strength of Cylindrical Concrete Specimens\nE. ASTM C94 - Specification for Ready-Mixed Concrete",
        "1.02 SUBMITTALS\nA. Mix design submittals for each concrete class, including 28-day compressive strength test results from an independent testing laboratory.\nB. Material certificates for cement, aggregates, admixtures, and reinforcing steel.\nC. Concrete placement plan for each major pour, including sequence, joint locations, and curing method.",
        "1.03 QUALITY ASSURANCE\nA. Concrete supplier shall be certified by the National Ready Mixed Concrete Association (NRMCA).\nB. Testing laboratory shall be accredited per ASTM C1077.\nC. Finisher qualification: minimum 5 years experience on similar commercial projects.",
        "2.01 CONCRETE MIX DESIGN\nClass A: f'c 5,000 PSI, slump 4 +/- 1 in, air 5-7%, max w/c 0.40. Application: foundations, slabs-on-grade.\nClass B: f'c 4,000 PSI, slump 4 +/- 1 in, air 5-7%, max w/c 0.45. Application: elevated slabs, beams.\nClass C: f'c 6,000 PSI, slump 4 +/- 1 in, air 5-7%, max w/c 0.35. Application: columns, shear walls.",
        "2.02 MATERIALS\nA. Portland Cement: ASTM C150, Type I/II. Manufacturer: Lehigh Hanson or approved equal.\nB. Aggregates: ASTM C33, Size No. 57 coarse aggregate. Fine aggregate: natural sand, FM 2.5-3.0.\nC. Water: Potable, free from oils, acids, and organic matter.\nD. Admixtures: Air-entraining ASTM C260 MasterAir AE 200; Water-reducing ASTM C494 Type A MasterGlenium 7920.",
        "2.03 REINFORCING STEEL\nA. Deformed bars: ASTM A615, Grade 60.\nB. Welded wire reinforcement: ASTM A185.\nC. Tie wire: 16 gauge minimum, black annealed.\nD. Bar supports: CRSI Class 1 for interior exposure, Class 2 for exterior exposure.\nE. Lap splices per ACI 318 Chapter 25.",
        "3.01 PREPARATION\nA. Verify formwork dimensions, alignment, and adequacy of shoring before placing concrete.\nB. Moisten subgrade and forms before placement. Remove standing water.\nC. Confirm reinforcing steel placement with structural engineer prior to pour.",
        "3.02 PLACEMENT\nA. Place concrete within 90 minutes of batching, or within 300 drum revolutions, whichever comes first.\nB. Maximum free-fall height: 5 feet. Use tremie or pump for greater heights.\nC. Consolidate with internal vibrators at 18-inch spacing. Do not use vibrators to move concrete laterally.",
        "3.03 CURING\nA. Begin curing immediately after finishing operations.\nB. Wet cure for minimum 7 days using water-saturated burlap and polyethylene sheeting.\nC. Curing compound alternative: ASTM C309, Type 1, Class B. Apply at manufacturer's recommended rate.",
        "3.04 FIELD QUALITY CONTROL\nA. Sample and test per ASTM C31: minimum one set of 4 cylinders per 50 CY or per day of placement.\nB. Slump test per ASTM C143 for each truckload.\nC. Air content test per ASTM C231 for each truckload.\nD. Compressive strength tests per ASTM C39 at 7 and 28 days.",
    ],
    "05": [
        "1.01 REFERENCES\nA. AISC 360 - Specification for Structural Steel Buildings\nB. AISC 341 - Seismic Provisions for Structural Steel Buildings\nC. AWS D1.1 - Structural Welding Code - Steel\nD. ASTM A992 - Standard Specification for Structural Steel Shapes\nE. RCSC - Specification for Structural Joints Using High-Strength Bolts",
        "1.02 SUBMITTALS\nA. Shop drawings showing member sizes, connection details, bolt patterns, and weld symbols.\nB. Mill certificates for all structural steel shapes and plates.\nC. Welding procedure specifications (WPS) per AWS D1.1.\nD. Bolt installation procedures including pre-installation verification testing.",
        "2.01 STRUCTURAL STEEL\nA. Wide flange shapes: ASTM A992, Fy = 50 ksi.\nB. HSS sections: ASTM A500, Grade C, Fy = 50 ksi.\nC. Plates: ASTM A572, Grade 50.\nD. Angles and channels: ASTM A36, Fy = 36 ksi.\nE. All steel to be domestic (melted and manufactured in USA).",
        "2.02 BOLTS AND CONNECTORS\nA. High-strength bolts: ASTM F3125, Grade A325 or A490.\nB. Anchor rods: ASTM F1554, Grade 55.\nC. Shear studs: ASTM A108, AWS D1.1 Type B.\nD. Direct tension indicators: ASTM F959.\nE. All connections slip-critical unless noted otherwise.",
        "2.03 WELDING MATERIALS\nA. Electrodes: AWS A5.1, E70XX for SMAW; AWS A5.20, E71T-1 for FCAW.\nB. Weld metal: matching or overmatching base metal strength.\nC. Preheat per AWS D1.1 Table 3.2 based on thickness and material grade.",
        "2.04 FIREPROOFING\nA. Spray-applied fire-resistive material (SFRM): UL listed for required rating.\nB. Columns: 2-hour fire rating minimum.\nC. Beams and girders: 1.5-hour fire rating minimum.\nD. Manufacturer: Carboline Pyrocrete 241 or approved equal.",
        "3.01 FABRICATION\nA. Fabricate in accordance with AISC Code of Standard Practice.\nB. Shop connections: bolted or welded per design drawings.\nC. Tolerances: AISC Code of Standard Practice, Chapter 6.\nD. Surface preparation: SSPC-SP6 Commercial Blast Cleaning for painted surfaces.",
        "3.02 ERECTION\nA. Erect in accordance with AISC Code of Standard Practice.\nB. Plumbing tolerance: 1/500 of column height, maximum 1 inch.\nC. Temporary bracing per erection engineer's design.\nD. Do not field weld without approval of structural engineer of record.",
        "3.03 BOLTING\nA. Install bolts in snug-tight condition as minimum.\nB. Slip-critical connections: turn-of-nut method per RCSC specification.\nC. Pre-installation verification testing required for each bolt lot.\nD. Inspection: verify 10% of bolts per connection using calibrated wrench.",
        "3.04 QUALITY CONTROL\nA. Special inspection per IBC Section 1705.\nB. Ultrasonic testing of CJP welds per AWS D1.1.\nC. Visual inspection of all welds per AWS D1.1.\nD. High-strength bolt installation verification per RCSC.",
    ],
    "07": [
        "1.01 REFERENCES\nA. ASTM D4586 - Standard Specification for Asphalt Roof Cement\nB. ASTM D6162 - SBS Modified Bituminous Sheet Materials\nC. ASTM D6163 - APP Modified Bituminous Sheet Materials\nD. FM Global Loss Prevention Data Sheet 1-29\nE. NRCA Roofing Manual: Membrane Roof Systems",
        "1.02 SYSTEM DESCRIPTION\nA. Modified bituminous membrane roofing system with minimum 20-year warranty.\nB. Total system thickness: minimum 240 mils.\nC. Wind uplift rating: FM 1-90 minimum.\nD. Fire classification: UL Class A.",
        "2.01 MEMBRANE\nA. Base sheet: ASTM D6163, Type I, Grade S. Manufacturer: Siplast Paradiene 20 or approved equal.\nB. Cap sheet: ASTM D6162, granule-surfaced. Manufacturer: Siplast Paradiene 30 or approved equal.\nC. Insulation: Polyisocyanurate, ASTM C1289, minimum R-30 total assembly.\nD. Cover board: DensDeck Prime, 1/4-inch minimum.",
        "2.02 FLASHING AND ACCESSORIES\nA. Metal flashing: 24 ga galvanized steel, prefinished.\nB. Membrane flashing: reinforced modified bitumen, self-adhering.\nC. Sealant: polyurethane, ASTM C920, Type S, Grade NS, Class 25.\nD. Drains: cast iron, Zurn Z100 or approved equal.",
        "3.01 INSTALLATION\nA. Apply in accordance with manufacturer's published instructions and FM Global requirements.\nB. Do not apply membrane when ambient temperature is below 40 degrees F.\nC. Torch-applied method: use propane torch with appropriate safety precautions.\nD. Lap seams minimum 4 inches side lap, 6 inches end lap.",
        "3.02 INSULATION\nA. Install in minimum two layers with staggered joints.\nB. Mechanically fasten bottom layer; adhere top layer in hot asphalt or adhesive.\nC. Maximum slope for mechanical fastening: 2:12.\nD. Taper insulation for positive drainage: minimum 1/4 inch per foot.",
        "3.03 WATERPROOFING - BELOW GRADE\nA. Membrane: self-adhering rubberized asphalt, 60 mils minimum thickness.\nB. Manufacturer: Carlisle CCW MiraDRI 860 or approved equal.\nC. Protection board: 1/4-inch semi-rigid fiberglass.\nD. Drainage mat: HDPE composite with filter fabric.",
        "3.04 INSULATION - WALL\nA. Continuous insulation: extruded polystyrene (XPS), ASTM C578, Type IV, minimum R-7.5.\nB. Spray foam insulation: closed-cell polyurethane, 2 lb/cf density, R-6.5 per inch.\nC. Vapor retarder: 6 mil polyethylene, Class I per IRC.",
        "3.05 AIR BARRIER\nA. Fluid-applied air barrier: Carlisle CCW-702 or approved equal.\nB. Apply at 40 mils wet film thickness, 25 mils dry.\nC. Continuity at all joints, penetrations, and transitions.\nD. Air leakage rate: maximum 0.04 cfm/sf at 1.57 psf pressure differential.",
        "3.06 QUALITY CONTROL\nA. Flood test completed roof areas for 48 hours minimum.\nB. Electronic leak detection per ASTM D7877.\nC. Core cuts at 5 locations per 10,000 SF to verify insulation thickness.\nD. Infrared scan after 30 days of service to detect moisture infiltration.",
    ],
    "09": [
        "1.01 REFERENCES\nA. ASTM C36 - Specification for Gypsum Wallboard\nB. ASTM C840 - Application and Finishing of Gypsum Board\nC. ASTM C645 - Steel Studs and Track for Screw Application of Gypsum Board\nD. GA-216 - Application and Finishing of Gypsum Panel Products\nE. UL Fire Resistance Ratings: Design Nos. U419, U465, U411",
        "1.02 SUBMITTALS\nA. Product data sheets for all gypsum board products.\nB. Fire-rated assembly documentation with UL or equivalent listing.\nC. Sound transmission class (STC) test reports for rated assemblies.\nD. Installer qualifications: minimum 3 years experience on commercial projects.",
        "2.01 GYPSUM BOARD\nA. Standard: ASTM C36, 5/8-inch Type X for fire-rated assemblies.\nB. Moisture resistant: ASTM C1396, Type X, for wet areas (toilets, janitor closets).\nC. Abuse resistant: ASTM C1629, Level 3 for corridors and public areas.\nD. Manufacturer: Georgia-Pacific, CertainTeed, or National Gypsum.",
        "2.02 FRAMING\nA. Steel studs: ASTM C645, 20 gauge minimum for non-load bearing, 3-5/8 inch depth.\nB. Steel track: ASTM C645, matching gauge to studs.\nC. Maximum stud spacing: 16 inches o.c. for single-layer 5/8-inch board.\nD. Deflection track at top of partitions for structural movement.",
        "2.03 ACCESSORIES\nA. Joint compound: ASTM C475, setting type for first coat, drying type for finish coats.\nB. Joint tape: paper tape per ASTM C475.\nC. Corner bead: galvanized steel or vinyl, L-bead at exposed edges.\nD. Control joints: zinc or PVC, at maximum 30-foot spacing.",
        "3.01 PAINTING\nA. Interior paint system:\n   1. Primer: one coat latex primer-sealer, Sherwin-Williams ProMar 200 or equal.\n   2. Finish: two coats latex eggshell, Sherwin-Williams ProMar 200 or equal.\nB. Colors: per architect's color schedule.\nC. Minimum dry film thickness: 1.5 mils per coat.",
        "3.02 FLOORING - CARPET\nA. Carpet tile: 24x24 inch modular, solution-dyed nylon, minimum 20 oz face weight.\nB. Manufacturer: Interface, Shaw, or Mohawk.\nC. Adhesive: pressure-sensitive, low VOC per SCAQMD Rule 1168.\nD. Installation: monolithic or quarter-turn pattern per architect.",
        "3.03 FLOORING - LVT\nA. Luxury vinyl tile: minimum 3mm thickness, 20 mil wear layer.\nB. Manufacturer: Armstrong, Mannington, or Tarkett.\nC. Installation: glue-down with manufacturer-recommended adhesive.\nD. Moisture testing: maximum 3 lbs/1000 SF/24 hrs per ASTM F1869.",
        "3.04 TILE\nA. Porcelain tile: ANSI A137.1, water absorption less than 0.5%.\nB. Size: 12x24 inch rectified for walls, 24x24 for floors.\nC. Mortar: ANSI A118.4 or A118.15 for large format.\nD. Grout: ANSI A118.6 or A118.7 polymer-modified unsanded for joints <= 1/8 inch.",
        "3.05 QUALITY CONTROL\nA. Level 4 finish per GA-216 for painted surfaces.\nB. Level 5 finish for critical lighting areas and gloss paint applications.\nC. Sound flanking test after installation per ASTM E336.\nD. Flatness tolerance: 1/8 inch in 10 feet for gypsum board surfaces.",
    ],
    "26": [
        "1.01 REFERENCES\nA. NFPA 70 - National Electrical Code (NEC) 2023 Edition\nB. NFPA 72 - National Fire Alarm and Signaling Code\nC. IEEE C2 - National Electrical Safety Code\nD. UL 67 - Panelboards\nE. UL 891 - Switchboards",
        "1.02 SUBMITTALS\nA. Shop drawings for switchboards, panelboards, and motor control centers.\nB. Load calculations for each panel and switchboard.\nC. Short circuit and coordination study.\nD. Arc flash hazard analysis per IEEE 1584 and NFPA 70E.\nE. Equipment cut sheets with catalog numbers.",
        "2.01 POWER DISTRIBUTION\nA. Main switchboard: 2000A, 480/277V, 3-phase, 4-wire.\nB. Distribution panels: 400A, 480/277V, bolt-on breakers.\nC. Branch panels: 225A, 208/120V, bolt-on breakers.\nD. Manufacturer: Square D, Eaton, or Siemens.\nE. Arc flash labels per NFPA 70E on all equipment.",
        "2.02 CONDUCTORS\nA. Building wire: copper, THHN/THWN-2, 600V rated.\nB. Minimum conductor size: #12 AWG for branch circuits, #10 AWG for 20A circuits over 100 feet.\nC. Feeders: copper for sizes through 1/0 AWG, aluminum for larger sizes with approved connectors.\nD. Color coding: black/red/blue for phases, white for neutral, green for ground.",
        "2.03 RACEWAYS\nA. EMT: ANSI C80.3, for concealed dry locations.\nB. IMC: ANSI C80.6, for exposed and outdoor locations.\nC. Rigid PVC: Schedule 40 for underground, Schedule 80 where exposed.\nD. Cable tray: aluminum, ladder type, 12-inch minimum width.\nE. Minimum conduit size: 3/4-inch trade size.",
        "2.04 LIGHTING\nA. LED fixtures throughout, minimum DLC Premium listed.\nB. Office areas: recessed 2x4 troffer, 4000K, minimum 40 lumens/watt, 0-10V dimming.\nC. Corridors: recessed linear, 3500K, integral emergency battery.\nD. Parking garage: high-bay LED, 5000K, IP65 rated, occupancy sensor integral.\nE. Emergency: unit equipment type with self-diagnostic LED heads.",
        "2.05 FIRE ALARM\nA. Addressable fire alarm system per NFPA 72.\nB. FACP: Notifier NFS2-3030 or approved equal.\nC. Smoke detectors: photoelectric, addressable, with integral sounder base.\nD. Manual pull stations: double-action, addressable, at all required exits.\nE. Notification appliances: horn/strobe, wall-mounted, candela per room size.",
        "3.01 INSTALLATION\nA. Install per NEC and manufacturer's instructions.\nB. All splices in accessible junction boxes, no splices in raceways.\nC. Conductor fill: maximum 40% of conduit area per NEC Chapter 9.\nD. Minimum 12-inch separation between power and low-voltage raceways.",
        "3.02 GROUNDING\nA. Equipment grounding conductors: copper, sized per NEC Table 250.122.\nB. Ground bus in each panelboard and switchboard.\nC. Grounding electrode system per NEC Article 250.\nD. Lightning protection: UL 96A master-labeled system with air terminals and conductors.",
        "3.03 TESTING\nA. Megger test all feeders: minimum 100 megohms at 1000V DC.\nB. Ground resistance: maximum 5 ohms per electrode.\nC. Circuit continuity test on 100% of branch circuits.\nD. Functional test of all fire alarm devices and sequences.\nE. Phase rotation verification on all 3-phase equipment.",
    ],
}


async def seed_documents(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    pm_user_id = ctx["pm_user_id"]
    doc_ids = []

    async with async_session() as db:
        for filename, title, csi_div, page_count, discipline in SPECS:
            s3_key = f"projects/{project_id}/specs/{filename}"
            content_hash = hashlib.sha256(filename.encode()).hexdigest()

            doc = Document(
                project_id=project_id,
                type="specification",
                title=title,
                original_filename=filename,
                csi_division=csi_div,
                discipline=discipline,
                revision="A",
                cde_status="shared",
                s3_key=s3_key,
                file_size_bytes=random.randint(180_000, 450_000),
                content_hash=content_hash,
                page_count=page_count,
                processing_status="complete",
                metadata={"seeded": True, "csi_format": "masterformat_2018"},
                uploaded_by=pm_user_id,
            )
            db.add(doc)
            await db.flush()
            doc_ids.append(str(doc.id))

            # Create 10 chunks per document
            chunks = CHUNK_CONTENT.get(csi_div, CHUNK_CONTENT["03"])
            for idx, content in enumerate(chunks):
                page = (idx // 2) + 1
                section_parts = content.split("\n")[0] if "\n" in content else content[:40]

                chunk = DocumentChunk(
                    document_id=doc.id,
                    chunk_index=idx,
                    content=content,
                    chunk_type="text",
                    page_number=page,
                    section_hierarchy=[title, section_parts],
                    csi_section=f"{csi_div} {(idx + 1):02d} 00",
                    token_count=len(content.split()),
                    metadata={"seeded": True},
                )
                db.add(chunk)
                await db.flush()

                # Create embedding record (actual vector stored via pgvector in production)
                embedding = DocumentEmbedding(
                    chunk_id=chunk.id,
                    model_name="voyage-3-large",
                )
                db.add(embedding)

        await db.commit()

    return {"document_ids": doc_ids}
