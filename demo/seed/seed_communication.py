"""
Create communication records:

- 10 daily reports over the past 2 weeks
- 3 meeting minutes (OAC, safety, coordination)
- 5 RFIs at different stages
"""
import random
from datetime import date, datetime, timedelta, timezone

from app.database import async_session
from app.models import DailyReport, MeetingMinutes, RFI

random.seed(42)
TODAY = date(2026, 2, 23)
NOW = datetime.now(timezone.utc)


async def seed_communication(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    pm_id = ctx["pm_user_id"]
    super_id = ctx["super_user_id"]
    safety_id = ctx["safety_user_id"]
    architect_id = ctx["architect_user_id"]

    async with async_session() as db:
        # --- 10 Daily Reports ---
        for day_offset in range(14):
            report_date = TODAY - timedelta(days=day_offset)
            if report_date.weekday() >= 5:
                continue

            crew_count = random.randint(35, 65)
            weather_desc = random.choice([
                "Clear skies, 48F, winds 5-10 mph",
                "Partly cloudy, 52F, calm winds",
                "Overcast, 40F, winds 10-15 mph",
                "Light rain AM, clearing PM, 44F",
            ])

            report = DailyReport(
                project_id=project_id,
                report_date=report_date,
                status="published" if day_offset > 0 else "draft",
                content_markdown=(
                    f"# Daily Report - {report_date.strftime('%B %d, %Y')}\n\n"
                    f"## Weather\n{weather_desc}\n\n"
                    f"## Workforce\n- Total on site: {crew_count}\n"
                    f"- Concrete crew: 8\n- Steel erectors: 6\n"
                    f"- Electricians: 5\n- Drywall: 7\n\n"
                    f"## Work Completed\n"
                    f"- Structural steel erection Level 2 continued\n"
                    f"- Electrical rough-in Level 1 corridors\n"
                    f"- Foundation waterproofing south wall\n\n"
                    f"## Issues\n"
                    f"- Concrete delivery delayed 2 hours due to batch plant issue\n"
                    f"- Safety observation: worker without hard hat at north gate (addressed)\n"
                ),
                sections={
                    "weather": weather_desc,
                    "workforce": {"total": crew_count, "trades": 4},
                    "work_completed": 3,
                    "issues": 2,
                },
                generated_by="system",
                reviewed_by=pm_id if day_offset > 0 else None,
                published_at=NOW - timedelta(days=day_offset) if day_offset > 0 else None,
            )
            db.add(report)

        # --- 3 Meeting Minutes ---
        meetings = [
            {
                "type": "oac",
                "title": "OAC Meeting #18 - Bi-Weekly Progress Review",
                "date": TODAY - timedelta(days=3),
                "attendees": [
                    {"name": "Sarah Chen", "role": "PM", "org": "BuildRight"},
                    {"name": "David Riverside", "role": "Owner Rep", "org": "Riverside"},
                    {"name": "Robert Kim", "role": "Lead Architect", "org": "ArcDesign"},
                ],
                "summary": (
                    "Reviewed project status at month 10. Schedule is 12% behind "
                    "baseline (SPI 0.88) primarily due to foundation issues in months 5-6. "
                    "Cost tracking 9% over budget (CPI 0.91). Three active change orders "
                    "discussed. Owner approved CO-001 (rooftop terrace). CO-002 foundation "
                    "redesign pending final pricing. CO-003 electrical panel upgrade submitted."
                ),
                "decisions": [
                    {"decision": "Approve CO-001 rooftop terrace addition at $350K", "by": "David Riverside"},
                    {"decision": "Schedule recovery plan to be submitted by Feb 28", "by": "Sarah Chen"},
                    {"decision": "Expedite curtain wall fabrication to recover 5 days", "by": "Sarah Chen"},
                ],
                "action_items": [
                    {"action": "Submit schedule recovery plan", "assignee": "Sarah Chen", "due": str(TODAY + timedelta(days=5))},
                    {"action": "Finalize CO-002 pricing with foundation sub", "assignee": "Sarah Chen", "due": str(TODAY + timedelta(days=7))},
                    {"action": "Review curtain wall shop drawing revision 3", "assignee": "Robert Kim", "due": str(TODAY + timedelta(days=3))},
                ],
            },
            {
                "type": "safety",
                "title": "Weekly Safety Meeting #40",
                "date": TODAY - timedelta(days=5),
                "attendees": [
                    {"name": "James Okafor", "role": "Safety Manager", "org": "BuildRight"},
                    {"name": "Mike Rodriguez", "role": "Superintendent", "org": "BuildRight"},
                    {"name": "Emily Tran", "role": "Field Engineer", "org": "BuildRight"},
                ],
                "summary": (
                    "Reviewed 15 safety alerts from past 30 days. Two P1 incidents: crane zone "
                    "breach and fall detection at rooftop perimeter. Both addressed immediately. "
                    "PPE compliance has improved from 82% to 89% since camera system activation. "
                    "Two false positive alerts identified and fed back to AI system for retraining."
                ),
                "decisions": [
                    {"decision": "Install additional warning signage at crane zone boundary", "by": "James Okafor"},
                    {"decision": "Mandatory safety orientation for all new workers before site access", "by": "James Okafor"},
                ],
                "action_items": [
                    {"action": "Order and install crane zone warning signs", "assignee": "Mike Rodriguez", "due": str(TODAY + timedelta(days=2))},
                    {"action": "Update safety orientation checklist", "assignee": "James Okafor", "due": str(TODAY + timedelta(days=5))},
                    {"action": "Review rooftop perimeter guard rail installation", "assignee": "Emily Tran", "due": str(TODAY + timedelta(days=1))},
                ],
            },
            {
                "type": "coordination",
                "title": "MEP Coordination Meeting #12",
                "date": TODAY - timedelta(days=7),
                "attendees": [
                    {"name": "Sarah Chen", "role": "PM", "org": "BuildRight"},
                    {"name": "Emily Tran", "role": "Field Engineer", "org": "BuildRight"},
                ],
                "summary": (
                    "Reviewed BIM coordination model for Level 1 MEP systems. Two clashes "
                    "identified: electrical conduit vs HVAC duct at corridor C-1, and plumbing "
                    "drain crossing sprinkler main in mechanical room 102. Resolutions proposed "
                    "and agreed upon. Electrical will reroute conduit above duct; plumbing will "
                    "offset drain pipe 6 inches south."
                ),
                "decisions": [
                    {"decision": "Reroute electrical conduit above HVAC duct at corridor C-1", "by": "Sarah Chen"},
                    {"decision": "Offset plumbing drain 6 inches south at mech room 102", "by": "Sarah Chen"},
                ],
                "action_items": [
                    {"action": "Update BIM model with clash resolutions", "assignee": "Emily Tran", "due": str(TODAY + timedelta(days=3))},
                    {"action": "Issue revised MEP coordination drawing", "assignee": "Emily Tran", "due": str(TODAY + timedelta(days=5))},
                ],
            },
        ]

        for m in meetings:
            mm = MeetingMinutes(
                project_id=project_id,
                meeting_type=m["type"],
                meeting_date=m["date"],
                title=m["title"],
                attendees=m["attendees"],
                summary=m["summary"],
                action_items=m["action_items"],
                decisions=m["decisions"],
            )
            db.add(mm)

        # --- 5 RFIs ---
        rfi_defs = [
            {
                "number": "RFI-001",
                "subject": "Foundation Rebar Spacing Clarification",
                "question": "Drawing S-201 shows #5 rebar at 12 inches o.c. for footing F-A3, but Specification 03 30 00 Section 2.03 references ACI 318 minimum spacing which allows 8 inches o.c. for this loading condition. Please clarify the required spacing.",
                "status": "closed",
                "priority": "high",
                "response": "Use #5 @ 8 inches o.c. per ACI 318 calculation. Drawing S-201 will be revised in next bulletin. Proceed with 8-inch spacing.",
                "ai_response": "Based on ACI 318 Section 25.4.2 and the loading diagram on S-200, the required spacing for the given moment demand is 8 inches o.c. The drawing appears to show a conservative value. Recommend confirming with structural engineer.",
                "submitted_by": pm_id,
                "assigned_to": architect_id,
                "due": TODAY - timedelta(days=20),
                "responded_at": NOW - timedelta(days=18),
            },
            {
                "number": "RFI-002",
                "subject": "Curtain Wall Anchor Bolt Pattern",
                "question": "Curtain wall shop drawing detail CW-15 shows a 4-bolt anchor pattern, but the structural embed plate at Level 3 only has 3 anchor points. Please confirm if an additional anchor is required or if the 3-bolt pattern is acceptable.",
                "status": "responded",
                "priority": "high",
                "response": "4-bolt pattern is required per structural calculations. Additional embed plate to be installed. See SK-CW-001 for details.",
                "ai_response": "The structural embed at Level 3 appears undersized for 4-bolt connection. Recommend structural engineer review connection capacity with 3 bolts vs adding supplemental plate.",
                "submitted_by": pm_id,
                "assigned_to": architect_id,
                "due": TODAY - timedelta(days=5),
                "responded_at": NOW - timedelta(days=3),
            },
            {
                "number": "RFI-003",
                "subject": "Electrical Panel Location Conflict",
                "question": "Panel LP-2A location shown on E-101 conflicts with the ADA-required clear floor space at restroom 2B entrance. The panel door swing requires 42 inches of clearance which encroaches on the required 60-inch maneuvering space. Please advise on relocation.",
                "status": "open",
                "priority": "normal",
                "submitted_by": pm_id,
                "assigned_to": architect_id,
                "due": TODAY + timedelta(days=7),
            },
            {
                "number": "RFI-004",
                "subject": "Roofing Membrane Manufacturer Substitution",
                "question": "Specified roofing membrane (Siplast Paradiene 30) has a 6-week lead time. Carlisle SynTec TPO offers equivalent performance and is available in 2 weeks. Is substitution acceptable?",
                "status": "open",
                "priority": "normal",
                "submitted_by": pm_id,
                "assigned_to": architect_id,
                "due": TODAY + timedelta(days=10),
            },
            {
                "number": "RFI-005",
                "subject": "Concrete Mix Design for Cold Weather",
                "question": "With current winter conditions (overnight lows at 28F), can we use Type III cement with calcium chloride accelerator for the Level 2 slab pour, or does the spec require non-chloride accelerator given the post-tensioned slab reinforcement?",
                "status": "responded",
                "priority": "urgent",
                "response": "Do NOT use calcium chloride accelerator with post-tensioned reinforcement per ACI 318-19 Section 20.6.6.2. Use non-chloride accelerator (ASTM C494 Type C/E). BASF MasterSet DELVO is approved.",
                "ai_response": "ACI 318-19 Section 20.6.6.2 prohibits calcium chloride in prestressed concrete. Non-chloride accelerator is required. ASTM C494 Type C or Type E recommended.",
                "submitted_by": super_id,
                "assigned_to": architect_id,
                "due": TODAY - timedelta(days=10),
                "responded_at": NOW - timedelta(days=9),
            },
        ]

        for r in rfi_defs:
            rfi = RFI(
                project_id=project_id,
                rfi_number=r["number"],
                subject=r["subject"],
                question=r["question"],
                status=r["status"],
                priority=r["priority"],
                submitted_by=r.get("submitted_by"),
                assigned_to=r.get("assigned_to"),
                response=r.get("response"),
                ai_suggested_response=r.get("ai_response"),
                due_date=r.get("due"),
                responded_at=r.get("responded_at"),
            )
            db.add(rfi)

        await db.commit()

    return {}
