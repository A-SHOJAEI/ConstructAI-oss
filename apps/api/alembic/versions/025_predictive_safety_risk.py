"""Add predictive safety risk tables and expand OSHA columns.

Revision ID: 025
Revises: 024
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Expand osha_inspections with additional CSV columns ---
    op.add_column("osha_inspections", sa.Column("reporting_id", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("state_flag", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("site_address", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("site_zip", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("owner_type", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("safety_hlth", sa.Text(), nullable=True))
    op.add_column("osha_inspections", sa.Column("nr_in_estab", sa.Integer(), nullable=True))
    op.add_column("osha_inspections", sa.Column("union_status", sa.Text(), nullable=True))

    # --- Expand osha_violations with additional CSV columns ---
    op.add_column(
        "osha_violations",
        sa.Column("delete_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("osha_violations", sa.Column("gravity", sa.Integer(), nullable=True))
    op.add_column("osha_violations", sa.Column("nr_exposed", sa.Integer(), nullable=True))
    op.add_column("osha_violations", sa.Column("nr_instances", sa.Integer(), nullable=True))
    op.add_column("osha_violations", sa.Column("initial_penalty", sa.Numeric(12, 2), nullable=True))
    op.add_column("osha_violations", sa.Column("current_penalty", sa.Numeric(12, 2), nullable=True))
    op.add_column("osha_violations", sa.Column("contest_date", sa.Date(), nullable=True))
    op.add_column("osha_violations", sa.Column("final_order_date", sa.Date(), nullable=True))
    op.add_column("osha_violations", sa.Column("emphasis", sa.Text(), nullable=True))

    # --- Create daily_risk_scores table ---
    op.create_table(
        "daily_risk_scores",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score_date", sa.Date(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("category_scores", JSONB, server_default="{}", nullable=False),
        sa.Column("top_risks", JSONB, server_default="[]", nullable=False),
        sa.Column("recommended_mitigations", JSONB, server_default="[]", nullable=False),
        sa.Column("weather_factors", JSONB, server_default="{}", nullable=False),
        sa.Column("schedule_factors", JSONB, server_default="{}", nullable=False),
        sa.Column("project_factors", JSONB, server_default="{}", nullable=False),
        sa.Column("osha_factors", JSONB, server_default="{}", nullable=False),
        sa.Column("safety_briefing", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_daily_risk_scores_project_date", "daily_risk_scores", ["project_id", "score_date"]
    )
    op.create_index("ix_daily_risk_scores_score_date", "daily_risk_scores", ["score_date"])


def downgrade() -> None:
    op.drop_table("daily_risk_scores")

    # Remove added violation columns
    for col in (
        "emphasis",
        "final_order_date",
        "contest_date",
        "current_penalty",
        "initial_penalty",
        "nr_instances",
        "nr_exposed",
        "gravity",
        "delete_flag",
    ):
        op.drop_column("osha_violations", col)

    # Remove added inspection columns
    for col in (
        "union_status",
        "nr_in_estab",
        "safety_hlth",
        "owner_type",
        "site_zip",
        "site_address",
        "state_flag",
        "reporting_id",
    ):
        op.drop_column("osha_inspections", col)
