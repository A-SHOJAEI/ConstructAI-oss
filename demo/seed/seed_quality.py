"""
Create quality management data:

- 8 inspections over the past 60 days
- 12 defects found across inspections
- 15 punch list items (as defect_reports with status tracking)
- 5 compliance checks against IBC and ADA requirements
"""
import random
from datetime import datetime, timedelta, timezone

from app.database import async_session
from app.models import Inspection, DefectReport, ComplianceCheck

random.seed(42)
NOW = datetime.now(timezone.utc)


async def seed_quality(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    field1_id = ctx["field1_user_id"]
    field2_id = ctx["field2_user_id"]

    async with async_session() as db:
        # --- 8 Inspections ---
        inspection_defs = [
            ("foundation", "Foundation - Footing Rebar", "Level B1, Grid A1-A4", 45, 88.5),
            ("foundation", "Foundation - Concrete Pour Pre-check", "Level B1, Grid B1-B6", 40, 92.0),
            ("structural", "Structural Steel - Level 1 Connections", "Level 1, all bays", 30, 85.0),
            ("structural", "Structural Steel - Level 2 Erection", "Level 2, bays 1-8", 25, 90.5),
            ("mep", "MEP Rough-in - Electrical L1", "Level 1, all rooms", 20, 78.0),
            ("mep", "MEP Rough-in - Plumbing L1", "Level 1, wet walls", 18, 82.5),
            ("drywall", "Drywall - Level 1 Framing", "Level 1, offices 101-108", 12, 91.0),
            ("drywall", "Drywall - Level 1 Taping", "Level 1, offices 101-108", 8, 87.5),
        ]

        inspections = []
        for i, (itype, location, loc_detail, days_ago, score) in enumerate(inspection_defs):
            completed = NOW - timedelta(days=days_ago)
            insp = Inspection(
                project_id=project_id,
                inspection_type=itype,
                status="completed",
                inspector_id=field1_id if i % 2 == 0 else field2_id,
                location=loc_detail,
                checklist_data={
                    "template": f"{itype}_checklist_v2",
                    "total_items": random.randint(15, 30),
                    "passed_items": random.randint(12, 28),
                },
                findings=[{"description": location, "severity": "info"}],
                score=score,
                scheduled_at=completed - timedelta(days=1),
                completed_at=completed,
            )
            db.add(insp)
            inspections.append(insp)
        await db.flush()

        # --- 12 Defects ---
        defect_defs = [
            # Foundation cracks (4)
            (inspections[0], "crack", "major", "Hairline crack in footing F-A1, 0.3mm width, 450mm length",
             "Level B1, Footing F-A1", "open"),
            (inspections[0], "crack", "minor", "Surface crack in footing F-A2, 0.1mm width",
             "Level B1, Footing F-A2", "in_progress"),
            (inspections[1], "crack", "major", "Diagonal crack in grade beam GB-3, 0.4mm width",
             "Level B1, Grade Beam GB-3", "open"),
            (inspections[1], "crack", "minor", "Shrinkage crack in slab-on-grade, non-structural",
             "Level B1, Area 4", "resolved"),
            # Steel misalignments (3)
            (inspections[2], "misalignment", "major", "Column C-12 plumb deviation 3/8 inch over 14 feet",
             "Level 1, Column C-12", "in_progress"),
            (inspections[2], "misalignment", "minor", "Beam-to-column connection gap 1/16 inch at B-7",
             "Level 1, Bay 7", "resolved"),
            (inspections[3], "misalignment", "minor", "Minor web coping offset at connection W2-L2-5",
             "Level 2, Bay 5", "open"),
            # MEP coordination (2)
            (inspections[4], "coordination_conflict", "major",
             "Electrical conduit conflicts with HVAC duct at corridor ceiling, insufficient clearance (2 inch gap required, 0.5 inch actual)",
             "Level 1, Corridor C-1", "open"),
            (inspections[5], "coordination_conflict", "minor",
             "Plumbing drain pipe crosses sprinkler main - requires reroute",
             "Level 1, Mechanical Room 102", "in_progress"),
            # Drywall issues (3)
            (inspections[6], "installation_defect", "minor",
             "Stud spacing 18 inches instead of specified 16 inches at offices 103-104",
             "Level 1, Offices 103-104", "open"),
            (inspections[7], "finish_defect", "minor",
             "Visible joint banding at Level 4 finish under raking light conditions",
             "Level 1, Office 106", "open"),
            (inspections[7], "finish_defect", "minor",
             "Corner bead damage at office 108 entrance, needs replacement",
             "Level 1, Office 108", "in_progress"),
        ]

        for insp, dtype, severity, desc, loc, status in defect_defs:
            resolved_at = NOW - timedelta(days=random.randint(1, 5)) if status == "resolved" else None
            defect = DefectReport(
                project_id=project_id,
                inspection_id=insp.id,
                defect_type=dtype,
                severity=severity,
                status=status,
                description=desc,
                location=loc,
                image_urls=[],
                ai_classification={"type": dtype, "confidence": round(random.uniform(0.82, 0.97), 3)},
                assigned_to=field1_id if severity == "major" else field2_id,
                resolved_at=resolved_at,
            )
            db.add(defect)

        # --- 15 Punch List Items (as DefectReport with punch_list type) ---
        punch_items = [
            ("paint_touch_up", "minor", "Touch up paint scuff on wall, office 101", "Level 1, Office 101", "open"),
            ("hardware", "minor", "Door closer adjustment needed, suite 201", "Level 2, Suite 201", "open"),
            ("caulking", "minor", "Missing caulk at window frame, room 305", "Level 3, Room 305", "open"),
            ("electrical", "minor", "Outlet cover plate missing, corridor 2N", "Level 2, Corridor 2N", "open"),
            ("plumbing", "minor", "Faucet drip at restroom 1B lavatory 3", "Level 1, Restroom 1B", "open"),
            ("flooring", "minor", "Carpet tile edge lifting at entrance 1A", "Level 1, Entrance 1A", "open"),
            ("ceiling", "minor", "Ceiling tile alignment at office 105", "Level 1, Office 105", "open"),
            ("signage", "minor", "Room number sign missing at office 107", "Level 1, Office 107", "open"),
            ("paint_touch_up", "minor", "Roller marks visible on north wall, suite 202", "Level 2, Suite 202", "in_progress"),
            ("hardware", "minor", "Lock cylinder needs adjustment, office 103", "Level 1, Office 103", "in_progress"),
            ("caulking", "minor", "Exterior caulk gap at curtain wall panel CW-12", "Level 3, CW-12", "in_progress"),
            ("electrical", "minor", "Dimmer switch non-responsive, conference room 2A", "Level 2, Conf 2A", "in_progress"),
            ("flooring", "minor", "LVT seam visible at corridor transition", "Level 1, Corridor", "resolved"),
            ("plumbing", "minor", "Hot/cold labels reversed at break room sink", "Level 2, Break Room", "resolved"),
            ("ceiling", "minor", "Light fixture alignment with ceiling grid off by 1/4 inch", "Level 1, Lobby", "resolved"),
        ]

        for dtype, severity, desc, loc, status in punch_items:
            resolved_at = NOW - timedelta(days=random.randint(1, 3)) if status == "resolved" else None
            defect = DefectReport(
                project_id=project_id,
                defect_type=dtype,
                severity=severity,
                status=status,
                description=desc,
                location=loc,
                image_urls=[],
                ai_classification={"source": "punch_list", "trade": dtype},
                assigned_to=field2_id,
                resolved_at=resolved_at,
            )
            db.add(defect)

        # --- 5 Compliance Checks ---
        compliance_defs = [
            ("IBC 1005.1", "Egress Width - Stairways",
             "passed", "pass", "All stairways meet minimum 44-inch clear width requirement"),
            ("IBC 1006.2", "Egress Illumination",
             "passed", "pass", "Emergency lighting verified at 1 foot-candle average along egress path"),
            ("ADA 404.2.3", "Door Maneuvering Clearances",
             "failed", "fail", "Office 104 door pull-side clearance is 16 inches; 18 inches required"),
            ("ADA 603.2.1", "Accessible Toilet Rooms - Clear Floor Space",
             "failed", "fail", "Restroom 2B accessible stall clear floor space 56x57 inches; 60x56 required"),
            ("IBC 1020.1", "Corridor Width",
             "pending", None, "Pending measurement verification after drywall completion on Level 3"),
        ]

        for reg_code, reg_title, status, result, finding in compliance_defs:
            checked_at = NOW - timedelta(days=random.randint(5, 20)) if status != "pending" else None
            check = ComplianceCheck(
                project_id=project_id,
                regulation_code=reg_code,
                regulation_title=reg_title,
                status=status,
                check_result=result,
                findings=[{"description": finding, "regulation": reg_code}],
                checked_by=field1_id if status != "pending" else None,
                checked_at=checked_at,
                next_check_due=NOW + timedelta(days=30) if status != "pending" else None,
            )
            db.add(check)

        await db.commit()

    return {}
