"""
Create demo organizations and users with different roles.

Organizations:
  1. Riverside Properties LLC (owner/developer)
  2. BuildRight Construction (general contractor)
  3. ArcDesign Associates (architect)

Users per org with construction-appropriate roles.
"""
from app.database import async_session
from app.models import Organization, User
from app.utils.security import hash_password


async def seed_tenants(ctx: dict) -> dict:
    async with async_session() as db:
        # --- Organizations ---
        owner_org = Organization(
            name="Riverside Properties LLC",
            slug="riverside",
            type="owner",
            subscription_tier="growth",
            settings={"industry": "commercial", "region": "mid-atlantic"},
        )
        gc_org = Organization(
            name="BuildRight Construction",
            slug="buildright",
            type="gc",
            subscription_tier="growth",
            settings={"industry": "commercial", "trades": ["general", "concrete", "steel"]},
        )
        arch_org = Organization(
            name="ArcDesign Associates",
            slug="arcdesign",
            type="architect",
            subscription_tier="startup",
            settings={"industry": "commercial"},
        )
        db.add_all([owner_org, gc_org, arch_org])
        await db.flush()

        # --- Users ---
        pw = hash_password("Demo2026!")

        users = [
            # Platform admin
            User(org_id=gc_org.id, email="admin@constructai.dev", hashed_password=pw,
                 full_name="System Administrator", role="org_admin", email_verified=True),
            # BuildRight (GC)
            User(org_id=gc_org.id, email="pm@buildright.dev", hashed_password=pw,
                 full_name="Sarah Chen", role="project_manager", email_verified=True),
            User(org_id=gc_org.id, email="super@buildright.dev", hashed_password=pw,
                 full_name="Mike Rodriguez", role="project_admin", email_verified=True),
            User(org_id=gc_org.id, email="safety@buildright.dev", hashed_password=pw,
                 full_name="James Okafor", role="safety_manager", email_verified=True),
            User(org_id=gc_org.id, email="field1@buildright.dev", hashed_password=pw,
                 full_name="Emily Tran", role="field_engineer", email_verified=True),
            User(org_id=gc_org.id, email="field2@buildright.dev", hashed_password=pw,
                 full_name="Carlos Mendez", role="subcontractor", email_verified=True),
            # Riverside (Owner)
            User(org_id=owner_org.id, email="owner@riverside.dev", hashed_password=pw,
                 full_name="David Riverside", role="owner_rep", email_verified=True),
            User(org_id=owner_org.id, email="exec@riverside.dev", hashed_password=pw,
                 full_name="Amanda Foster", role="readonly", email_verified=True),
            # ArcDesign (Architect)
            User(org_id=arch_org.id, email="architect@arcdesign.dev", hashed_password=pw,
                 full_name="Robert Kim", role="field_engineer", email_verified=True),
        ]
        db.add_all(users)
        await db.commit()

        return {
            "owner_org_id": str(owner_org.id),
            "gc_org_id": str(gc_org.id),
            "arch_org_id": str(arch_org.id),
            "admin_user_id": str(users[0].id),
            "pm_user_id": str(users[1].id),
            "super_user_id": str(users[2].id),
            "safety_user_id": str(users[3].id),
            "field1_user_id": str(users[4].id),
            "field2_user_id": str(users[5].id),
            "owner_user_id": str(users[6].id),
            "exec_user_id": str(users[7].id),
            "architect_user_id": str(users[8].id),
        }
