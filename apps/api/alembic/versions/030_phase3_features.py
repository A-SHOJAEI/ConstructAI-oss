"""Phase 3 feature tables: certified payroll, prevailing wage, insurance exports,
EMR calculations, digital twin, drone surveys, earthwork volumes.

Adds 11 new tables.

Revision ID: 030
Revises: 029
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. prevailing_wage_rates
    # ------------------------------------------------------------------
    op.create_table(
        "prevailing_wage_rates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("location_state", sa.Text(), nullable=False),
        sa.Column("location_county", sa.Text(), nullable=True),
        sa.Column("trade", sa.Text(), nullable=False),
        sa.Column("base_rate", sa.Numeric(8, 2), nullable=False),
        sa.Column("fringe_rate", sa.Numeric(8, 2), nullable=False),
        sa.Column("total_rate", sa.Numeric(8, 2), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("determination_number", sa.Text(), nullable=True),
        sa.Column(
            "data_source",
            sa.Text(),
            nullable=False,
            server_default="davis_bacon",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "location_state",
            "location_county",
            "trade",
            "effective_date",
            name="uq_prevailing_wage_location_trade_date",
        ),
    )
    op.create_index(
        "ix_prevailing_wage_state_trade",
        "prevailing_wage_rates",
        ["location_state", "trade"],
    )

    # ------------------------------------------------------------------
    # 2. payroll_records
    # ------------------------------------------------------------------
    op.create_table(
        "payroll_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("worker_name", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("trade", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("pay_period_start", sa.Date(), nullable=False),
        sa.Column("pay_period_end", sa.Date(), nullable=False),
        sa.Column(
            "hours_straight",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "hours_overtime",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "hours_other",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("rate_straight", sa.Numeric(8, 2), nullable=False),
        sa.Column("rate_overtime", sa.Numeric(8, 2), nullable=False),
        sa.Column("gross_pay", sa.Numeric(10, 2), nullable=False),
        sa.Column("deductions", JSONB, nullable=False, server_default="{}"),
        sa.Column("net_pay", sa.Numeric(10, 2), nullable=False),
        sa.Column("fringe_benefits", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "prevailing_wage_rate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prevailing_wage_rates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "compliance_status",
            sa.Text(),
            nullable=False,
            server_default="review",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_payroll_records_project_period",
        "payroll_records",
        ["project_id", "pay_period_start"],
    )

    # ------------------------------------------------------------------
    # 3. certified_payroll_reports
    # ------------------------------------------------------------------
    op.create_table(
        "certified_payroll_reports",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_number", sa.Text(), nullable=False),
        sa.Column("pay_period_start", sa.Date(), nullable=False),
        sa.Column("pay_period_end", sa.Date(), nullable=False),
        sa.Column("contractor_name", sa.Text(), nullable=False),
        sa.Column("contractor_address", sa.Text(), nullable=True),
        sa.Column("project_name", sa.Text(), nullable=False),
        sa.Column("contract_number", sa.Text(), nullable=True),
        sa.Column("payroll_records", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "total_gross_pay",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_fringe",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "certified_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_certified_payroll_project_period",
        "certified_payroll_reports",
        ["project_id", "pay_period_start"],
    )

    # ------------------------------------------------------------------
    # 4. insurance_exports
    # ------------------------------------------------------------------
    op.create_table(
        "insurance_exports",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("export_type", sa.Text(), nullable=False),
        sa.Column("date_range_start", sa.Date(), nullable=False),
        sa.Column("date_range_end", sa.Date(), nullable=False),
        sa.Column("export_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_insurance_exports_org", "insurance_exports", ["org_id"])

    # ------------------------------------------------------------------
    # 5. emr_calculations
    # ------------------------------------------------------------------
    op.create_table(
        "emr_calculations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calculation_year", sa.Integer(), nullable=False),
        sa.Column(
            "actual_losses",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "expected_losses",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "emr_value",
            sa.Numeric(5, 3),
            nullable=False,
            server_default="1.000",
        ),
        sa.Column("payroll_by_class", JSONB, nullable=False, server_default="{}"),
        sa.Column("loss_detail", JSONB, nullable=False, server_default="{}"),
        sa.Column("naics_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_emr_calculations_org_year",
        "emr_calculations",
        ["org_id", "calculation_year"],
    )

    # ------------------------------------------------------------------
    # 6. digital_twin_models (placeholder for future feature)
    # ------------------------------------------------------------------
    op.create_table(
        "digital_twin_models",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_type", sa.Text(), nullable=False, server_default="bim"),
        sa.Column("source_file_url", sa.Text(), nullable=True),
        sa.Column("ifc_schema_version", sa.Text(), nullable=True),
        sa.Column("metadata_", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 7. twin_sensor_links
    # ------------------------------------------------------------------
    op.create_table(
        "twin_sensor_links",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "twin_model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("digital_twin_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sensor_id", sa.Text(), nullable=False),
        sa.Column("sensor_type", sa.Text(), nullable=False),
        sa.Column("element_guid", sa.Text(), nullable=True),
        sa.Column("location", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 8. twin_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "twin_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "twin_model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("digital_twin_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("sensor_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("progress_data", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 9. drone_flight_logs
    # ------------------------------------------------------------------
    op.create_table(
        "drone_flight_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("flight_date", sa.Date(), nullable=False),
        sa.Column("pilot_name", sa.Text(), nullable=True),
        sa.Column("drone_model", sa.Text(), nullable=True),
        sa.Column("flight_plan", JSONB, nullable=False, server_default="{}"),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("area_covered_sqft", sa.Numeric(12, 2), nullable=True),
        sa.Column("weather_conditions", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="completed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 10. drone_captures
    # ------------------------------------------------------------------
    op.create_table(
        "drone_captures",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "flight_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drone_flight_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capture_type", sa.Text(), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("gps_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("gps_lon", sa.Numeric(10, 7), nullable=True),
        sa.Column("altitude_ft", sa.Numeric(8, 2), nullable=True),
        sa.Column("metadata_", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 11. earthwork_volumes
    # ------------------------------------------------------------------
    op.create_table(
        "earthwork_volumes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("survey_date", sa.Date(), nullable=False),
        sa.Column("zone_name", sa.Text(), nullable=True),
        sa.Column("cut_volume_cy", sa.Numeric(12, 2), nullable=True),
        sa.Column("fill_volume_cy", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_volume_cy", sa.Numeric(12, 2), nullable=True),
        sa.Column("surface_model_url", sa.Text(), nullable=True),
        sa.Column("calculation_method", sa.Text(), nullable=True),
        sa.Column("metadata_", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("earthwork_volumes")
    op.drop_table("drone_captures")
    op.drop_table("drone_flight_logs")
    op.drop_table("twin_snapshots")
    op.drop_table("twin_sensor_links")
    op.drop_table("digital_twin_models")
    op.drop_table("emr_calculations")
    op.drop_table("insurance_exports")
    op.drop_table("certified_payroll_reports")
    op.drop_table("payroll_records")
    op.drop_table("prevailing_wage_rates")
