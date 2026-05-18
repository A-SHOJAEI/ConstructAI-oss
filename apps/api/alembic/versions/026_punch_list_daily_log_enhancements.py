"""Punch list walkthrough grouping and daily log safety fields.

Adds:
- punch_lists table for walkthrough grouping
- punch_list_items: punch_list_id, verified_by, date_verified, spec_section
- daily_logs: safety_incidents, safety_topic_discussed, weather_delay_hours

Revision ID: 026
Revises: 025
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- punch_lists table (walkthrough grouping) --
    op.create_table(
        "punch_lists",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("walk_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("participants", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_punch_lists_project_status", "punch_lists", ["project_id", "status"])

    # -- punch_list_items enhancements --
    op.add_column(
        "punch_list_items",
        sa.Column(
            "punch_list_id",
            UUID(as_uuid=True),
            sa.ForeignKey("punch_lists.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "punch_list_items",
        sa.Column(
            "verified_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "punch_list_items",
        sa.Column(
            "date_verified",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "punch_list_items",
        sa.Column(
            "spec_section",
            sa.Text(),
            nullable=True,
        ),
    )

    op.create_index("idx_punch_list_items_punch_list_id", "punch_list_items", ["punch_list_id"])

    # -- daily_logs safety & delay fields --
    op.add_column("daily_logs", sa.Column("safety_incidents", sa.Text(), nullable=True))
    op.add_column("daily_logs", sa.Column("safety_topic_discussed", sa.Text(), nullable=True))
    op.add_column("daily_logs", sa.Column("weather_delay_hours", sa.Numeric(5, 1), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_logs", "weather_delay_hours")
    op.drop_column("daily_logs", "safety_topic_discussed")
    op.drop_column("daily_logs", "safety_incidents")

    op.drop_index("idx_punch_list_items_punch_list_id", "punch_list_items")
    op.drop_column("punch_list_items", "spec_section")
    op.drop_column("punch_list_items", "date_verified")
    op.drop_column("punch_list_items", "verified_by")
    op.drop_column("punch_list_items", "punch_list_id")

    op.drop_index("idx_punch_lists_project_status", "punch_lists")
    op.drop_table("punch_lists")
