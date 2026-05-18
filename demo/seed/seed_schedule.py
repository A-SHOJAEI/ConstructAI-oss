"""
Create 50 schedule activities with realistic WBS, durations, relationships,
and deliberately planted DCMA issues for the Scheduling Agent to find:

- 3 activities with missing predecessors (DCMA check #1 failure)
- 2 activities with excessive lag (>5 days, DCMA check #5)
- 4 activities with high total float (>44 days, DCMA check #7)
- 1 negative lag (DCMA check #4)

WBS Structure:
  01 General Conditions
  02 Sitework
  03 Foundations
  04 Structural Steel
  05 Building Envelope
  06 MEP Rough-in
  07 Interior Finishes
  08 Commissioning & Closeout
"""
import json
from datetime import date, timedelta

from app.database import async_session
from app.models import ScheduleBaseline, ScheduleActivity

PROJECT_START = date(2025, 5, 1)
TODAY = date(2026, 2, 23)

# (activity_code, wbs, name, duration, predecessors, is_critical, total_float)
# predecessors: [(pred_code, type, lag)]
ACTIVITIES = [
    # 01 General Conditions
    ("A010", "01", "Mobilization", 10, [], True, 0),
    ("A011", "01", "Temporary Facilities", 15, [("A010", "FS", 0)], False, 5),
    ("A012", "01", "Permits & Approvals", 20, [], False, 60),  # Missing pred (DCMA #1)

    # 02 Sitework
    ("A020", "02", "Site Clearing", 8, [("A010", "FS", 0)], True, 0),
    ("A021", "02", "Erosion Control", 5, [("A020", "FS", 0)], False, 3),
    ("A022", "02", "Excavation - Parking Garage", 25, [("A020", "FS", 0)], True, 0),
    ("A023", "02", "Dewatering", 30, [("A022", "SS", 5)], False, 2),
    ("A024", "02", "Underground Utilities", 20, [("A022", "FS", 10)], False, 8),  # Excessive lag (DCMA #5)

    # 03 Foundations
    ("A030", "03", "Foundation Formwork", 15, [("A022", "FS", 0)], True, 0),
    ("A031", "03", "Foundation Rebar", 12, [("A030", "FS", 0)], True, 0),
    ("A032", "03", "Foundation Concrete Pour", 8, [("A031", "FS", 0)], True, 0),
    ("A033", "03", "Foundation Waterproofing", 10, [("A032", "FS", 3)], False, 5),
    ("A034", "03", "Backfill", 8, [("A033", "FS", 0)], False, 5),

    # 04 Structural Steel
    ("A040", "04", "Steel Shop Drawings", 30, [("A030", "SS", 0)], False, 10),
    ("A041", "04", "Steel Fabrication", 45, [("A040", "FS", 0)], False, 10),
    ("A042", "04", "Steel Erection - Level 1", 15, [("A032", "FS", 0)], True, 0),
    ("A043", "04", "Steel Erection - Level 2", 12, [("A042", "FS", 0)], True, 0),
    ("A044", "04", "Steel Erection - Level 3", 12, [("A043", "FS", 0)], True, 0),
    ("A045", "04", "Steel Erection - Level 4-5", 15, [("A044", "FS", 0)], True, 0),
    ("A046", "04", "Steel Connections & Bolting", 20, [("A045", "FS", -3)], True, 0),  # Negative lag (DCMA #4)

    # 05 Building Envelope
    ("A050", "05", "Metal Deck - Level 1", 10, [("A042", "FS", 0)], True, 0),
    ("A051", "05", "Metal Deck - Level 2", 10, [("A043", "FS", 0)], True, 0),
    ("A052", "05", "Concrete on Deck", 20, [("A050", "FS", 0), ("A051", "FS", 0)], True, 0),
    ("A053", "05", "Curtain Wall Fabrication", 60, [("A040", "FS", 0)], False, 15),
    ("A054", "05", "Curtain Wall Installation", 40, [("A053", "FS", 0), ("A045", "FS", 0)], False, 5),
    ("A055", "05", "Roofing", 15, [("A045", "FS", 5)], False, 8),
    ("A056", "05", "Waterproofing - Below Grade", 12, [], False, 50),  # Missing pred (DCMA #1), high float (DCMA #7)

    # 06 MEP Rough-in
    ("A060", "06", "Electrical Rough-in L1-L2", 25, [("A052", "FS", 0)], True, 0),
    ("A061", "06", "Plumbing Rough-in L1-L2", 25, [("A052", "FS", 0)], False, 3),
    ("A062", "06", "HVAC Ductwork L1-L2", 30, [("A052", "FS", 0)], False, 2),
    ("A063", "06", "Fire Sprinkler", 20, [("A060", "FS", 0)], True, 0),
    ("A064", "06", "Elevator Installation", 45, [("A045", "FS", 0)], False, 12),
    ("A065", "06", "MEP Rough-in L3-L5", 35, [("A060", "FS", 0)], True, 0),
    ("A066", "06", "MEP Coordination BIM", 15, [], False, 55),  # Missing pred (DCMA #1), high float (DCMA #7)

    # 07 Interior Finishes
    ("A070", "07", "Drywall & Framing L1-L2", 20, [("A063", "FS", 0)], True, 0),
    ("A071", "07", "Drywall & Framing L3-L5", 25, [("A065", "FS", 0)], True, 0),
    ("A072", "07", "Taping & Finishing", 15, [("A070", "FS", 0), ("A071", "FS", 0)], True, 0),
    ("A073", "07", "Painting", 20, [("A072", "FS", 0)], True, 0),
    ("A074", "07", "Flooring - Retail", 12, [("A073", "FS", 0)], False, 5),
    ("A075", "07", "Flooring - Office", 15, [("A073", "FS", 0)], True, 0),
    ("A076", "07", "Flooring - Residential", 15, [("A073", "FS", 8)], False, 8),  # Excessive lag (DCMA #5)
    ("A077", "07", "Millwork & Casework", 20, [("A073", "FS", 0)], False, 3),
    ("A078", "07", "Tile Work", 15, [("A072", "FS", 0)], False, 8),
    ("A079", "07", "Specialties (signage, accessories)", 10, [], False, 48),  # High float (DCMA #7)

    # 08 Commissioning
    ("A080", "08", "MEP Systems Testing", 15, [("A075", "FS", 0)], True, 0),
    ("A081", "08", "Fire Alarm Testing", 8, [("A080", "FS", 0)], True, 0),
    ("A082", "08", "Elevator Inspection", 5, [("A064", "FS", 0)], False, 15),
    ("A083", "08", "Punch List", 15, [("A081", "FS", 0)], True, 0),
    ("A084", "08", "Final Cleaning", 5, [("A083", "FS", 0)], True, 0),
    ("A085", "08", "Certificate of Occupancy", 10, [("A084", "FS", 0)], True, 0),
    ("A086", "08", "Demobilization", 5, [("A085", "FS", 0)], True, 0),
]


def _compute_dates(activity_code: str, duration: int) -> tuple[date, date]:
    """Deterministic early start based on activity code hash for consistent demos."""
    idx = int(activity_code[1:]) * 3  # Spread activities across timeline
    es = PROJECT_START + timedelta(days=idx)
    ef = es + timedelta(days=duration)
    return es, ef


async def seed_schedule(ctx: dict) -> dict:
    project_id = ctx["project_id"]

    async with async_session() as db:
        # Create a baseline first
        baseline = ScheduleBaseline(
            project_id=project_id,
            name="Original Baseline",
            version=1,
            baseline_date=PROJECT_START,
            total_duration_days=548,
            critical_path_length=420,
            metadata={"seeded": True},
        )
        db.add(baseline)
        await db.flush()

        for act_code, wbs, name, duration, preds, is_critical, total_float in ACTIVITIES:
            early_start, early_finish = _compute_dates(act_code, duration)
            late_start = early_start + timedelta(days=total_float)
            late_finish = early_finish + timedelta(days=total_float)

            # Calculate progress based on current date
            if early_finish <= TODAY:
                pct = 100.0
                status = "complete"
                actual_start = early_start
                actual_finish = early_finish
            elif early_start <= TODAY:
                elapsed = (TODAY - early_start).days
                pct = min(95.0, round(elapsed / duration * 100, 1))
                status = "in_progress"
                actual_start = early_start
                actual_finish = None
            else:
                pct = 0.0
                status = "not_started"
                actual_start = None
                actual_finish = None

            activity = ScheduleActivity(
                project_id=project_id,
                baseline_id=baseline.id,
                activity_code=act_code,
                name=name,
                duration_days=duration,
                start_date=early_start,
                finish_date=early_finish,
                early_start=early_start,
                early_finish=early_finish,
                late_start=late_start,
                late_finish=late_finish,
                total_float=total_float,
                free_float=max(0, total_float - 2),
                is_critical=is_critical,
                predecessors=[
                    {"predecessor_id": p[0], "type": p[1], "lag": p[2]}
                    for p in preds
                ],
                wbs_code=wbs,
                status=status,
                actual_start=actual_start,
                actual_finish=actual_finish,
                pct_complete=pct,
                metadata={"seeded": True},
            )
            db.add(activity)

        await db.commit()

    return {"baseline_id": str(baseline.id)}
