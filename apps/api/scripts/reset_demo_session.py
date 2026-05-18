"""Reset transient session state for one demo tenant between demo runs.

Preserves the RAG index (document_chunks), org / users / project rows.
Clears: ai_usage_logs, rfi_resolution_logs, semantic cache via service.

Usage:
    .venv/bin/python scripts/reset_demo_session.py --tenant demo_session_01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from app.database import async_session

# Tables to truncate per tenant. Each row is (table, where_clause_template).
# Some tables join through projects; rfi_resolution_logs uses project_id directly.
TENANT_SCOPED_TABLES = [
    ("ai_usage_logs", "org_id = :org_id"),
    (
        "rfi_resolution_logs",
        "project_id IN (SELECT id FROM projects WHERE org_id = :org_id)",
    ),
    ("audit_logs", "org_id = :org_id"),
]


async def reset(tenant_slug: str) -> None:
    async with async_session() as db:
        result = await db.execute(
            text("SELECT id FROM organizations WHERE slug = :slug"),
            {"slug": tenant_slug},
        )
        row = result.first()
        if row is None:
            print(f"WARN: no organization with slug '{tenant_slug}' — nothing to reset")
            return
        org_id = row[0]

        rows_cleared = 0
        for table, where in TENANT_SCOPED_TABLES:
            # Each table gets its own session/transaction so a failure on one
            # doesn't poison the rest. Idempotent: if a table doesn't exist,
            # skip it without aborting.
            async with async_session() as inner:
                try:
                    exists = await inner.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_name = :t AND table_schema = current_schema()"
                        ),
                        {"t": table},
                    )
                    if exists.first() is None:
                        continue
                    stmt = text(f"DELETE FROM {table} WHERE {where}")
                    deleted = await inner.execute(stmt, {"org_id": org_id})
                    rows_cleared += deleted.rowcount or 0
                    await inner.commit()
                except Exception as exc:
                    await inner.rollback()
                    print(f"  - skipped {table}: {type(exc).__name__}: {exc}")

        print(f"reset {tenant_slug}: cleared {rows_cleared} session rows (RAG index preserved)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="organization slug (e.g. demo_session_01)")
    args = ap.parse_args()
    asyncio.run(reset(args.tenant))
