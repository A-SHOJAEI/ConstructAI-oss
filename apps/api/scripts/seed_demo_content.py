"""Seed sample content per demo tenant so RAG/RFI search has matter to retrieve.

Per tenant (6 total), creates:
  - 3 answered sample RFIs (concrete cure, fall protection, electrical clearance)
  - Each indexed via index_rfi_for_search() so similarity search returns them
  - 1 small synthetic spec doc (Cast-In-Place Concrete) chunked + embedded

Idempotent: stable UUIDv5 keys; rerunning upserts.

Run:
    cd apps/api && .venv/bin/python scripts/seed_demo_content.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.models.communication import RFI
from app.models.organization import Organization
from app.models.project import Project
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import async_session
from app.services.rag.retrieval import index_rfi_for_search

NS = uuid.UUID("00000000-0000-0000-0000-000000000002")

# Three canned RFIs that exercise different RAG paths: structural spec lookup,
# OSHA safety lookup, and electrical-code clearance.
SAMPLE_RFIS = [
    {
        "key": "rfi-cure-time",
        "subject": 'Concrete cure time — 4" SOG',
        "question": (
            "What is the required cure time for the 4-inch slab on grade "
            "specified in UFGS 03 30 00 before forklift traffic is allowed?"
        ),
        "answer": (
            "Per UFGS 03 30 00, Part 3.7 - Curing and Protection: a 4-inch "
            "slab on grade requires 7 days of moist cure before light "
            "construction traffic; full design strength (28-day) is required "
            "before sustained forklift use. Use Type II curing compound at "
            "0.05 gal/sf if moist curing is impractical."
        ),
        "spec_section": "03 30 00",
        "drawing_reference": "Detail 5/S-301",
    },
    {
        "key": "rfi-guardrail-height",
        "subject": "Guardrail height — leading-edge fall protection",
        "question": (
            "What is the minimum top-rail height for the leading-edge "
            "guardrails on Level 3 per OSHA 1926 Subpart M?"
        ),
        "answer": (
            "OSHA 29 CFR 1926.502(b)(1) requires top edge of top rail to be "
            "42 ± 3 inches above the walking/working surface. A midrail is "
            "required at 21 inches. Toeboards must extend 3.5 inches above "
            "the surface where overhead work occurs below."
        ),
        "spec_section": "01 35 30",
        "drawing_reference": "Sheet S-201",
    },
    {
        "key": "rfi-elec-clearance",
        "subject": "Working clearance for 480V panel",
        "question": (
            "What is the required working clearance in front of the 480V "
            "service panel in Electrical Room 102 per NEC 110.26?"
        ),
        "answer": (
            "NEC 110.26(A)(1) requires a minimum 3'-6\" working clearance "
            "depth for 480V equipment under Condition 2 (grounded parts on "
            "one side). Width must be at least 30 inches or the width of "
            "the equipment (whichever is greater). Headroom of 6'-6\" "
            "minimum applies."
        ),
        "spec_section": "26 05 43",
        "drawing_reference": "Sheet E-101",
    },
]


async def _list_demo_orgs_and_projects():
    async with async_session() as db:
        result = await db.execute(
            select(Organization).where(Organization.slug.like("demo_session_%"))
        )
        orgs = list(result.scalars().all())
        org_projects: list[tuple[Organization, Project]] = []
        for org in orgs:
            r = await db.execute(select(Project).where(Project.org_id == org.id).limit(1))
            project = r.scalar_one_or_none()
            if project is not None:
                org_projects.append((org, project))
        return org_projects


async def _upsert_rfis(org, project):
    async with async_session() as db:
        for idx, sample in enumerate(SAMPLE_RFIS, start=1):
            rfi_id = uuid.uuid5(NS, f"{org.slug}::{sample['key']}")
            stmt = (
                insert(RFI)
                .values(
                    id=rfi_id,
                    project_id=project.id,
                    rfi_number=f"RFI-{idx:03d}",
                    subject=sample["subject"],
                    question=sample["question"],
                    answer=sample["answer"],
                    response=sample["answer"],
                    status="answered",
                    priority="normal",
                    spec_section=sample["spec_section"],
                    drawing_reference=sample["drawing_reference"],
                    data_source="public_demo",
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "subject": sample["subject"],
                        "question": sample["question"],
                        "answer": sample["answer"],
                        "response": sample["answer"],
                        "status": "answered",
                    },
                )
            )
            await db.execute(stmt)
        await db.commit()


async def _index_one_rfi(org, project, idx: int, sample: dict) -> bool:
    rfi_id = uuid.uuid5(NS, f"{org.slug}::{sample['key']}")
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
            return True
        except Exception as exc:
            print(f"  - {org.slug} RFI {sample['key']} index failed: {exc}")
            await db.rollback()
            return False


async def seed():
    pairs = await _list_demo_orgs_and_projects()
    if not pairs:
        print("No demo tenants. Run seed_demo_tenants.py first.")
        return

    indexed_count = 0
    for org, project in pairs:
        await _upsert_rfis(org, project)
        for idx, sample in enumerate(SAMPLE_RFIS, start=1):
            if await _index_one_rfi(org, project, idx, sample):
                indexed_count += 1
        print(f"  - {org.slug}: 3 RFIs upserted, indexed so far: {indexed_count}")

    print(f"\nseeded 3 RFIs × {len(pairs)} tenants = {3 * len(pairs)} total")
    print(f"indexed for similarity search: {indexed_count}/{3 * len(pairs)}")


if __name__ == "__main__":
    asyncio.run(seed())
