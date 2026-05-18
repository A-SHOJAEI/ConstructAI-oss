"""Add fred_price_history table for FRED backfill data

Revision ID: 009
Revises: 008
Create Date: 2026-03-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fred_price_history",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("series_id", sa.Text(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("csi_division", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # Unique constraint for idempotent upserts
    op.create_index(
        "ix_fred_price_history_series_date",
        "fred_price_history",
        ["series_id", "observation_date"],
        unique=True,
    )
    op.create_index(
        "ix_fred_price_history_category",
        "fred_price_history",
        ["category"],
    )
    op.create_index(
        "ix_fred_price_history_csi",
        "fred_price_history",
        ["csi_division"],
    )


def downgrade() -> None:
    op.drop_index("ix_fred_price_history_csi", table_name="fred_price_history")
    op.drop_index("ix_fred_price_history_category", table_name="fred_price_history")
    op.drop_index("ix_fred_price_history_series_date", table_name="fred_price_history")
    op.drop_table("fred_price_history")
