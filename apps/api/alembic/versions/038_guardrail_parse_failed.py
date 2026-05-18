"""Add parse_failed column to guardrail_logs (H-8).

Revision ID: 038
Revises: 037
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guardrail_logs",
        sa.Column(
            "parse_failed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Partial index for "show me all parse failures" queries — cheap to maintain
    # because the vast majority of rows have parse_failed=false.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_guardrail_logs_parse_failed "
        "ON guardrail_logs (created_at DESC) WHERE parse_failed = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_guardrail_logs_parse_failed")
    op.drop_column("guardrail_logs", "parse_failed")
