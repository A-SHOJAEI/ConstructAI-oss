"""Add change order lifecycle (PCO/COR) and AIA G702/G703 pay application tables.

Revision ID: 019
Revises: 018
Create Date: 2025-06-15

New tables: potential_change_orders, change_order_requests, cor_pco_links,
schedule_of_values, pay_applications, pay_application_line_items.

Alters: change_orders — adds lifecycle columns (cor_id, approved_date,
cost breakdown, contract adjustment tracking).
"""

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade() -> None:
    # ── potential_change_orders ────────────────────────────────────────────
    op.create_table(
        "potential_change_orders",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pco_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column(
            "originated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("labor_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("material_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("equipment_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("subcontractor_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("overhead_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("profit_markup_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("schedule_impact_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("spec_section", sa.Text(), nullable=True),
        sa.Column("drawing_reference", sa.Text(), nullable=True),
        sa.Column("attachments", JSONB(), server_default="[]", nullable=False),
        sa.Column("risk_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("ai_analysis", JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "pco_number", name="uq_project_pco_number"),
    )
    op.create_index("idx_pco_project_id", "potential_change_orders", ["project_id"])
    op.create_index("idx_pco_project_status", "potential_change_orders", ["project_id", "status"])

    # ── change_order_requests ─────────────────────────────────────────────
    op.create_table(
        "change_order_requests",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cor_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("markup_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("overhead_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("cor_adjustment", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("schedule_impact_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "submitted_to",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "cor_number", name="uq_project_cor_number"),
    )
    op.create_index("idx_cor_project_id", "change_order_requests", ["project_id"])
    op.create_index("idx_cor_project_status", "change_order_requests", ["project_id", "status"])

    # ── cor_pco_links (many-to-many) ──────────────────────────────────────
    op.create_table(
        "cor_pco_links",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "cor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("change_order_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pco_id",
            UUID(as_uuid=True),
            sa.ForeignKey("potential_change_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("cor_id", "pco_id", name="uq_cor_pco"),
    )
    op.create_index("idx_cor_pco_cor_id", "cor_pco_links", ["cor_id"])
    op.create_index("idx_cor_pco_pco_id", "cor_pco_links", ["pco_id"])

    # ── change_orders — add lifecycle columns ─────────────────────────────
    op.add_column(
        "change_orders",
        sa.Column(
            "cor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("change_order_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "change_orders", sa.Column("approved_date", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "change_orders", sa.Column("executed_date", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "change_orders",
        sa.Column("labor_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("material_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("equipment_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("subcontractor_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("overhead_cost", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("markup_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders",
        sa.Column("overhead_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "change_orders", sa.Column("original_contract_sum", sa.Numeric(16, 2), nullable=True)
    )
    op.add_column("change_orders", sa.Column("previous_cos_sum", sa.Numeric(16, 2), nullable=True))
    op.add_column("change_orders", sa.Column("this_co_amount", sa.Numeric(16, 2), nullable=True))
    op.add_column("change_orders", sa.Column("new_contract_sum", sa.Numeric(16, 2), nullable=True))

    # ── schedule_of_values ────────────────────────────────────────────────
    op.create_table(
        "schedule_of_values",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_number", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scheduled_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("csi_code", sa.Text(), nullable=True),
        sa.Column(
            "change_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("change_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_change_order_line", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "item_number", name="uq_project_sov_item"),
    )
    op.create_index("idx_sov_project_id", "schedule_of_values", ["project_id"])

    # ── pay_applications (G702) ───────────────────────────────────────────
    op.create_table(
        "pay_applications",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("application_number", sa.Integer(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("contractor_info", JSONB(), server_default="{}", nullable=False),
        sa.Column("architect_info", JSONB(), server_default="{}", nullable=False),
        sa.Column("original_contract_sum", sa.Numeric(16, 2), nullable=False),
        sa.Column("net_change_by_cos", sa.Numeric(16, 2), server_default="0", nullable=False),
        sa.Column("contract_sum_to_date", sa.Numeric(16, 2), nullable=False),
        sa.Column(
            "total_completed_and_stored", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column("retainage_pct", sa.Numeric(5, 2), server_default="10.00", nullable=False),
        sa.Column(
            "retainage_work_completed", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column(
            "retainage_stored_materials", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column("total_retainage", sa.Numeric(16, 2), server_default="0", nullable=False),
        sa.Column(
            "total_earned_less_retainage", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column(
            "less_previous_certificates", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column("current_payment_due", sa.Numeric(16, 2), server_default="0", nullable=False),
        sa.Column(
            "balance_to_finish_including_retainage",
            sa.Numeric(16, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column(
            "submitted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "certified_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "application_number", name="uq_project_pay_app_number"),
    )
    op.create_index("idx_pay_app_project_id", "pay_applications", ["project_id"])
    op.create_index("idx_pay_app_project_status", "pay_applications", ["project_id", "status"])

    # ── pay_application_line_items (G703) ─────────────────────────────────
    op.create_table(
        "pay_application_line_items",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "pay_application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pay_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sov_id",
            UUID(as_uuid=True),
            sa.ForeignKey("schedule_of_values.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("item_number", sa.Text(), nullable=False),
        sa.Column("description_of_work", sa.Text(), nullable=False),
        sa.Column("scheduled_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("work_completed_previous", sa.Numeric(16, 2), server_default="0", nullable=False),
        sa.Column(
            "work_completed_this_period", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column(
            "materials_presently_stored", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column(
            "total_completed_and_stored", sa.Numeric(16, 2), server_default="0", nullable=False
        ),
        sa.Column("percent_complete", sa.Numeric(7, 4), server_default="0", nullable=False),
        sa.Column("balance_to_finish", sa.Numeric(16, 2), server_default="0", nullable=False),
        sa.Column("retainage_pct", sa.Numeric(5, 2), server_default="10.00", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("pay_application_id", "item_number", name="uq_pay_app_line_item"),
    )
    op.create_index(
        "idx_pay_app_li_pay_app_id", "pay_application_line_items", ["pay_application_id"]
    )
    op.create_index("idx_pay_app_li_sov_id", "pay_application_line_items", ["sov_id"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("pay_application_line_items")
    op.drop_table("pay_applications")
    op.drop_table("schedule_of_values")

    # Remove lifecycle columns from change_orders
    op.drop_column("change_orders", "new_contract_sum")
    op.drop_column("change_orders", "this_co_amount")
    op.drop_column("change_orders", "previous_cos_sum")
    op.drop_column("change_orders", "original_contract_sum")
    op.drop_column("change_orders", "overhead_pct")
    op.drop_column("change_orders", "markup_pct")
    op.drop_column("change_orders", "overhead_cost")
    op.drop_column("change_orders", "subcontractor_cost")
    op.drop_column("change_orders", "equipment_cost")
    op.drop_column("change_orders", "material_cost")
    op.drop_column("change_orders", "labor_cost")
    op.drop_column("change_orders", "executed_date")
    op.drop_column("change_orders", "approved_date")
    op.drop_column("change_orders", "cor_id")

    op.drop_table("cor_pco_links")
    op.drop_table("change_order_requests")
    op.drop_table("potential_change_orders")
