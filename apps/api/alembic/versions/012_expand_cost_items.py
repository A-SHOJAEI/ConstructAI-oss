"""Expand cost_items table for DDC CWICR data ingestion.

Adds cost breakdown columns (material, labor, equipment), CSI MasterFormat code,
productivity metrics (crew size, daily output, manhours), and uncertainty bounds
for Monte Carlo simulation.

Revision ID: 012
Revises: 011
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: str = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New columns on cost_items ─────────────────────────────────────
    op.add_column("cost_items", sa.Column("csi_code", sa.Text, nullable=True))
    op.add_column("cost_items", sa.Column("material_cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("cost_items", sa.Column("labor_cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("cost_items", sa.Column("equipment_cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("cost_items", sa.Column("unit_of_measure", sa.Text, nullable=True))
    op.add_column("cost_items", sa.Column("crew_size", sa.Numeric(6, 2), nullable=True))
    op.add_column("cost_items", sa.Column("daily_output", sa.Numeric(12, 4), nullable=True))
    op.add_column("cost_items", sa.Column("manhours_per_unit", sa.Numeric(10, 4), nullable=True))
    op.add_column("cost_items", sa.Column("uncertainty_min", sa.Numeric(5, 4), nullable=True))
    op.add_column("cost_items", sa.Column("uncertainty_max", sa.Numeric(5, 4), nullable=True))
    op.add_column(
        "cost_items",
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Indexes ───────────────────────────────────────────────────────
    op.create_index("idx_cost_items_csi_code", "cost_items", ["csi_code"])
    op.create_index("idx_cost_items_data_source", "cost_items", ["data_source"])
    op.create_index(
        "idx_cost_items_category_unit",
        "cost_items",
        ["category", "unit"],
    )


def downgrade() -> None:
    op.drop_index("idx_cost_items_category_unit")
    op.drop_index("idx_cost_items_data_source")
    op.drop_index("idx_cost_items_csi_code")

    op.drop_column("cost_items", "last_updated")
    op.drop_column("cost_items", "uncertainty_max")
    op.drop_column("cost_items", "uncertainty_min")
    op.drop_column("cost_items", "manhours_per_unit")
    op.drop_column("cost_items", "daily_output")
    op.drop_column("cost_items", "crew_size")
    op.drop_column("cost_items", "unit_of_measure")
    op.drop_column("cost_items", "equipment_cost")
    op.drop_column("cost_items", "labor_cost")
    op.drop_column("cost_items", "material_cost")
    op.drop_column("cost_items", "csi_code")
