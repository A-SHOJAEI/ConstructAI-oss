"""Enhance daily_logs and punch_list_items for field data capture workflows.

Revision ID: 018
Revises: 017
Create Date: 2025-06-01

daily_logs — add status workflow (draft→submitted→approved), structured
manpower/equipment/delivery/visitor tracking, photos with EXIF, work
narrative, approval columns, and site coordinates for weather auto-populate.

punch_list_items — add GPS coordinates, drawing reference, company
(responsible subcontractor).
"""

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade() -> None:
    # ── daily_logs enhancements ──────────────────────────────────────────
    op.add_column(
        "daily_logs", sa.Column("status", sa.Text(), server_default="draft", nullable=False)
    )
    op.add_column("daily_logs", sa.Column("work_narrative", sa.Text(), nullable=True))
    op.add_column(
        "daily_logs", sa.Column("manpower_by_trade", JSONB(), server_default="[]", nullable=False)
    )
    op.add_column(
        "daily_logs", sa.Column("equipment_entries", JSONB(), server_default="[]", nullable=False)
    )
    op.add_column(
        "daily_logs", sa.Column("deliveries", JSONB(), server_default="[]", nullable=False)
    )
    op.add_column("daily_logs", sa.Column("visitors", JSONB(), server_default="[]", nullable=False))
    op.add_column("daily_logs", sa.Column("photos", JSONB(), server_default="[]", nullable=False))
    op.add_column("daily_logs", sa.Column("location_lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("daily_logs", sa.Column("location_lon", sa.Numeric(9, 6), nullable=True))
    op.add_column(
        "daily_logs",
        sa.Column(
            "approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("daily_logs", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "daily_logs", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_index("idx_daily_logs_project_date", "daily_logs", ["project_id", "log_date"])
    op.create_index("idx_daily_logs_status", "daily_logs", ["status"])

    # ── punch_list_items — create base table then enhance ──────────────
    op.create_table(
        "punch_list_items",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_number", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column(
            "assigned_to",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_date", sa.Date(), nullable=True),
        sa.Column("photos", JSONB(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.add_column("punch_list_items", sa.Column("gps_lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("punch_list_items", sa.Column("gps_lon", sa.Numeric(9, 6), nullable=True))
    op.add_column("punch_list_items", sa.Column("drawing_reference", sa.Text(), nullable=True))
    op.add_column("punch_list_items", sa.Column("company", sa.Text(), nullable=True))

    op.create_index(
        "idx_punch_list_items_project_status", "punch_list_items", ["project_id", "status"]
    )
    op.create_index("idx_punch_list_items_assigned_to", "punch_list_items", ["assigned_to"])
    op.create_index("idx_punch_list_items_company", "punch_list_items", ["company"])


def downgrade() -> None:
    op.drop_index("idx_punch_list_items_company", "punch_list_items")
    op.drop_index("idx_punch_list_items_assigned_to", "punch_list_items")
    op.drop_index("idx_punch_list_items_project_status", "punch_list_items")

    op.drop_table("punch_list_items")

    op.drop_index("idx_daily_logs_status", "daily_logs")
    op.drop_index("idx_daily_logs_project_date", "daily_logs")

    op.drop_column("daily_logs", "submitted_at")
    op.drop_column("daily_logs", "approved_at")
    op.drop_column("daily_logs", "approved_by")
    op.drop_column("daily_logs", "location_lon")
    op.drop_column("daily_logs", "location_lat")
    op.drop_column("daily_logs", "photos")
    op.drop_column("daily_logs", "visitors")
    op.drop_column("daily_logs", "deliveries")
    op.drop_column("daily_logs", "equipment_entries")
    op.drop_column("daily_logs", "manpower_by_trade")
    op.drop_column("daily_logs", "work_narrative")
    op.drop_column("daily_logs", "status")
