"""
Create the demo project: Riverside Mixed-Use Development.

- 5-story mixed-use: ground-floor retail, floors 2-3 office, floors 4-5 residential
- $45M contract value, 18-month schedule
- Currently at month 10 (approximately 55% complete)
- Located in Roanoke, Virginia
"""
from datetime import date

from app.database import async_session
from app.models import Project, ProjectMember


async def seed_project(ctx: dict) -> dict:
    async with async_session() as db:
        project = Project(
            org_id=ctx["gc_org_id"],
            name="Riverside Mixed-Use Development",
            project_number="RMD-2025-001",
            type="commercial",
            status="active",
            address="100 Riverside Drive, Roanoke, VA 24011",
            contract_value=45_000_000.00,
            start_date=date(2025, 5, 1),
            end_date=date(2026, 10, 31),
            settings={
                "timezone": "America/New_York",
                "currency": "USD",
                "weather_station": "KROA",
                "cameras_enabled": True,
                "privacy_face_blur": True,
                "privacy_skeleton_only": True,
            },
            metadata={
                "building_type": "mixed_use",
                "stories": 5,
                "gross_area_sf": 125000,
                "structural_system": "steel_frame",
                "foundation_type": "spread_footings",
                "owner": "Riverside Properties LLC",
                "architect": "ArcDesign Associates",
                "description": (
                    "5-story mixed-use development with ground-floor retail, "
                    "floors 2-3 Class A office, and floors 4-5 luxury residential. "
                    "Steel frame with curtain wall facade. Includes 2-level "
                    "underground parking garage (150 spaces)."
                ),
            },
        )
        db.add(project)
        await db.flush()

        members = [
            ProjectMember(project_id=project.id, user_id=ctx["pm_user_id"], role="project_manager"),
            ProjectMember(project_id=project.id, user_id=ctx["safety_user_id"], role="safety_manager"),
            ProjectMember(project_id=project.id, user_id=ctx["super_user_id"], role="superintendent"),
            ProjectMember(project_id=project.id, user_id=ctx["field1_user_id"], role="field_engineer"),
            ProjectMember(project_id=project.id, user_id=ctx["field2_user_id"], role="field_engineer"),
            ProjectMember(project_id=project.id, user_id=ctx["owner_user_id"], role="owner_rep"),
            ProjectMember(project_id=project.id, user_id=ctx["architect_user_id"], role="architect"),
        ]
        db.add_all(members)
        await db.commit()

        return {"project_id": str(project.id)}
