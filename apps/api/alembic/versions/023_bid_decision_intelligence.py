"""Bid/No-Bid Decision Intelligence tables.

Revision ID: 023
Revises: 022
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bid_opportunities",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_name", sa.Text(), nullable=True),
        sa.Column("project_type", sa.Text(), nullable=True),
        sa.Column("delivery_method", sa.Text(), nullable=True),
        sa.Column("estimated_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("bid_due_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="evaluating"),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("actual_margin", sa.Numeric(8, 4), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_bid_opportunities_org_id", "bid_opportunities", ["org_id"])
    op.create_index("ix_bid_opportunities_status", "bid_opportunities", ["status"])

    op.create_table(
        "bid_decisions",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "opportunity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("bid_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ai_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_recommendation", sa.Text(), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("human_decision", sa.Text(), nullable=True),
        sa.Column("human_notes", sa.Text(), nullable=True),
        sa.Column("factor_scores", JSONB(), nullable=False, server_default="{}"),
        sa.Column("win_probability", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_bid_decisions_opportunity_id", "bid_decisions", ["opportunity_id"])

    op.create_table(
        "bid_scoring_factors",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("factor_name", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("custom_params", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("org_id", "factor_name", name="uq_bid_scoring_factor"),
    )


def downgrade() -> None:
    op.drop_table("bid_scoring_factors")
    op.drop_table("bid_decisions")
    op.drop_table("bid_opportunities")
