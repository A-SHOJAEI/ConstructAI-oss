"""Add Procore sync columns and sync_logs table.

Adds data_source and procore_id columns to projects, documents, rfis,
change_orders, and daily_logs tables. Creates the sync_logs table for
tracking Procore data synchronization progress.

Revision ID: 011
Revises: 010
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Projects ---
    op.add_column(
        "projects",
        sa.Column("data_source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "projects",
        sa.Column("procore_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_projects_procore_id",
        "projects",
        ["org_id", "procore_id"],
        unique=True,
        postgresql_where=sa.text("procore_id IS NOT NULL"),
    )

    # --- Documents ---
    op.add_column(
        "documents",
        sa.Column("data_source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "documents",
        sa.Column("procore_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_documents_procore_id",
        "documents",
        ["project_id", "procore_id"],
        unique=True,
        postgresql_where=sa.text("procore_id IS NOT NULL"),
    )

    # --- RFIs ---
    op.add_column(
        "rfis",
        sa.Column("data_source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "rfis",
        sa.Column("procore_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_rfis_procore_id",
        "rfis",
        ["project_id", "procore_id"],
        unique=True,
        postgresql_where=sa.text("procore_id IS NOT NULL"),
    )

    # --- Change Orders ---
    op.add_column(
        "change_orders",
        sa.Column("data_source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "change_orders",
        sa.Column("procore_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_change_orders_procore_id",
        "change_orders",
        ["project_id", "procore_id"],
        unique=True,
        postgresql_where=sa.text("procore_id IS NOT NULL"),
    )

    # --- Daily Logs ---
    op.add_column(
        "daily_logs",
        sa.Column("data_source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "daily_logs",
        sa.Column("procore_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_daily_logs_procore_id",
        "daily_logs",
        ["project_id", "procore_id"],
        unique=True,
        postgresql_where=sa.text("procore_id IS NOT NULL"),
    )

    # --- Sync Logs table ---
    op.create_table(
        "sync_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sync_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entities_synced", JSONB, nullable=False, server_default="{}"),
        sa.Column("errors", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "triggered_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_sync_logs_org_id", "sync_logs", ["org_id"])
    op.create_index("ix_sync_logs_status", "sync_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sync_logs_status")
    op.drop_index("ix_sync_logs_org_id")
    op.drop_table("sync_logs")

    # NOTE: Index/table names come from trusted compile-time constants, not user input.
    for table in ["daily_logs", "change_orders", "rfis", "documents", "projects"]:
        op.drop_index(f"ix_{table}_procore_id", table_name=table)
        op.drop_column(table, "procore_id")
        op.drop_column(table, "data_source")
