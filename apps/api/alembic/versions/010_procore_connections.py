"""Add procore_connections table for OAuth token storage.

Revision ID: 010
Revises: 009
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "procore_connections",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("procore_company_id", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "connected_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.Text(), nullable=False, server_default="connected"),
    )
    op.create_index(
        "ix_procore_conn_org_id", "procore_connections", ["organization_id"], unique=True
    )
    op.create_index("ix_procore_conn_company_id", "procore_connections", ["procore_company_id"])


def downgrade() -> None:
    op.drop_index("ix_procore_conn_company_id")
    op.drop_index("ix_procore_conn_org_id")
    op.drop_table("procore_connections")
