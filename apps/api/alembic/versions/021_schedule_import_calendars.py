"""Add schedule import and calendar support columns.

Revision ID: 021
Revises: 020
Create Date: 2026-03-04

Alters: schedule_baselines — adds source_file, source_format, calendars, data_date.
Alters: schedule_activities — adds calendar_id, original_id, wbs_path.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- schedule_baselines ---------------------------------------------------
    op.add_column("schedule_baselines", sa.Column("source_file", sa.Text(), nullable=True))
    op.add_column("schedule_baselines", sa.Column("source_format", sa.Text(), nullable=True))
    op.add_column(
        "schedule_baselines",
        sa.Column("calendars", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("schedule_baselines", sa.Column("data_date", sa.Date(), nullable=True))

    # --- schedule_activities --------------------------------------------------
    op.add_column("schedule_activities", sa.Column("calendar_id", sa.Text(), nullable=True))
    op.add_column("schedule_activities", sa.Column("original_id", sa.Text(), nullable=True))
    op.add_column("schedule_activities", sa.Column("wbs_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedule_activities", "wbs_path")
    op.drop_column("schedule_activities", "original_id")
    op.drop_column("schedule_activities", "calendar_id")

    op.drop_column("schedule_baselines", "data_date")
    op.drop_column("schedule_baselines", "calendars")
    op.drop_column("schedule_baselines", "source_format")
    op.drop_column("schedule_baselines", "source_file")
