"""Seed deeper, more realistic demo content across every major module.

Adds, per demo tenant:
  - 12 additional RFIs across MEP / structural / finishes / civil / safety
    trades (plus the 3 baseline from seed_demo_content.py = 15 total).
  - 6 submittals at different lifecycle states (not_submitted, submitted,
    under_review, approved, revise_resubmit, rejected).
  - 5 daily reports (last 5 working days) with realistic narrative.
  - 3 meeting minutes (preconstruction kickoff, weekly OAC, safety stand-down).
  - 1 punch list with 6 items at varying priorities.
  - 2 PCOs (one approved, one pending review).
  - 3 safety alerts at high / medium / low priority.

All records tagged data_source='public_demo' (where the column exists) so
they never collide with Procore or customer-internal data. Stable UUIDv5
keys for idempotent re-runs.

Run:
    cd apps/api && .venv/bin/python scripts/seed_demo_extras.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import async_session
from app.models.change_order_lifecycle import PotentialChangeOrder
from app.models.communication import (
    RFI,
    DailyReport,
    MeetingMinutes,
    Submittal,
)
from app.models.evm import ChangeOrder
from app.models.field_management import PunchList, PunchListItem
from app.models.organization import Organization
from app.models.project import Project
from app.models.quality import DefectReport, Inspection
from app.models.safety_incident import SafetyAlert
from app.models.user import User
from app.services.rag.retrieval import index_rfi_for_search

NS = uuid.UUID("00000000-0000-0000-0000-000000000004")
TODAY = date.today()


# ===========================================================================
# Extra RFIs — 12 per tenant covering MEP, structural, finishes, civil
# (plus 3 from seed_demo_content.py = 15 total per tenant)
# ===========================================================================

EXTRA_RFIS = [
    # MEP — mechanical, electrical, plumbing
    {
        "subject": "VAV box static pressure setpoint conflict",
        "question": (
            "Drawings M-401 show VAV box VAV-3-2 at 1.5 in. w.c. setpoint, but "
            "spec 23 09 23 calls for 1.0 in. w.c. minimum. Which controls?"
        ),
        "answer": (
            "Spec 23 09 23 paragraph 2.3.B governs: minimum static pressure "
            "setpoint is 1.0 in. w.c. The drawing value is a target for "
            "design-day load. Re-tag VAV-3-2 to 1.0 in. w.c. for commissioning."
        ),
        "spec_section": "23 09 23",
        "drawing_reference": "Sheet M-401",
        "status": "answered",
    },
    {
        "subject": "Branch circuit conductor sizing for kitchen receptacles",
        "question": (
            "Schedule shows 20 A circuits feeding 6 receptacles each. NEC "
            "210.52(B) requires 2 small-appliance branch circuits — does the "
            "current layout satisfy?"
        ),
        "answer": (
            "Confirmed: SAB-1 and SAB-2 both 20 A, 12 AWG copper, GFCI on "
            "first device. Layout meets NEC 210.52(B). No change needed."
        ),
        "spec_section": "26 27 26",
        "drawing_reference": "Sheet E-201",
        "status": "answered",
    },
    {
        "subject": "Domestic hot-water recirculation pump schedule discrepancy",
        "question": (
            "Pump RP-1 listed at 5 gpm at 15 ft head on plumbing schedule "
            "P-001, but cut sheet submittal shows 8 gpm at 22 ft head. Approve "
            "as-submitted or require revision?"
        ),
        "answer": None,
        "spec_section": "22 11 23",
        "drawing_reference": "Sheet P-001",
        "status": "open",
    },
    # Structural
    {
        "subject": "Anchor rod embedment for column base CB-7",
        "question": (
            "Detail 4/S-301 shows 12 in. embedment for 3/4 in. anchor rods at "
            "CB-7. ACI 318 Appendix D requires concrete cone strength check — "
            "is 12 in. adequate for the W14×90 column reaction?"
        ),
        "answer": (
            "Reaction is 42 kips tension. Per ACI 318-19 Section 17.6.2, "
            "12 in. embedment provides cone capacity = 56 kips with 0.65 phi-"
            "factor = 36.4 kips. INADEQUATE. Increase to 18 in. embedment or "
            "add reinforcing bar for tension transfer."
        ),
        "spec_section": "05 12 00",
        "drawing_reference": "Detail 4/S-301",
        "status": "answered",
    },
    {
        "subject": "Slab edge thickening at perimeter wall",
        "question": (
            "Foundation plan shows 12 in. thick edge at perimeter, but "
            "structural notes call for 16 in. minimum at exterior bearing "
            "walls. Revise drawings or accept as-built?"
        ),
        "answer": (
            "Revise drawings. Edge thickening must be 16 in. at all exterior "
            "bearing walls per S-001 Note 3. Issue ASI-007 with corrected "
            "plan; existing thickening at gridline G-1 must be cut and "
            "extended."
        ),
        "spec_section": "03 30 00",
        "drawing_reference": "Sheet S-101",
        "status": "answered",
    },
    # Finishes
    {
        "subject": "Tile substrate moisture content limits",
        "question": (
            "Spec 09 30 13 requires < 4% MVER before thinset application. "
            "Recent calcium chloride test showed 5.2% on slab area B. Acceptable "
            "with vapor retarder, or require additional dry time?"
        ),
        "answer": (
            "Reject. 5.2% MVER exceeds spec. Options: (1) extend cure / dry "
            "time and re-test in 7 days, or (2) install Schluter-DITRA-HEAT "
            "uncoupling membrane per manufacturer's allowable moisture "
            "tolerance (8% MVER). Submit RFI response with chosen path."
        ),
        "spec_section": "09 30 13",
        "drawing_reference": "Sheet A-401",
        "status": "answered",
    },
    {
        "subject": "Paint system at exterior CMU",
        "question": (
            "Spec 09 91 00 calls for elastomeric coating on CMU. Owner has "
            "asked about a less expensive acrylic system. Performance penalty?"
        ),
        "answer": (
            "Acrylic permits 2x water-vapor transmission and tolerates only "
            "1/16 in. crack bridging vs. 1/8 in. for elastomeric. NOT "
            "RECOMMENDED for our exterior exposure (EPA Climate Zone 3C). "
            "Owner cost-saving could be achieved instead via single-coat "
            "elastomeric on east/north walls only."
        ),
        "spec_section": "09 91 00",
        "drawing_reference": "Spec only",
        "status": "answered",
    },
    # Civil / sitework
    {
        "subject": "Aggregate base course gradation tolerance",
        "question": (
            "Lab report on Stockpile #3 shows 8% passing #200 sieve; spec "
            "limit is 6% maximum per UFGS 32 11 16. Reject the stockpile?"
        ),
        "answer": (
            "Reject for use under structural pavement. Acceptable for "
            "shoulder backfill (limit 12% per same spec section). Direct "
            "stockpile to site sub-base or remove from project."
        ),
        "spec_section": "32 11 16",
        "drawing_reference": "Sheet C-301",
        "status": "answered",
    },
    {
        "subject": "Storm-water vault dewatering protection",
        "question": (
            "Excavation for vault 1B is encountering groundwater 4 ft above "
            "the planned subgrade. Add wellpoint dewatering or change to "
            "open-cut with pumps?"
        ),
        "answer": None,
        "spec_section": "31 23 19",
        "drawing_reference": "Sheet C-501",
        "status": "open",
    },
    # Safety / OSHA
    {
        "subject": "Scaffold tagging frequency at NW elevation",
        "question": (
            "OSHA 1926.451(f)(3) requires daily competent-person inspection "
            "on scaffold above 10 ft. NW elevation has fixed scaffold for 6 "
            "months — does daily inspection still apply, or weekly per "
            "1926.451(f)(7)?"
        ),
        "answer": (
            "Daily inspection still applies — 1926.451(f)(7) (weekly) covers "
            "ladder access and tie-in only, not the structural integrity of "
            "the scaffold itself. Document daily inspection on tag at access "
            "ladder; failure to do so is a serious violation."
        ),
        "spec_section": "01 35 26",
        "drawing_reference": "OSHA 1926.451",
        "status": "answered",
    },
    {
        "subject": "Confined-space entry requirements at sump pit",
        "question": (
            "Sump pit at Mech-100 is 5 ft deep, 6 ft × 6 ft. Permit-required "
            "or non-permit per OSHA 1926.1203? What atmospheric monitoring is "
            "needed?"
        ),
        "answer": (
            "Permit-required. Configuration meets 1926.1202 confined-space "
            "definition; potential for accumulated H2S from drain ties means "
            "hazardous atmosphere. Required: continuous gas monitoring (O2, "
            "CO, H2S, LEL), entry permit with attendant, retrieval system, "
            "and rescue plan. Standby team must be 4 minutes from access "
            "point."
        ),
        "spec_section": "01 35 26",
        "drawing_reference": "OSHA 1926.1203",
        "status": "answered",
    },
    # Procurement / submittal
    {
        "subject": "Glulam beam moisture content certification",
        "question": (
            "Spec 06 18 00 requires APA certification including moisture "
            "content < 16% at delivery. Submitted certificate shows MC at "
            "fabrication, not delivery. Acceptable?"
        ),
        "answer": (
            "Not acceptable. Re-certification required at delivery; supplier "
            "can run portable moisture meter on each piece on-site, log to "
            "submittal record. Reject deliveries above 16% MC; protected "
            "storage at site to reach equilibrium before installation."
        ),
        "spec_section": "06 18 00",
        "drawing_reference": "Sheet S-401",
        "status": "answered",
    },
]


# ===========================================================================
# Submittals — 6 per tenant
# ===========================================================================

SUBMITTALS = [
    {
        "submittal_number": "01-001",
        "title": "Concrete mix design — 4000 psi, normal weight",
        "spec_section": "03 30 00",
        "status": "approved",
        "due_offset_days": -45,
        "description": (
            "Cast-in-place concrete mix design for foundations and slab on "
            "grade. Includes water/cement ratio, admixtures, and aggregate "
            "gradation per UFGS 03 30 00."
        ),
    },
    {
        "submittal_number": "01-002",
        "title": "Reinforcing steel shop drawings",
        "spec_section": "03 21 00",
        "status": "approved",
        "due_offset_days": -30,
        "description": "Bar bend schedules, splice locations, placement plans.",
    },
    {
        "submittal_number": "01-003",
        "title": "Structural steel mill certs and shop drawings",
        "spec_section": "05 12 00",
        "status": "under_review",
        "due_offset_days": 7,
        "description": (
            "ASTM A992 mill certificates plus erection drawings. Reviewer to "
            "verify ASI-007 anchor rod revision is incorporated."
        ),
    },
    {
        "submittal_number": "01-004",
        "title": "VAV terminal units — Trane VV550",
        "spec_section": "23 36 00",
        "status": "revise_resubmit",
        "due_offset_days": 0,
        "description": (
            "Initial submittal received; static pressure setpoint discrepancy "
            "with M-401 (see RFI). Resubmit after RFI resolution."
        ),
    },
    {
        "submittal_number": "01-005",
        "title": "Curtain wall system — Kawneer 1600",
        "spec_section": "08 44 13",
        "status": "submitted",
        "due_offset_days": 14,
        "description": (
            "Mockup pending; structural calculations and weatherproofing " "details for review."
        ),
    },
    {
        "submittal_number": "01-006",
        "title": "Water-source heat pump units",
        "spec_section": "23 81 46",
        "status": "not_submitted",
        "due_offset_days": 21,
        "description": "Awaiting subcontractor coordination meeting.",
    },
]


# ===========================================================================
# Daily reports — narrative for last 5 working days
# ===========================================================================

DAILY_REPORTS = [
    {
        "offset_days": -5,
        "weather": "Sunny, 68°F, light wind",
        "narrative": (
            "Foundation crew continued slab pour at gridlines C-7 to F-7 "
            "(approximately 84 cubic yards). Concrete cured under wet "
            "burlap; cylinders cast for 7-day and 28-day breaks. Steel "
            "delivery arrived 2 hours late but crane was repositioned in "
            "time to maintain erection schedule. No safety incidents."
        ),
        "manpower": 42,
        "deliveries": ["Concrete 84 cy", "Structural steel 22 tons"],
    },
    {
        "offset_days": -4,
        "weather": "Partly cloudy, 71°F",
        "narrative": (
            "Steel erection started at gridline A-1; first column W14×90 "
            "set and plumbed by 09:30. Deck delivery confirmed for 09:00 "
            "tomorrow. Concrete subgrade prep at lower level continued. "
            "OSHA fall-protection training refresher held during AM stand-up."
        ),
        "manpower": 47,
        "deliveries": ["Composite metal deck 18 squares"],
    },
    {
        "offset_days": -3,
        "weather": "Rain showers, 64°F, 8 mph wind",
        "narrative": (
            "Light rain delayed steel erection by 90 minutes. Resumed at "
            "10:45 with priority on infill beams at gridline B. Concrete "
            "scope shifted to interior — bond beam at CMU lift 2. Hot work "
            "permit issued for stair pan welding."
        ),
        "manpower": 38,
        "deliveries": ['CMU 8" 1,200 units'],
    },
    {
        "offset_days": -2,
        "weather": "Clear, 73°F",
        "narrative": (
            "Steel erection complete through gridline E. Punch list walk "
            "with structural engineer identified 3 items: anchor bolt "
            "embedment at CB-7 (RFI pending), bracing connection torque "
            "log incomplete, and one missed welder qualification card. "
            "All being addressed."
        ),
        "manpower": 51,
        "deliveries": ["Bolts/anchors hardware"],
    },
    {
        "offset_days": -1,
        "weather": "Sunny, 76°F",
        "narrative": (
            "MEP rough-in started at lower level mechanical room. Plumber "
            "set sump-pit drain ties pending confined-space permit "
            "approval (RFI in progress). Concrete cylinders broke at 28 "
            "days: 4,420 psi (spec 4,000 psi) — accepted. Schedule float "
            "is +2 days against baseline."
        ),
        "manpower": 55,
        "deliveries": ["DWV pipe + fittings", "Electrical conduit"],
    },
]


# ===========================================================================
# Meeting minutes — 3 per tenant
# ===========================================================================

MEETINGS = [
    {
        "meeting_type": "preconstruction_kickoff",
        "offset_days": -45,
        "title": "Preconstruction kickoff — owner / GC / architect",
        "summary": (
            "Reviewed schedule baseline, contract value, key milestones. "
            "Owner emphasized minimum disruption to adjacent operations. "
            "Architect confirmed shop drawing review turnaround commitment "
            "of 10 working days. Logistics plan accepted with revisions to "
            "delivery window (07:00-15:00 only)."
        ),
        "decisions": [
            {"topic": "Delivery hours", "decision": "07:00 to 15:00 weekdays"},
            {"topic": "Site access", "decision": "Gate B only; ID badging required"},
            {"topic": "Submittal review", "decision": "10 working days max turnaround"},
        ],
        "action_items": [
            {"owner": "GC", "item": "Distribute logistics plan rev 2", "due": -40},
            {"owner": "Architect", "item": "Confirm submittal log structure", "due": -38},
        ],
    },
    {
        "meeting_type": "weekly_oac",
        "offset_days": -7,
        "title": "Weekly OAC — week 6",
        "summary": (
            "Schedule status: +2 days favorable. RFI count: 9 outstanding "
            "(2 critical path). Discussed VAV setpoint discrepancy and anchor "
            "rod embedment items. Owner approved PCO-001 (foundation rebar "
            "uplift) for $24,500. PCO-002 (cure-time accelerator on slab) "
            "submitted, pending review."
        ),
        "decisions": [
            {"topic": "PCO-001", "decision": "Approved at $24,500"},
            {"topic": "Anchor rod RFI", "decision": "Issue ASI-007 within 5 days"},
        ],
        "action_items": [
            {"owner": "Architect", "item": "Issue ASI-007 anchor rod revision", "due": -2},
            {"owner": "GC", "item": "Submit PCO-002 cost backup", "due": 0},
            {"owner": "MEP sub", "item": "Resolve VAV setpoint per RFI-004", "due": 5},
        ],
    },
    {
        "meeting_type": "safety_stand_down",
        "offset_days": -3,
        "title": "Safety stand-down — fall protection refresher",
        "summary": (
            "Quarterly safety stand-down. Reviewed OSHA 1926.501 and "
            "1926.502 requirements with all crews. Demonstrated correct "
            "anchorage of personal fall-arrest system on leading edge. "
            "Three near-misses from prior month reviewed; no recordable "
            "incidents. Site-specific fall-protection plan re-issued."
        ),
        "decisions": [
            {"topic": "Tie-off procedure", "decision": "Mandatory above 6 ft"},
            {"topic": "Re-training", "decision": "All new hires within 1 week of arrival"},
        ],
        "action_items": [
            {"owner": "Safety officer", "item": "Update site-specific FP plan", "due": -1},
            {"owner": "Foremen", "item": "Sign-off on revised plan with crews", "due": 5},
        ],
    },
]


# ===========================================================================
# Punch list — 6 items per tenant under one walkthrough
# ===========================================================================

PUNCH_LIST_NAME = "Substantial completion walk — Building 24"
PUNCH_ITEMS = [
    {
        "item_number": "PL-001",
        "description": "Touch-up paint at door frame DF-201, scuff at base",
        "location": "Level 1 — corridor 1A",
        "category": "finishes",
        "priority": "low",
        "status": "open",
    },
    {
        "item_number": "PL-002",
        "description": "Replace damaged ceiling tile at ACT grid (corner SE)",
        "location": "Level 1 — Conf room 110",
        "category": "finishes",
        "priority": "normal",
        "status": "open",
    },
    {
        "item_number": "PL-003",
        "description": "Re-caulk window mullion W-12 — bead split at sill",
        "location": "Level 2 — west elevation",
        "category": "weatherproofing",
        "priority": "high",
        "status": "in_progress",
    },
    {
        "item_number": "PL-004",
        "description": "Adjust diffuser D-3-4 throw pattern — drafty per occupant",
        "location": "Level 3 — open office",
        "category": "mechanical",
        "priority": "normal",
        "status": "in_progress",
    },
    {
        "item_number": "PL-005",
        "description": "Replace burned-out exit sign LED at stair S-2",
        "location": "Stair S-2 — landing 2",
        "category": "electrical",
        "priority": "high",
        "status": "open",
    },
    {
        "item_number": "PL-006",
        "description": 'Floor levelness exceeds spec at gridline F (3/16" over 10 ft)',
        "location": "Level 1 — gridline F",
        "category": "structural",
        "priority": "high",
        "status": "open",
    },
]


# ===========================================================================
# PCOs — 2 per tenant
# ===========================================================================

PCOS = [
    {
        "pco_number": 1,
        "title": "Foundation rebar uplift — ground stratum revision",
        "description": (
            "Geotech encountered higher water table than borings indicated. "
            "Required additional uplift reinforcement at footings F-3 through "
            "F-9. Material + labor cost backup attached."
        ),
        "change_type": "field_condition",
        "status": "approved",
    },
    {
        "pco_number": 2,
        "title": "Slab cure-time accelerator — owner request",
        "description": (
            "Owner requested 5-day forklift access on slab area C (vs. 7-day "
            "moist cure per spec). Calcium-chloride accelerator added at "
            "1.5% by weight of cement. Cost includes admixture + warranty "
            "extension from supplier."
        ),
        "change_type": "owner_directed",
        "status": "pending_review",
    },
]


# ===========================================================================
# Change Orders — 4 per tenant (executed change orders, downstream of PCOs)
# ===========================================================================

CHANGE_ORDERS = [
    {
        "co_number": "CO-001",
        "title": "Foundation rebar uplift — water table revision",
        "description": (
            "Approved CO covering additional uplift reinforcement at footings "
            "F-3 through F-9 due to higher-than-anticipated water table. "
            "Includes labor, material, and engineering review per geotech "
            "addendum 2."
        ),
        "change_type": "field_condition",
        "status": "approved",
        "cost_impact": Decimal("48750.00"),
        "schedule_impact_days": 3,
        "labor_cost": Decimal("18500.00"),
        "material_cost": Decimal("22000.00"),
        "equipment_cost": Decimal("3250.00"),
        "subcontractor_cost": Decimal("0.00"),
        "overhead_cost": Decimal("5000.00"),
        "markup_pct": Decimal("10.00"),
        "risk_score": Decimal("0.35"),
        "offset_days": -45,
    },
    {
        "co_number": "CO-002",
        "title": "Owner-directed slab cure accelerator",
        "description": (
            "Owner request to reduce slab cure window from 7 days to 5 for "
            "early forklift access on Area C. Calcium-chloride accelerator "
            "added at 1.5% by cement weight. Supplier warranty extension "
            "included."
        ),
        "change_type": "owner_directed",
        "status": "approved",
        "cost_impact": Decimal("12400.00"),
        "schedule_impact_days": -2,
        "labor_cost": Decimal("2400.00"),
        "material_cost": Decimal("8200.00"),
        "equipment_cost": Decimal("0.00"),
        "subcontractor_cost": Decimal("0.00"),
        "overhead_cost": Decimal("1800.00"),
        "markup_pct": Decimal("8.00"),
        "risk_score": Decimal("0.18"),
        "offset_days": -28,
    },
    {
        "co_number": "CO-003",
        "title": "MEP coordination — chilled-water main rerouting",
        "description": (
            "Rerouting of 8-inch chilled-water main around relocated shaft on "
            "Level 4. Includes additional welding, pressure test, insulation, "
            "and BIM model update. Time and material backup attached."
        ),
        "change_type": "design_revision",
        "status": "pending_approval",
        "cost_impact": Decimal("32100.00"),
        "schedule_impact_days": 5,
        "labor_cost": Decimal("14200.00"),
        "material_cost": Decimal("11800.00"),
        "equipment_cost": Decimal("1500.00"),
        "subcontractor_cost": Decimal("0.00"),
        "overhead_cost": Decimal("4600.00"),
        "markup_pct": Decimal("12.00"),
        "risk_score": Decimal("0.55"),
        "offset_days": -10,
    },
    {
        "co_number": "CO-004",
        "title": "Storefront glazing — tempered upgrade per code review",
        "description": (
            "AHJ plan-review comment required tempered glazing in storefront "
            "panels SF-3, SF-4, SF-5 within 24 inches of door swing. Replaced "
            "annealed lites with tempered. No schedule impact (pre-installation)."
        ),
        "change_type": "code_required",
        "status": "approved",
        "cost_impact": Decimal("8950.00"),
        "schedule_impact_days": 0,
        "labor_cost": Decimal("1500.00"),
        "material_cost": Decimal("6800.00"),
        "equipment_cost": Decimal("0.00"),
        "subcontractor_cost": Decimal("0.00"),
        "overhead_cost": Decimal("650.00"),
        "markup_pct": Decimal("10.00"),
        "risk_score": Decimal("0.10"),
        "offset_days": -7,
    },
]


# ===========================================================================
# Safety alerts — 3 per tenant
# ===========================================================================

# Each alert points at a real training image (copied to apps/web/public/safety-demo/)
# and the detection bboxes match what the YOLO labels show in that frame.
# Coordinates are in 1280x720 pixel space — the frontend scales them.
SAFETY_ALERTS = [
    {
        "alert_type": "ppe_violation",
        "priority": "P5_info",
        "description": (
            "Worker entered active zone — PPE compliance verified "
            "(hard hat + hi-vis vest detected)."
        ),
        "confidence": Decimal("0.94"),
        "offset_minutes": -180,
        "osha_reference": "29 CFR 1926.95 — Personal protective equipment (PPE)",
        "frame_s3_key": "/safety-demo/alert-1-ppe-check.jpg",
        "detections": [
            {"class_name": "person", "confidence": 0.94, "bbox": [111, 1, 610, 719]},
            {"class_name": "hard_hat", "confidence": 0.91, "bbox": [295, 1, 255, 143]},
            {"class_name": "safety_vest", "confidence": 0.88, "bbox": [187, 255, 377, 464]},
        ],
    },
    {
        "alert_type": "ppe_violation",
        "priority": "P3_medium",
        "description": "Worker operating power saw — face/eye protection check required.",
        "confidence": Decimal("0.83"),
        "offset_minutes": -540,
        "osha_reference": "29 CFR 1926.95 — Personal protective equipment (PPE)",
        "frame_s3_key": "/safety-demo/alert-2-power-tool.jpg",
        "detections": [
            {"class_name": "person", "confidence": 0.92, "bbox": [12, 44, 489, 594]},
            {"class_name": "hard_hat", "confidence": 0.86, "bbox": [296, 45, 198, 126]},
        ],
    },
    {
        "alert_type": "equipment_proximity",
        "priority": "P4_low",
        "description": (
            "Material staging zone — scaffolding pipe inventory detected. "
            "Workers in PPE present at far edge."
        ),
        "confidence": Decimal("0.89"),
        "offset_minutes": -1440,
        "osha_reference": "29 CFR 1926.451 — Scaffolds (general requirements)",
        "frame_s3_key": "/safety-demo/alert-3-staging-zone.jpg",
        "detections": [
            {"class_name": "scaffolding", "confidence": 0.89, "bbox": [1, 57, 1274, 641]},
            {"class_name": "person", "confidence": 0.78, "bbox": [689, 1, 123, 91]},
            {"class_name": "person", "confidence": 0.74, "bbox": [915, 5, 89, 95]},
        ],
    },
]


# ===========================================================================
# Quality — inspections + AI-classified defect reports
# ===========================================================================
#
# Each defect image is a real CODEBRIM training sample copied to
# /quality-demo/. The ai_classification dict mirrors what the Defect ViT v1.1
# inference service emits at runtime (top-1 label + per-class softmax
# probabilities), so the UI demonstrates the same shape it would render in
# production.

INSPECTIONS = [
    {
        "inspection_type": "structural_concrete",
        "status": "completed",
        "location": "Level 2 — east bay, slab and columns",
        "score": Decimal("78.50"),
        "offset_days": -3,
        "checklist_data": {
            "items_checked": 24,
            "items_passing": 19,
            "items_failing": 5,
            "method": "visual + photographic survey + Defect ViT v1.1",
        },
    },
    {
        "inspection_type": "envelope_facade",
        "status": "completed",
        "location": "South elevation — full height",
        "score": Decimal("85.00"),
        "offset_days": -10,
        "checklist_data": {
            "items_checked": 18,
            "items_passing": 16,
            "items_failing": 2,
            "method": "drone overflight + AI defect classification",
        },
    },
    {
        "inspection_type": "structural_steel",
        "status": "in_progress",
        "location": "Level 4 — beam connections, gridlines D-G",
        "score": None,
        "offset_days": -1,
        "checklist_data": {
            "items_checked": 12,
            "items_passing": 9,
            "items_failing": 3,
            "method": "visual + bolt torque + corrosion check",
        },
    },
]


# Defect ViT v1.1 reports — one per class. Each entry pairs a real defect
# image with the model's top-1 classification, severity, and recommendations.
DEFECT_REPORTS = [
    {
        "defect_type": "crack",
        "severity": "major",
        "status": "open",
        "description": (
            "Hairline-to-fine crack pattern detected across slab surface. "
            "Propagation visible along two intersecting paths. ACI 224R "
            "limits suggest investigation required to determine if structural "
            "or shrinkage origin."
        ),
        "location": "Level 2 — slab S-12, gridline F",
        "image_url": "/quality-demo/defect-crack.png",
        "offset_days": -3,
        "ai_classification": {
            "model": "defect_vit_v1.1",
            "top_class": "crack",
            "confidence": 0.913,
            "probabilities": {
                "crack": 0.913,
                "spalling": 0.041,
                "corrosion": 0.012,
                "efflorescence": 0.014,
                "exposed_rebar": 0.005,
                "surface_deterioration": 0.011,
                "biological_growth": 0.001,
                "no_defect": 0.003,
            },
            "severity_estimate": "major",
            "recommendations": [
                "Mark crack endpoints, monitor over 14 days for propagation",
                "Measure crack width with crack comparator at three points",
                "If width > 0.012 in., engage structural engineer for ACI 224R review",
            ],
        },
    },
    {
        "defect_type": "spalling",
        "severity": "major",
        "status": "in_progress",
        "description": (
            "Concrete spalling observed on column face — surface paint "
            "delaminated and underlying concrete fragments missing. Likely "
            "caused by freeze-thaw cycle or impact damage."
        ),
        "location": "Level 1 — column C-7, north face",
        "image_url": "/quality-demo/defect-spalling.png",
        "offset_days": -7,
        "ai_classification": {
            "model": "defect_vit_v1.1",
            "top_class": "spalling",
            "confidence": 0.872,
            "probabilities": {
                "crack": 0.038,
                "spalling": 0.872,
                "corrosion": 0.014,
                "efflorescence": 0.024,
                "exposed_rebar": 0.022,
                "surface_deterioration": 0.026,
                "biological_growth": 0.001,
                "no_defect": 0.003,
            },
            "severity_estimate": "major",
            "recommendations": [
                "Sound surrounding concrete with hammer to find drummy areas",
                "Remove loose material to sound concrete; do NOT patch over",
                "Inspect for rebar exposure or corrosion behind spall",
            ],
        },
    },
    {
        "defect_type": "efflorescence",
        "severity": "minor",
        "status": "open",
        "description": (
            "White salt deposits visible on concrete surface — typical "
            "indicator of moisture migration through the substrate. Not "
            "structurally significant but suggests waterproofing or drainage "
            "issue requiring attention."
        ),
        "location": "Basement — west retaining wall, Bay B",
        "image_url": "/quality-demo/defect-efflorescence.png",
        "offset_days": -5,
        "ai_classification": {
            "model": "defect_vit_v1.1",
            "top_class": "efflorescence",
            "confidence": 0.894,
            "probabilities": {
                "crack": 0.008,
                "spalling": 0.018,
                "corrosion": 0.011,
                "efflorescence": 0.894,
                "exposed_rebar": 0.002,
                "surface_deterioration": 0.058,
                "biological_growth": 0.005,
                "no_defect": 0.004,
            },
            "severity_estimate": "minor",
            "recommendations": [
                "Locate moisture source — check drainage and waterproofing membrane",
                "Wash deposits with diluted muriatic acid (1:10) after source repair",
                "Re-inspect in 60 days to confirm no recurrence",
            ],
        },
    },
    {
        "defect_type": "corrosion",
        "severity": "critical",
        "status": "open",
        "description": (
            "Heavy rust staining on structural steel beam underside, with "
            "vertical streaking indicating active water intrusion. Section "
            "loss possible — engineering review required before continued "
            "load."
        ),
        "location": "Level 3 — beam B-14, gridline E-F",
        "image_url": "/quality-demo/defect-corrosion.png",
        "offset_days": -1,
        "ai_classification": {
            "model": "defect_vit_v1.1",
            "top_class": "corrosion",
            "confidence": 0.928,
            "probabilities": {
                "crack": 0.005,
                "spalling": 0.012,
                "corrosion": 0.928,
                "efflorescence": 0.018,
                "exposed_rebar": 0.026,
                "surface_deterioration": 0.008,
                "biological_growth": 0.001,
                "no_defect": 0.002,
            },
            "severity_estimate": "critical",
            "recommendations": [
                "Section-loss measurement with calipers or ultrasonic gauge",
                "Engage structural engineer before adding any load to this member",
                "Identify and repair water source above the affected area",
                "If loss > 10% of original thickness, plate-reinforce or replace",
            ],
        },
    },
    {
        "defect_type": "exposed_rebar",
        "severity": "critical",
        "status": "open",
        "description": (
            "Exposed and corroding reinforcement bar visible at concrete "
            "spall location. Cover concrete missing for ~6 inches. Active "
            "corrosion will accelerate; structural integrity compromised "
            "until repaired."
        ),
        "location": "Loading dock — slab edge, station +24",
        "image_url": "/quality-demo/defect-exposed-rebar.png",
        "offset_days": -2,
        "ai_classification": {
            "model": "defect_vit_v1.1",
            "top_class": "exposed_rebar",
            "confidence": 0.951,
            "probabilities": {
                "crack": 0.004,
                "spalling": 0.018,
                "corrosion": 0.014,
                "efflorescence": 0.002,
                "exposed_rebar": 0.951,
                "surface_deterioration": 0.008,
                "biological_growth": 0.001,
                "no_defect": 0.002,
            },
            "severity_estimate": "critical",
            "recommendations": [
                "Engage structural engineer for repair design (concrete cover restoration)",
                "Sandblast or wire-brush rebar to remove rust scale before patching",
                "Apply rebar primer + corrosion-inhibiting bonding agent",
                "Patch with non-shrink, polymer-modified concrete repair mortar",
            ],
        },
    },
]


# ===========================================================================
# Helpers
# ===========================================================================


async def _list_demo_orgs_and_users() -> list[tuple[Organization, Project, User]]:
    """Returns (org, project, pm_user) tuples for each demo tenant."""
    async with async_session() as db:
        result = await db.execute(
            select(Organization).where(Organization.slug.like("demo_session_%"))
        )
        orgs = list(result.scalars().all())
        out: list[tuple[Organization, Project, User]] = []
        for org in orgs:
            r = await db.execute(select(Project).where(Project.org_id == org.id).limit(1))
            project = r.scalar_one_or_none()
            r = await db.execute(
                select(User).where(User.org_id == org.id, User.role == "project_manager").limit(1)
            )
            pm = r.scalar_one_or_none()
            if project is not None and pm is not None:
                out.append((org, project, pm))
        return out


# ===========================================================================
# Per-content-type seeders
# ===========================================================================


async def seed_extra_rfis(org: Organization, project: Project, pm: User) -> int:
    """12 extra RFIs per tenant (added to the 3 baseline)."""
    inserted = 0
    async with async_session() as db:
        for idx, sample in enumerate(EXTRA_RFIS, start=4):  # baseline used 1-3
            rfi_id = uuid.uuid5(NS, f"{org.slug}::extra-rfi-{idx:03d}")
            stmt = (
                insert(RFI)
                .values(
                    id=rfi_id,
                    project_id=project.id,
                    rfi_number=f"RFI-{idx:03d}",
                    subject=sample["subject"],
                    question=sample["question"],
                    answer=sample.get("answer"),
                    response=sample.get("answer"),
                    status=sample.get("status", "answered"),
                    priority="normal",
                    spec_section=sample.get("spec_section"),
                    drawing_reference=sample.get("drawing_reference"),
                    submitted_by=pm.id,
                    data_source="public_demo",
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "subject": sample["subject"],
                        "answer": sample.get("answer"),
                        "response": sample.get("answer"),
                        "status": sample.get("status", "answered"),
                    },
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()

    # Index each answered RFI for similarity search (separate sessions to
    # avoid greenlet-related transaction issues).
    indexed = 0
    for idx, sample in enumerate(EXTRA_RFIS, start=4):
        if sample.get("status") != "answered" or not sample.get("answer"):
            continue
        rfi_id = uuid.uuid5(NS, f"{org.slug}::extra-rfi-{idx:03d}")
        async with async_session() as db:
            try:
                await index_rfi_for_search(
                    db,
                    rfi_id=rfi_id,
                    project_id=project.id,
                    subject=sample["subject"],
                    question=sample["question"],
                    answer=sample["answer"],
                    rfi_number=f"RFI-{idx:03d}",
                )
                await db.commit()
                indexed += 1
            except Exception as exc:
                await db.rollback()
                print(f"  - {org.slug} RFI-{idx:03d} index failed: {exc}")
    return inserted


async def seed_submittals(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for sample in SUBMITTALS:
            sub_id = uuid.uuid5(NS, f"{org.slug}::sub::{sample['submittal_number']}")
            due = TODAY + timedelta(days=sample["due_offset_days"])
            stmt = (
                insert(Submittal)
                .values(
                    id=sub_id,
                    project_id=project.id,
                    submittal_number=sample["submittal_number"],
                    title=sample["title"],
                    spec_section=sample["spec_section"],
                    status=sample["status"],
                    due_date=due,
                    description=sample["description"],
                    submitted_by=pm.id,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "status": sample["status"],
                        "title": sample["title"],
                        "description": sample["description"],
                    },
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_daily_reports(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for sample in DAILY_REPORTS:
            report_date = TODAY + timedelta(days=sample["offset_days"])
            dr_id = uuid.uuid5(NS, f"{org.slug}::daily::{report_date.isoformat()}")
            md = (
                f"# Daily Report — {report_date.isoformat()}\n\n"
                f"**Weather:** {sample['weather']}\n"
                f"**Manpower on site:** {sample['manpower']}\n\n"
                f"## Narrative\n\n{sample['narrative']}\n\n"
                f"## Deliveries\n\n" + "\n".join(f"- {d}" for d in sample["deliveries"]) + "\n"
            )
            stmt = (
                insert(DailyReport)
                .values(
                    id=dr_id,
                    project_id=project.id,
                    report_date=report_date,
                    status="published",
                    content_markdown=md,
                    sections={
                        "weather": sample["weather"],
                        "manpower": sample["manpower"],
                        "deliveries": sample["deliveries"],
                        "narrative": sample["narrative"],
                    },
                    generated_by="ai",
                    reviewed_by=pm.id,
                    published_at=datetime.combine(report_date, time(17, 0, tzinfo=UTC)),
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"content_markdown": md, "status": "published"},
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_meetings(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for sample in MEETINGS:
            meeting_date = TODAY + timedelta(days=sample["offset_days"])
            m_id = uuid.uuid5(
                NS, f"{org.slug}::meeting::{sample['meeting_type']}::{meeting_date.isoformat()}"
            )
            stmt = (
                insert(MeetingMinutes)
                .values(
                    id=m_id,
                    project_id=project.id,
                    meeting_type=sample["meeting_type"],
                    meeting_date=meeting_date,
                    title=sample["title"],
                    summary=sample["summary"],
                    decisions=sample["decisions"],
                    action_items=[
                        {**a, "due_date": (TODAY + timedelta(days=a["due"])).isoformat()}
                        for a in sample["action_items"]
                    ],
                    attendees=[
                        {"name": "Demo PM", "role": "PM", "company": org.name},
                        {"name": "Demo FE", "role": "Field Engineer", "company": org.name},
                    ],
                    status="published",
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"summary": sample["summary"], "decisions": sample["decisions"]},
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_punch_list(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        pl_id = uuid.uuid5(NS, f"{org.slug}::punch::main")
        stmt = (
            insert(PunchList)
            .values(
                id=pl_id,
                project_id=project.id,
                name=PUNCH_LIST_NAME,
                description="Substantial-completion walkthrough by GC + Architect + Owner.",
                walk_date=TODAY - timedelta(days=2),
                status="open",
                created_by=pm.id,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"status": "open"},
            )
        )
        await db.execute(stmt)

        for sample in PUNCH_ITEMS:
            item_id = uuid.uuid5(NS, f"{org.slug}::punch_item::{sample['item_number']}")
            stmt = (
                insert(PunchListItem)
                .values(
                    id=item_id,
                    project_id=project.id,
                    punch_list_id=pl_id,
                    item_number=sample["item_number"],
                    description=sample["description"],
                    location=sample["location"],
                    category=sample["category"],
                    priority=sample["priority"],
                    status=sample["status"],
                    created_by=pm.id,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"status": sample["status"], "description": sample["description"]},
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_pcos(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for sample in PCOS:
            pco_id = uuid.uuid5(NS, f"{org.slug}::pco::{sample['pco_number']}")
            stmt = (
                insert(PotentialChangeOrder)
                .values(
                    id=pco_id,
                    project_id=project.id,
                    pco_number=sample["pco_number"],
                    title=sample["title"],
                    description=sample["description"],
                    change_type=sample["change_type"],
                    status=sample["status"],
                    originated_by=pm.id,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"status": sample["status"], "description": sample["description"]},
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_change_orders(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for sample in CHANGE_ORDERS:
            co_id = uuid.uuid5(NS, f"{org.slug}::co::{sample['co_number']}")
            submitted_at = datetime.combine(
                TODAY + timedelta(days=sample["offset_days"]), time(9, 0), tzinfo=UTC
            )
            stmt = (
                insert(ChangeOrder)
                .values(
                    id=co_id,
                    project_id=project.id,
                    co_number=sample["co_number"],
                    title=sample["title"],
                    description=sample["description"],
                    change_type=sample["change_type"],
                    status=sample["status"],
                    cost_impact=sample["cost_impact"],
                    schedule_impact_days=sample["schedule_impact_days"],
                    labor_cost=sample["labor_cost"],
                    material_cost=sample["material_cost"],
                    equipment_cost=sample["equipment_cost"],
                    subcontractor_cost=sample["subcontractor_cost"],
                    overhead_cost=sample["overhead_cost"],
                    markup_pct=sample["markup_pct"],
                    risk_score=sample["risk_score"],
                    requested_by=pm.id,
                    data_source="public_demo",
                    submitted_at=submitted_at,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "title": sample["title"],
                        "description": sample["description"],
                        "status": sample["status"],
                        "cost_impact": sample["cost_impact"],
                        "schedule_impact_days": sample["schedule_impact_days"],
                    },
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


async def seed_quality(org: Organization, project: Project, pm: User) -> tuple[int, int]:
    """Seed inspection rows + AI-classified defect reports per tenant."""
    inspection_ids: list[uuid.UUID] = []
    inserted_inspections = 0
    inserted_defects = 0

    async with async_session() as db:
        for idx, sample in enumerate(INSPECTIONS, start=1):
            insp_id = uuid.uuid5(NS, f"{org.slug}::inspection::{idx}")
            inspection_ids.append(insp_id)
            scheduled = datetime.combine(
                TODAY + timedelta(days=sample["offset_days"]), time(9, 0), tzinfo=UTC
            )
            completed = scheduled + timedelta(hours=4) if sample["status"] == "completed" else None
            stmt = (
                insert(Inspection)
                .values(
                    id=insp_id,
                    project_id=project.id,
                    inspection_type=sample["inspection_type"],
                    status=sample["status"],
                    inspector_id=pm.id,
                    location=sample["location"],
                    checklist_data=sample["checklist_data"],
                    findings={},
                    score=sample["score"],
                    scheduled_at=scheduled,
                    completed_at=completed,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "status": sample["status"],
                        "location": sample["location"],
                        "checklist_data": sample["checklist_data"],
                        "score": sample["score"],
                        "completed_at": completed,
                    },
                )
            )
            await db.execute(stmt)
            inserted_inspections += 1
        await db.commit()

    # Defects — link each one to the first inspection so the relationship
    # is meaningful in the demo.
    primary_inspection = inspection_ids[0] if inspection_ids else None

    async with async_session() as db:
        for idx, sample in enumerate(DEFECT_REPORTS, start=1):
            defect_id = uuid.uuid5(NS, f"{org.slug}::defect::{idx}")
            stmt = (
                insert(DefectReport)
                .values(
                    id=defect_id,
                    project_id=project.id,
                    inspection_id=primary_inspection,
                    defect_type=sample["defect_type"],
                    severity=sample["severity"],
                    status=sample["status"],
                    description=sample["description"],
                    location=sample["location"],
                    image_urls=[sample["image_url"]],
                    ai_classification=sample["ai_classification"],
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "severity": sample["severity"],
                        "status": sample["status"],
                        "description": sample["description"],
                        "location": sample["location"],
                        "image_urls": [sample["image_url"]],
                        "ai_classification": sample["ai_classification"],
                    },
                )
            )
            await db.execute(stmt)
            inserted_defects += 1
        await db.commit()

    return inserted_inspections, inserted_defects


async def seed_safety_alerts(org: Organization, project: Project, pm: User) -> int:
    inserted = 0
    async with async_session() as db:
        for idx, sample in enumerate(SAFETY_ALERTS, start=1):
            alert_id = uuid.uuid5(NS, f"{org.slug}::alert::{idx}")
            detections = sample.get(
                "detections",
                [
                    {
                        "class_name": sample["alert_type"].replace("_", " "),
                        "confidence": float(sample["confidence"]),
                        "bbox": [120, 80, 240, 320],
                    }
                ],
            )
            stmt = (
                insert(SafetyAlert)
                .values(
                    id=alert_id,
                    project_id=project.id,
                    priority=sample["priority"],
                    alert_type=sample["alert_type"],
                    description=sample["description"],
                    confidence=sample["confidence"],
                    detections=detections,
                    osha_reference=sample.get("osha_reference"),
                    frame_s3_key=sample.get("frame_s3_key"),
                    is_acknowledged=False,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "priority": sample["priority"],
                        "alert_type": sample["alert_type"],
                        "description": sample["description"],
                        "confidence": sample["confidence"],
                        "detections": detections,
                        "osha_reference": sample.get("osha_reference"),
                        "frame_s3_key": sample.get("frame_s3_key"),
                    },
                )
            )
            await db.execute(stmt)
            inserted += 1
        await db.commit()
    return inserted


# ===========================================================================
# Driver
# ===========================================================================


async def seed():
    pairs = await _list_demo_orgs_and_users()
    if not pairs:
        print("No demo tenants. Run seed_demo_tenants.py first.")
        return

    totals = {
        "rfis": 0,
        "submittals": 0,
        "daily_reports": 0,
        "meetings": 0,
        "punch_items": 0,
        "pcos": 0,
        "change_orders": 0,
        "inspections": 0,
        "defects": 0,
        "alerts": 0,
    }

    for org, project, pm in pairs:
        print(f"=== {org.slug} ({org.name}) ===")
        try:
            totals["rfis"] += await seed_extra_rfis(org, project, pm)
            totals["submittals"] += await seed_submittals(org, project, pm)
            totals["daily_reports"] += await seed_daily_reports(org, project, pm)
            totals["meetings"] += await seed_meetings(org, project, pm)
            totals["punch_items"] += await seed_punch_list(org, project, pm)
            totals["pcos"] += await seed_pcos(org, project, pm)
            totals["change_orders"] += await seed_change_orders(org, project, pm)
            ins, dfs = await seed_quality(org, project, pm)
            totals["inspections"] += ins
            totals["defects"] += dfs
            totals["alerts"] += await seed_safety_alerts(org, project, pm)
            print(
                f"  rfis+={len(EXTRA_RFIS)} subs+={len(SUBMITTALS)} dr+={len(DAILY_REPORTS)} "
                f"meetings+={len(MEETINGS)} punch+={len(PUNCH_ITEMS)} "
                f"pco+={len(PCOS)} co+={len(CHANGE_ORDERS)} "
                f"insp+={len(INSPECTIONS)} defects+={len(DEFECT_REPORTS)} "
                f"alerts+={len(SAFETY_ALERTS)}"
            )
        except Exception as exc:
            print(f"  FAIL: {type(exc).__name__}: {exc}")

    print("\n=== TOTALS ===")
    for k, v in totals.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(seed())
