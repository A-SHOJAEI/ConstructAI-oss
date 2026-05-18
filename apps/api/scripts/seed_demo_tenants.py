"""Seed 6 demo tenants for the Spark 2 demo runbook.

Idempotent: re-running upserts. UUIDs are uuid5(NS, key) so re-runs produce
stable IDs (operator scripts can target them deterministically).

Each tenant gets:
  - 1 Organization (slug=demo_session_NN, name varies)
  - 1 Project ("Building 24 Renovation")
  - 2 Users: PM (demo.pm@<slug>.test) and FE (demo.fe@<slug>.test)
  - 2 ProjectMember rows linking PM/FE to the project

Run:
    cd apps/api && .venv/bin/python scripts/seed_demo_tenants.py

Verify:
    psql -U constructai -c "
      SELECT slug, name FROM organizations
      WHERE slug LIKE 'demo_session_%' ORDER BY slug"
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.dialects.postgresql import insert

from app.database import async_session
from app.models.organization import Organization
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.utils.security import hash_password

NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Schema constraint: type IN ('commercial', 'residential', 'infrastructure', 'industrial').
# We keep the demo "vertical" label in settings for display; persist a valid type.
# (slug, display name, project type, vertical label)
TENANTS: list[tuple[str, str, str, str]] = [
    ("demo_session_01", "Apex Builders", "residential", "multifamily"),
    ("demo_session_02", "Apex Builders", "residential", "multifamily"),
    ("demo_session_03", "Crestline Civil", "infrastructure", "civil_highway"),
    ("demo_session_04", "Crestline Civil", "infrastructure", "civil_highway"),
    ("demo_session_05", "Meridian Construction", "commercial", "federal_institutional"),
    ("demo_session_06", "Meridian Construction", "commercial", "federal_institutional"),
]


def stable_uuid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "::".join(parts))


async def seed() -> None:
    async with async_session() as db:
        for slug, name, project_type, vertical in TENANTS:
            org_id = stable_uuid("org", slug)
            project_id = stable_uuid("project", slug, "building-24")
            pm_id = stable_uuid("user", slug, "pm")
            fe_id = stable_uuid("user", slug, "fe")
            password = hash_password(f"demo-password-{slug}")

            org_stmt = (
                insert(Organization)
                .values(
                    id=org_id,
                    name=name,
                    slug=slug,
                    type="gc",
                    subscription_tier="growth",
                    settings={"vertical": vertical, "demo": True, "demo_session": slug},
                )
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_={
                        "name": name,
                        "type": "gc",
                        "settings": {
                            "vertical": vertical,
                            "demo": True,
                            "demo_session": slug,
                        },
                    },
                )
            )
            await db.execute(org_stmt)

            for user_id, role, local in [
                (pm_id, "project_manager", "demo.pm"),
                (fe_id, "field_engineer", "demo.fe"),
            ]:
                email = f"{local}@{slug}.test"
                user_stmt = (
                    insert(User)
                    .values(
                        id=user_id,
                        org_id=org_id,
                        email=email,
                        hashed_password=password,
                        full_name=("Demo PM" if role == "project_manager" else "Demo FE")
                        + f" ({slug})",
                        role=role,
                        email_verified=True,
                        is_active=True,
                    )
                    .on_conflict_do_update(
                        index_elements=["email"],
                        set_={
                            "hashed_password": password,
                            "full_name": ("Demo PM" if role == "project_manager" else "Demo FE")
                            + f" ({slug})",
                            "role": role,
                            "email_verified": True,
                            "is_active": True,
                        },
                    )
                )
                await db.execute(user_stmt)

            project_stmt = (
                insert(Project)
                .values(
                    id=project_id,
                    org_id=org_id,
                    name="Building 24 Renovation",
                    project_number=f"DEMO-{slug.split('_')[-1]}-B24",
                    type=project_type,
                    status="active",
                    address="123 Demo Way, Sample City",
                    contract_value=12_500_000,
                    settings={"demo": True, "demo_session": slug, "vertical": vertical},
                    data_source="public_demo",
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "name": "Building 24 Renovation",
                        "type": project_type,
                        "status": "active",
                        "settings": {
                            "demo": True,
                            "demo_session": slug,
                            "vertical": vertical,
                        },
                    },
                )
            )
            await db.execute(project_stmt)

            for user_id, role in [(pm_id, "project_manager"), (fe_id, "field_engineer")]:
                member_id = stable_uuid("member", slug, role)
                member_stmt = (
                    insert(ProjectMember)
                    .values(
                        id=member_id,
                        project_id=project_id,
                        user_id=user_id,
                        role=role,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["project_id", "user_id"],
                    )
                )
                await db.execute(member_stmt)

        await db.commit()
        print(f"Seeded {len(TENANTS)} demo tenants ({TENANTS[0][0]}..{TENANTS[-1][0]})")


if __name__ == "__main__":
    asyncio.run(seed())
