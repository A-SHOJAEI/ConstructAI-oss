"""Regional cost factors table for metro-level cost adjustments.

Stores per-metro material, labor, and equipment cost multipliers
relative to the national average. Supports lookup by city, state,
or zip prefix with nearest-metro fallback via lat/lon.

Revision ID: 013
Revises: 012
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "013"
down_revision: str = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "regional_cost_factors",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("city", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("state_abbr", sa.Text, nullable=False),
        sa.Column("zip_prefix", sa.Text, nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("material_factor", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("labor_factor", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("equipment_factor", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("composite_factor", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("data_source", sa.Text, nullable=False, server_default="curated"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_regional_cost_factors_city_state",
        "regional_cost_factors",
        ["city", "state_abbr"],
    )
    op.create_index(
        "idx_regional_cost_factors_zip",
        "regional_cost_factors",
        ["zip_prefix"],
    )
    op.create_index(
        "idx_regional_cost_factors_state",
        "regional_cost_factors",
        ["state_abbr"],
    )


def downgrade() -> None:
    op.drop_index("idx_regional_cost_factors_state")
    op.drop_index("idx_regional_cost_factors_zip")
    op.drop_index("idx_regional_cost_factors_city_state")
    op.drop_table("regional_cost_factors")
