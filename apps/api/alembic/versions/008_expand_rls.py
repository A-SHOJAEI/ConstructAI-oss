"""Expand Row-Level Security to all project-scoped tables

Revision ID: 008
Revises: 007
Create Date: 2026-02-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that have a direct project_id column and need RLS policies
# that filter via projects.org_id = current_tenant_id().
_DIRECT_PROJECT_ID_TABLES: list[str] = [
    "documents",
    "safety_alerts",
    "safety_zones",
    "cameras",
    "evm_snapshots",
    "eac_forecasts",
    "change_orders",
    "schedule_activities",
    "schedule_baselines",
    "daily_reports",
    "meeting_minutes",
    "rfis",
    "submittals",
    "inspections",
    "defect_reports",
    "compliance_checks",
    "daily_logs",
    "crew_productivity",
]

# Tables that do NOT have a direct project_id column.
# They relate to a project through an intermediate table and
# need a multi-hop sub-select for their RLS policy.
_INDIRECT_TABLES: dict[str, str] = {
    # table_name -> SQL USING clause
    "document_chunks": (
        "document_id IN ("
        "  SELECT d.id FROM documents d"
        "  JOIN projects p ON d.project_id = p.id"
        "  WHERE p.org_id = current_tenant_id()"
        ")"
    ),
    "document_embeddings": (
        "chunk_id IN ("
        "  SELECT dc.id FROM document_chunks dc"
        "  JOIN documents d ON dc.document_id = d.id"
        "  JOIN projects p ON d.project_id = p.id"
        "  WHERE p.org_id = current_tenant_id()"
        ")"
    ),
}


def upgrade() -> None:
    # NOTE: DDL statements (ALTER TABLE, CREATE POLICY) do not support bind
    # parameters. The table names below come from trusted compile-time
    # constants defined in this migration file — they are NOT user input.
    # We use sa.text() wrappers for consistency with SQLAlchemy conventions.

    # ── Direct project_id tables ───────────────────────────────────────
    for table in _DIRECT_PROJECT_ID_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation_{table} ON {table}"
                f" USING (project_id IN ("
                f"   SELECT id FROM projects WHERE org_id = current_tenant_id()"
                f" ))"
            )
        )

    # ── Indirect tables (no project_id column) ─────────────────────────
    for table, using_clause in _INDIRECT_TABLES.items():
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(f"CREATE POLICY tenant_isolation_{table} ON {table} USING ({using_clause})")
        )


def downgrade() -> None:
    # ── Drop indirect table policies ───────────────────────────────────
    for table in reversed(list(_INDIRECT_TABLES.keys())):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # ── Drop direct project_id table policies ──────────────────────────
    for table in reversed(_DIRECT_PROJECT_ID_TABLES):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
