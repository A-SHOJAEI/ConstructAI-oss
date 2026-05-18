"""Preconstruction tables for Phase 2 Estimating, Scheduling, Logistics, Procurement

Revision ID: 003
Revises: 002
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "003"
down_revision: str = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Cost Items ──────────────────────────────────────────────────────
    op.create_table(
        "cost_items",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("base_unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("region", sa.Text, nullable=True),
        sa.Column("bls_series_id", sa.Text, nullable=True),
        sa.Column("data_source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── Cost Estimates ──────────────────────────────────────────────────
    op.create_table(
        "cost_estimates",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("estimate_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("contingency_pct", sa.Numeric(5, 2), nullable=False, server_default="10.0"),
        sa.Column("confidence_low", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_high", sa.Numeric(14, 2), nullable=True),
        sa.Column("monte_carlo_p50", sa.Numeric(14, 2), nullable=True),
        sa.Column("monte_carlo_p80", sa.Numeric(14, 2), nullable=True),
        sa.Column("assumptions", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "estimate_type IN ('conceptual', 'schematic', 'detailed', 'final')",
            name="ck_estimate_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'in_progress', 'review', 'approved')",
            name="ck_estimate_status",
        ),
    )
    op.create_index("idx_cost_estimates_project", "cost_estimates", ["project_id"])

    # ── Estimate Line Items ─────────────────────────────────────────────
    op.create_table(
        "estimate_line_items",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "estimate_id",
            UUID,
            sa.ForeignKey("cost_estimates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cost_item_id", UUID, sa.ForeignKey("cost_items.id"), nullable=True),
        sa.Column("csi_code", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "source IN ('bim_extraction', 'manual', 'parametric', 'historical')",
            name="ck_line_item_source",
        ),
    )
    op.create_index("idx_estimate_line_items_estimate", "estimate_line_items", ["estimate_id"])

    # ── Schedule Baselines ──────────────────────────────────────────────
    op.create_table(
        "schedule_baselines",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("baseline_date", sa.Date, nullable=False),
        sa.Column("total_duration_days", sa.Integer, nullable=True),
        sa.Column("critical_path_length", sa.Integer, nullable=True),
        sa.Column("dcma_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("dcma_results", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_schedule_baselines_project", "schedule_baselines", ["project_id"])

    # ── Schedule Activities ─────────────────────────────────────────────
    op.create_table(
        "schedule_activities",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "baseline_id",
            UUID,
            sa.ForeignKey("schedule_baselines.id"),
            nullable=True,
        ),
        sa.Column("activity_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("duration_days", sa.Integer, nullable=False),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("finish_date", sa.Date, nullable=True),
        sa.Column("early_start", sa.Date, nullable=True),
        sa.Column("early_finish", sa.Date, nullable=True),
        sa.Column("late_start", sa.Date, nullable=True),
        sa.Column("late_finish", sa.Date, nullable=True),
        sa.Column("total_float", sa.Integer, nullable=True),
        sa.Column("free_float", sa.Integer, nullable=True),
        sa.Column("is_critical", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("predecessors", JSONB, nullable=False, server_default="[]"),
        sa.Column("resource_assignments", JSONB, nullable=False, server_default="[]"),
        sa.Column("wbs_code", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="not_started"),
        sa.Column("actual_start", sa.Date, nullable=True),
        sa.Column("actual_finish", sa.Date, nullable=True),
        sa.Column("pct_complete", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('not_started', 'in_progress', 'complete')",
            name="ck_activity_status",
        ),
    )
    op.create_index("idx_schedule_activities_project", "schedule_activities", ["project_id"])
    op.create_index("idx_schedule_activities_baseline", "schedule_activities", ["baseline_id"])

    # ── Site Layouts ────────────────────────────────────────────────────
    op.create_table(
        "site_layouts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("layout_data", JSONB, nullable=False),
        sa.Column("optimization_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("safety_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("efficiency_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("constraints", JSONB, nullable=False, server_default="{}"),
        sa.Column("pareto_rank", sa.Integer, nullable=True),
        sa.Column("generation", sa.Integer, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('draft', 'optimized', 'approved')",
            name="ck_site_layout_status",
        ),
    )
    op.create_index("idx_site_layouts_project", "site_layouts", ["project_id"])

    # ── Delivery Routes ─────────────────────────────────────────────────
    op.create_table(
        "delivery_routes",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("route_date", sa.Date, nullable=False),
        sa.Column("vehicle_id", sa.Text, nullable=True),
        sa.Column("stops", JSONB, nullable=False),
        sa.Column("total_distance_km", sa.Numeric(10, 2), nullable=True),
        sa.Column("total_duration_minutes", sa.Integer, nullable=True),
        sa.Column("total_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("optimization_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("constraints", JSONB, nullable=False, server_default="{}"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "optimization_status IN ('pending', 'optimized', 'dispatched')",
            name="ck_route_optimization_status",
        ),
    )
    op.create_index("idx_delivery_routes_project", "delivery_routes", ["project_id"])

    # ── Price Forecasts ─────────────────────────────────────────────────
    # NOTE: In production this would be a TimescaleDB hypertable but we keep
    # it as a regular table for test compatibility.
    op.create_table(
        "price_forecasts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_category", sa.Text, nullable=False),
        sa.Column("series_id", sa.Text, nullable=False),
        sa.Column("observation_date", sa.Date, nullable=False),
        sa.Column("price_index", sa.Numeric(10, 4), nullable=False),
        sa.Column("forecast_value", sa.Numeric(10, 4), nullable=True),
        sa.Column("forecast_lower", sa.Numeric(10, 4), nullable=True),
        sa.Column("forecast_upper", sa.Numeric(10, 4), nullable=True),
        sa.Column("model_used", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_price_forecasts_category_date",
        "price_forecasts",
        ["material_category", "observation_date"],
    )


def downgrade() -> None:
    op.drop_table("price_forecasts")
    op.drop_table("delivery_routes")
    op.drop_table("site_layouts")
    op.drop_table("schedule_activities")
    op.drop_table("schedule_baselines")
    op.drop_table("estimate_line_items")
    op.drop_table("cost_estimates")
    op.drop_table("cost_items")
