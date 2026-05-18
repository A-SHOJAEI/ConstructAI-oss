"""Productivity rates reference table for baseline crew output data.

Stores per-activity crew composition, daily output, and manhours/unit
rates by trade and CSI division for 200+ construction activities.

Revision ID: 014
Revises: 013
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "014"
down_revision: str = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "productivity_rates",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("activity_code", sa.Text, nullable=False),
        sa.Column("activity_name", sa.Text, nullable=False),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("trade", sa.Text, nullable=False),
        sa.Column("crew_composition", JSONB, nullable=False, server_default="{}"),
        sa.Column("crew_size", sa.Numeric(5, 1), nullable=False, server_default="4"),
        sa.Column("daily_output", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("manhours_per_unit", sa.Numeric(8, 4), nullable=False),
        sa.Column("conditions", sa.Text, nullable=False, server_default="normal"),
        sa.Column("data_source", sa.Text, nullable=False, server_default="curated"),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_productivity_rates_trade",
        "productivity_rates",
        ["trade"],
    )
    op.create_index(
        "idx_productivity_rates_csi_division",
        "productivity_rates",
        ["csi_division"],
    )
    op.create_index(
        "idx_productivity_rates_activity_code",
        "productivity_rates",
        ["activity_code"],
        unique=True,
    )
    op.create_index(
        "idx_productivity_rates_conditions",
        "productivity_rates",
        ["conditions"],
    )


def downgrade() -> None:
    op.drop_index("idx_productivity_rates_conditions")
    op.drop_index("idx_productivity_rates_activity_code")
    op.drop_index("idx_productivity_rates_csi_division")
    op.drop_index("idx_productivity_rates_trade")
    op.drop_table("productivity_rates")
