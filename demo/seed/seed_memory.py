"""
Create 15 project facts in the project_facts table for memory system demo:

- 3 decisions
- 3 constraints
- 3 requirements
- 3 risks
- 3 lessons learned
"""
from datetime import datetime, timedelta, timezone

from app.database import async_session
from app.models import ProjectFact

NOW = datetime.now(timezone.utc)


async def seed_memory(ctx: dict) -> dict:
    project_id = ctx["project_id"]

    facts = [
        # Decisions
        ("decision",
         "Concrete mix changed from 4000 PSI to 5000 PSI Class A for all foundations per structural engineer recommendation due to soil bearing capacity concerns.",
         "meeting_minutes", 0.95),
        ("decision",
         "Steel connection design changed from bolted to welded at moment frame locations per revised seismic analysis. AWS D1.1 qualified welders required.",
         "rfi_response", 0.90),
        ("decision",
         "Curtain wall system changed from stick-built to unitized system to improve installation speed and reduce on-site labor. 5-day schedule improvement expected.",
         "change_order", 0.88),

        # Constraints
        ("constraint",
         "No deliveries before 7:00 AM or after 6:00 PM per City of Roanoke noise ordinance (Section 21-34). Saturday deliveries allowed 8 AM - 4 PM.",
         "contract", 1.0),
        ("constraint",
         "Crane operations prohibited when sustained wind speed exceeds 30 mph or gusts exceed 40 mph per manufacturer requirements and OSHA 1926.1431.",
         "safety_plan", 1.0),
        ("constraint",
         "No concrete pours when ambient temperature is below 35F or forecast to drop below 35F within 24 hours without approved cold weather plan per ACI 306R.",
         "specification", 1.0),

        # Requirements
        ("requirement",
         "Owner requires LEED Silver certification minimum. Energy model shows current design achieves 22% energy savings over ASHRAE 90.1-2019 baseline.",
         "contract", 1.0),
        ("requirement",
         "All structural steel must be domestic (melted and manufactured in USA) per project specification and Buy America provisions.",
         "specification", 1.0),
        ("requirement",
         "Parking garage requires minimum 150 spaces per city zoning approval. Current design provides 156 spaces across 2 underground levels.",
         "permit", 1.0),

        # Risks
        ("risk",
         "Steel delivery delays from primary supplier (Nucor) - 3 week backlog reported as of February 2026. Backup supplier (Commercial Metals) quoted 4-week lead time.",
         "procurement", 0.85),
        ("risk",
         "Curtain wall fabrication shop in Mexico reporting COVID-related absenteeism. Potential 2-week delay on unitized panel delivery for floors 3-5.",
         "vendor_communication", 0.75),
        ("risk",
         "Adjacent property owner has filed complaint about construction vibration. Risk of work stoppage if vibration monitoring exceeds 0.5 in/sec PPV threshold.",
         "incident_report", 0.80),

        # Lessons Learned
        ("lesson_learned",
         "Foundation dewatering took 40% longer than estimated due to clay soil conditions not fully characterized in original geotech report. Recommend additional borings for future projects on riverside sites.",
         "daily_report", 0.92),
        ("lesson_learned",
         "Switching to prefabricated MEP assemblies for restroom risers saved 3 days per floor vs. field-fabricated. Quality also improved with factory QC. Apply to future multi-story projects.",
         "productivity_analysis", 0.88),
        ("lesson_learned",
         "AI safety camera system generated 15% false positives in first week. After 30 days of feedback loop training, false positive rate dropped to 3%. Early calibration period should be expected.",
         "safety_report", 0.90),
    ]

    async with async_session() as db:
        for i, (fact_type, fact_text, source_type, confidence) in enumerate(facts):
            created = NOW - timedelta(days=(15 - i) * 5)
            fact = ProjectFact(
                project_id=project_id,
                fact_type=fact_type,
                fact_text=fact_text,
                source_type=source_type,
                source_id=f"demo_{fact_type}_{i}",
                confidence=confidence,
                valid_from=created,
                metadata={"seeded": True},
            )
            db.add(fact)

        await db.commit()

    return {}
