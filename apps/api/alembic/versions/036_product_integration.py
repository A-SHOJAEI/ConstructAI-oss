"""Product integration: CloseoutIQ, HeatShield, WageGuard, CarbonLens, ChangeFlow T&M, SiteScribe.

Revision ID: 036
Revises: 035
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Foundation: Magic link tokens
    # -----------------------------------------------------------------------
    op.create_table(
        "magic_link_tokens",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_email", sa.Text, nullable=True),
        sa.Column("recipient_name", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer, server_default="1"),
        sa.Column("use_count", sa.Integer, server_default="0"),
        sa.Column("metadata_", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_magic_link_token", "magic_link_tokens", ["token_hash"])
    op.create_index("idx_magic_link_project", "magic_link_tokens", ["project_id", "purpose"])

    # -----------------------------------------------------------------------
    # Foundation: Project pricing configs
    # -----------------------------------------------------------------------
    op.create_table(
        "project_pricing_configs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("overhead_pct", sa.Numeric(5, 4), server_default="0.10"),
        sa.Column("profit_pct", sa.Numeric(5, 4), server_default="0.10"),
        sa.Column("bond_pct", sa.Numeric(5, 4), server_default="0.01"),
        sa.Column("labor_burden_pct", sa.Numeric(5, 4), server_default="0.40"),
        sa.Column("material_tax_rate", sa.Numeric(5, 4), server_default="0.0"),
        sa.Column("pricing_model", sa.Text, server_default="tm_plus_markup"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # Foundation: Billing subscriptions
    # -----------------------------------------------------------------------
    op.create_table(
        "billing_subscriptions",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("stripe_customer_id", sa.Text, nullable=True),
        sa.Column("stripe_subscription_id", sa.Text, nullable=True),
        sa.Column("plan_tier", sa.Text, server_default="starter"),
        sa.Column("products_enabled", sa.ARRAY(sa.Text), server_default="{sitescribe,rfi_copilot}"),
        sa.Column("status", sa.Text, server_default="active"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # Foundation: Product usage events
    # -----------------------------------------------------------------------
    op.create_table(
        "product_usage_events",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, server_default="1"),
        sa.Column("metadata_", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_usage_org_product",
        "product_usage_events",
        ["organization_id", "product", "created_at"],
    )

    # -----------------------------------------------------------------------
    # CloseoutIQ: Requirements, warranties, claims, communications
    # -----------------------------------------------------------------------
    op.create_table(
        "closeout_requirements",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("section_title", sa.Text, nullable=True),
        sa.Column("requirement_type", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("spec_reference", sa.Text, nullable=True),
        sa.Column("responsible_sub_id", UUID(as_uuid=True), nullable=True),
        sa.Column("responsible_sub_name", sa.Text, nullable=True),
        sa.Column("responsible_sub_email", sa.Text, nullable=True),
        sa.Column("due_milestone", sa.Text, server_default="substantial_completion"),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("pay_app_linkage", sa.Boolean, server_default="false"),
        sa.Column("status", sa.Text, server_default="not_started"),
        sa.Column("submitted_doc_s3_key", sa.Text, nullable=True),
        sa.Column("submitted_doc_name", sa.Text, nullable=True),
        sa.Column("validation_flags", JSONB, server_default="[]"),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_closeout_project_status", "closeout_requirements", ["project_id", "status"]
    )

    op.create_table(
        "warranty_records",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "closeout_requirement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("closeout_requirements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("warrantor", sa.Text, nullable=False),
        sa.Column("system_description", sa.Text, nullable=True),
        sa.Column("coverage_description", sa.Text, nullable=True),
        sa.Column("warranty_years", sa.Integer, server_default="1"),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("warranty_letter_s3_key", sa.Text, nullable=True),
        sa.Column("status", sa.Text, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_warranty_project", "warranty_records", ["project_id", "status"])
    op.create_index("idx_warranty_expiry", "warranty_records", ["end_date"])

    op.create_table(
        "warranty_claims",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "warranty_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warranty_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reported_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("issue_description", sa.Text, nullable=False),
        sa.Column("photos", JSONB, server_default="[]"),
        sa.Column("claim_date", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.Column("resolution_status", sa.Text, server_default="reported"),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "closeout_communications",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "requirement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("closeout_requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("sent_to", sa.Text, nullable=True),
        sa.Column("message_body", sa.Text, nullable=True),
        sa.Column("magic_link_token_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # HeatShield: Monitoring, workers, breaks, incidents, plans
    # -----------------------------------------------------------------------
    op.create_table(
        "heat_monitoring_configs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("zip_code", sa.Text, nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("threshold_initial_f", sa.Numeric(5, 1), server_default="80.0"),
        sa.Column("threshold_high_heat_f", sa.Numeric(5, 1), server_default="90.0"),
        sa.Column("notification_contacts", JSONB, server_default="[]"),
        sa.Column("crew_start_time", sa.Text, server_default="07:00"),
        sa.Column("monitoring_enabled", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "jobsite_heat_monitoring",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("temperature_f", sa.Numeric(5, 1), nullable=True),
        sa.Column("heat_index_f", sa.Numeric(5, 1), nullable=True),
        sa.Column("wbgt_f", sa.Numeric(5, 1), nullable=True),
        sa.Column("humidity_pct", sa.Numeric(5, 1), nullable=True),
        sa.Column("wind_speed_mph", sa.Numeric(5, 1), nullable=True),
        sa.Column("data_source", sa.Text, server_default="weather_api"),
        sa.Column("threshold_level", sa.Text, server_default="normal"),
        sa.Column("protocol_activated", sa.Boolean, server_default="false"),
        sa.Column("notified_users", JSONB, server_default="[]"),
    )
    op.create_index(
        "idx_heat_project_time",
        "jobsite_heat_monitoring",
        ["project_id", sa.text("timestamp DESC")],
    )

    op.create_table(
        "worker_acclimatization",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text, nullable=False),
        sa.Column("worker_name", sa.Text, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("acclimatization_day", sa.Integer, server_default="1"),
        sa.Column("max_exposure_hours", sa.Numeric(4, 1), server_default="8.0"),
        sa.Column("status", sa.Text, server_default="acclimatizing"),
        sa.Column("last_work_date", sa.Date, nullable=True),
        sa.Column(
            "supervisor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "worker_id", name="uq_worker_project"),
    )

    op.create_table(
        "rest_break_logs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("break_date", sa.Date, nullable=False),
        sa.Column("scheduled_time", sa.Text, nullable=True),
        sa.Column("actual_start", sa.Text, nullable=True),
        sa.Column("actual_end", sa.Text, nullable=True),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("location_compliant", sa.Boolean, server_default="true"),
        sa.Column(
            "logged_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workers_present", sa.Integer, server_default="0"),
        sa.Column("gps_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("gps_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("exception_flag", sa.Boolean, server_default="false"),
        sa.Column("exception_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_breaks_project_date", "rest_break_logs", ["project_id", "break_date"])

    op.create_table(
        "heat_incident_reports",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text, nullable=True),
        sa.Column("worker_name", sa.Text, nullable=True),
        sa.Column("incident_date", sa.Date, nullable=False),
        sa.Column("incident_time", sa.Text, nullable=True),
        sa.Column("symptoms", JSONB, server_default="[]"),
        sa.Column("heat_index_at_incident", sa.Numeric(5, 1), nullable=True),
        sa.Column("acclimatization_day", sa.Integer, nullable=True),
        sa.Column("actions_taken", sa.Text, nullable=True),
        sa.Column("medical_response", sa.Text, server_default="none"),
        sa.Column("outcome", sa.Text, nullable=True),
        sa.Column("root_cause", sa.Text, nullable=True),
        sa.Column("osha_recordable", sa.Boolean, server_default="false"),
        sa.Column("photos", JSONB, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "heat_plans",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("plan_content", JSONB, nullable=False),
        sa.Column("pdf_s3_key", sa.Text, nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # WageGuard: Determinations, configs, payrolls, line items, apprenticeship
    # -----------------------------------------------------------------------
    op.create_table(
        "wage_determinations",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("sam_gov_id", sa.Text, nullable=True),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("county", sa.Text, nullable=False),
        sa.Column("project_type", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("expiration_date", sa.Date, nullable=True),
        sa.Column("classifications", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_wage_det_state", "wage_determinations", ["state", "county", "project_type"]
    )

    op.create_table(
        "project_wage_configs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wage_determination_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wage_determinations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("project_type", sa.Text, server_default="traditional_federal"),
        sa.Column("apprenticeship_required", sa.Boolean, server_default="false"),
        sa.Column("apprenticeship_pct", sa.Numeric(5, 4), server_default="0.15"),
        sa.Column("ira_credit_multiplier", sa.Numeric(5, 2), server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "certified_payrolls_v2",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contractor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("contractor_name", sa.Text, nullable=True),
        sa.Column("week_ending", sa.Date, nullable=False),
        sa.Column("payroll_number", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, server_default="draft"),
        sa.Column("total_hours", sa.Numeric(10, 2), server_default="0"),
        sa.Column("total_gross_pay", sa.Numeric(12, 2), server_default="0"),
        sa.Column("compliance_flags", JSONB, server_default="[]"),
        sa.Column(
            "certified_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("wh347_pdf_s3_key", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_payroll_v2_project", "certified_payrolls_v2", ["project_id", "week_ending"]
    )

    op.create_table(
        "payroll_line_items_v2",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "payroll_id",
            UUID(as_uuid=True),
            sa.ForeignKey("certified_payrolls_v2.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_name", sa.Text, nullable=False),
        sa.Column("worker_last4_ssn_encrypted", sa.Text, nullable=True),
        sa.Column("classification", sa.Text, nullable=True),
        sa.Column("is_apprentice", sa.Boolean, server_default="false"),
        sa.Column("apprentice_program", sa.Text, nullable=True),
        sa.Column("hours_straight", sa.Numeric(6, 2), server_default="0"),
        sa.Column("hours_overtime", sa.Numeric(6, 2), server_default="0"),
        sa.Column("rate_paid", sa.Numeric(8, 2), nullable=True),
        sa.Column("fringe_paid", sa.Numeric(8, 2), nullable=True),
        sa.Column("prevailing_rate", sa.Numeric(8, 2), nullable=True),
        sa.Column("prevailing_fringe", sa.Numeric(8, 2), nullable=True),
        sa.Column("compliant", sa.Boolean, nullable=True),
        sa.Column("deficiency_amount", sa.Numeric(8, 2), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_payroll_items_v2", "payroll_line_items_v2", ["payroll_id"])

    op.create_table(
        "apprenticeship_tracking",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.Column("total_labor_hours", sa.Numeric(10, 2), server_default="0"),
        sa.Column("apprentice_hours", sa.Numeric(10, 2), server_default="0"),
        sa.Column("apprentice_pct", sa.Numeric(5, 4), server_default="0"),
        sa.Column("required_pct", sa.Numeric(5, 4), server_default="0.15"),
        sa.Column("compliant", sa.Boolean, server_default="false"),
        sa.Column("hours_deficit", sa.Numeric(10, 2), server_default="0"),
        sa.Column("projected_compliance_date", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # CarbonLens: Config, materials, EPDs, reports
    # -----------------------------------------------------------------------
    op.create_table(
        "project_carbon_configs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leed_version", sa.Text, server_default="v5"),
        sa.Column("building_area_sf", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_certification", sa.Text, nullable=True),
        sa.Column("baseline_gwp_kgco2e", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "scope_inclusions", JSONB, server_default='["structure", "enclosure", "hardscape"]'
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "epd_records",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("supplier", sa.Text, nullable=True),
        sa.Column("manufacturer", sa.Text, nullable=True),
        sa.Column("product_name", sa.Text, nullable=True),
        sa.Column("epd_program_operator", sa.Text, nullable=True),
        sa.Column("epd_number", sa.Text, nullable=True),
        sa.Column("epd_type", sa.Text, server_default="product_specific"),
        sa.Column("gwp_a1_a3", sa.Numeric(10, 4), nullable=True),
        sa.Column("declared_unit", sa.Text, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("pdf_s3_key", sa.Text, nullable=True),
        sa.Column("verification_status", sa.Text, server_default="pending"),
        sa.Column("ai_extracted_data", JSONB, server_default="{}"),
        sa.Column(
            "verified_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "carbon_material_inventory",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("material_category", sa.Text, nullable=False),
        sa.Column("material_type", sa.Text, nullable=False),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit", sa.Text, nullable=True),
        sa.Column("supplier", sa.Text, nullable=True),
        sa.Column("manufacturer", sa.Text, nullable=True),
        sa.Column("product_name", sa.Text, nullable=True),
        sa.Column(
            "epd_id",
            UUID(as_uuid=True),
            sa.ForeignKey("epd_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gwp_per_unit", sa.Numeric(10, 4), nullable=True),
        sa.Column("total_gwp", sa.Numeric(14, 4), nullable=True),
        sa.Column("baseline_gwp_per_unit", sa.Numeric(10, 4), nullable=True),
        sa.Column("improvement_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("is_carbon_hotspot", sa.Boolean, server_default="false"),
        sa.Column("procurement_status", sa.Text, server_default="specified"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_carbon_material_project", "carbon_material_inventory", ["project_id"])

    op.create_table(
        "carbon_reports",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_type", sa.Text, nullable=False),
        sa.Column("total_gwp_kgco2e", sa.Numeric(14, 4), nullable=True),
        sa.Column("gwp_per_sf", sa.Numeric(10, 4), nullable=True),
        sa.Column("baseline_comparison_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("hotspot_materials", JSONB, server_default="[]"),
        sa.Column("category_breakdown", JSONB, server_default="[]"),
        sa.Column("epd_coverage_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("mitigation_narrative", sa.Text, nullable=True),
        sa.Column("leed_credits_achieved", JSONB, server_default="{}"),
        sa.Column("pdf_s3_key", sa.Text, nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # ChangeFlow T&M: Entries, negotiations
    # -----------------------------------------------------------------------
    op.create_table(
        "tm_entries",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("change_event_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_date", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.Column("entry_type", sa.Text, nullable=False),
        sa.Column("worker_name", sa.Text, nullable=True),
        sa.Column("classification", sa.Text, nullable=True),
        sa.Column("straight_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("overtime_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("labor_rate", sa.Numeric(8, 2), nullable=True),
        sa.Column("ot_rate", sa.Numeric(8, 2), nullable=True),
        sa.Column("material_description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("unit", sa.Text, nullable=True),
        sa.Column("unit_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("vendor", sa.Text, nullable=True),
        sa.Column("equipment_type", sa.Text, nullable=True),
        sa.Column("equipment_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("equipment_rate", sa.Numeric(8, 2), nullable=True),
        sa.Column("sub_name", sa.Text, nullable=True),
        sa.Column("sub_scope", sa.Text, nullable=True),
        sa.Column("sub_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("gps_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("gps_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("photos", JSONB, server_default="[]"),
        sa.Column("voice_note_s3_key", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_tm_change_event", "tm_entries", ["change_event_id"])
    op.create_index("idx_tm_project", "tm_entries", ["project_id", "entry_date"])

    op.create_table(
        "cor_negotiations",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("cor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "acted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("acted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # -----------------------------------------------------------------------
    # SiteScribe: Report sources
    # -----------------------------------------------------------------------
    op.create_table(
        "report_sources",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("daily_report_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("s3_key", sa.Text, nullable=True),
        sa.Column("filename", sa.Text, nullable=True),
        sa.Column("mime_type", sa.Text, nullable=True),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("text_content", sa.Text, nullable=True),
        sa.Column("ai_tags", JSONB, server_default="{}"),
        sa.Column("exif_data", JSONB, server_default="{}"),
        sa.Column("processing_status", sa.Text, server_default="pending"),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_report_sources", "report_sources", ["daily_report_id"])


def downgrade() -> None:
    tables = [
        "report_sources",
        "cor_negotiations",
        "tm_entries",
        "carbon_reports",
        "carbon_material_inventory",
        "epd_records",
        "project_carbon_configs",
        "apprenticeship_tracking",
        "payroll_line_items_v2",
        "certified_payrolls_v2",
        "project_wage_configs",
        "wage_determinations",
        "heat_plans",
        "heat_incident_reports",
        "rest_break_logs",
        "worker_acclimatization",
        "jobsite_heat_monitoring",
        "heat_monitoring_configs",
        "closeout_communications",
        "warranty_claims",
        "warranty_records",
        "closeout_requirements",
        "product_usage_events",
        "billing_subscriptions",
        "project_pricing_configs",
        "magic_link_tokens",
    ]
    for table in tables:
        op.drop_table(table)
