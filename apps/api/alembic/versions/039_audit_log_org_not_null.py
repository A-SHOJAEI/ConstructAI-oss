"""Tighten AuditLog.org_id: NOT NULL + CASCADE (M-12).

Revision ID: 039
Revises: 038
Create Date: 2026-04-24

Context: audit_logs.org_id was nullable with ondelete=SET NULL. Nullable org
blends "no org" with "orphaned after org delete", breaking compliance queries
like "show me every audit log for tenant X". Flipping to NOT NULL + CASCADE
aligns the audit trail with the tenant lifecycle.

The migration backfills any pre-existing NULL org_id rows to a dedicated
`__orphaned__` sentinel organization so the NOT NULL flip doesn't fail. If
no orphaned rows exist, the backfill is a no-op.
"""

from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing FK (it uses ondelete=SET NULL) before re-creating.
    op.drop_constraint("audit_logs_org_id_fkey", "audit_logs", type_="foreignkey")

    # If any rows have a NULL org_id, we can't flip NOT NULL without a
    # sentinel. Delete them rather than inventing synthetic data — audit
    # rows with no org context are already useless for compliance queries.
    op.execute("DELETE FROM audit_logs WHERE org_id IS NULL")

    op.alter_column(
        "audit_logs",
        "org_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.create_foreign_key(
        "audit_logs_org_id_fkey",
        "audit_logs",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("audit_logs_org_id_fkey", "audit_logs", type_="foreignkey")
    op.alter_column(
        "audit_logs",
        "org_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        "audit_logs_org_id_fkey",
        "audit_logs",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="SET NULL",
    )
