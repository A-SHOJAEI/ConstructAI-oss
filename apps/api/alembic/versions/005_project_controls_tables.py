"""Project controls, quality, productivity, communication, and team workflow tables

Revision ID: 005
Revises: 004
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "005"
down_revision: str = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── EVM Snapshots ──────────────────────────────────────────────────────
    op.create_table(
        "evm_snapshots",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("bac", sa.Numeric(14, 2), nullable=False),
        sa.Column("pv", sa.Numeric(14, 2), nullable=False),
        sa.Column("ev", sa.Numeric(14, 2), nullable=False),
        sa.Column("ac", sa.Numeric(14, 2), nullable=False),
        sa.Column("sv", sa.Numeric(14, 2), nullable=False),
        sa.Column("cv", sa.Numeric(14, 2), nullable=False),
        sa.Column("spi", sa.Numeric(6, 4), nullable=False),
        sa.Column("cpi", sa.Numeric(6, 4), nullable=False),
        sa.Column("eac", sa.Numeric(14, 2), nullable=False),
        sa.Column("etc", sa.Numeric(14, 2), nullable=False),
        sa.Column("vac", sa.Numeric(14, 2), nullable=False),
        sa.Column("tcpi", sa.Numeric(6, 4), nullable=False),
        sa.Column("percent_complete", sa.Numeric(5, 2), nullable=False),
        sa.Column("data_date", sa.Date, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_evm_snapshots_project", "evm_snapshots", ["project_id"])

    # ── EAC Forecasts ──────────────────────────────────────────────────────
    op.create_table(
        "eac_forecasts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("evm_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("eac_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence_low", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_high", sa.Numeric(14, 2), nullable=True),
        sa.Column("model_params", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_eac_forecasts_snapshot", "eac_forecasts", ["snapshot_id"])

    # ── Change Orders ──────────────────────────────────────────────────────
    op.create_table(
        "change_orders",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("co_number", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("change_type", sa.Text, nullable=False),
        sa.Column("requested_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cost_impact", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("schedule_impact_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("risk_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("ai_analysis", JSONB, nullable=False, server_default="{}"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_change_orders_project", "change_orders", ["project_id"])

    # ── Schedule Risk Simulations ──────────────────────────────────────────
    op.create_table(
        "schedule_risk_simulations",
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
            sa.ForeignKey("schedule_baselines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("num_iterations", sa.Integer, nullable=False, server_default="10000"),
        sa.Column("p10_duration", sa.Integer, nullable=False),
        sa.Column("p50_duration", sa.Integer, nullable=False),
        sa.Column("p80_duration", sa.Integer, nullable=False),
        sa.Column("p90_duration", sa.Integer, nullable=False),
        sa.Column("mean_duration", sa.Numeric(10, 2), nullable=False),
        sa.Column("std_dev", sa.Numeric(10, 2), nullable=False),
        sa.Column("critical_risk_drivers", JSONB, nullable=False, server_default="[]"),
        sa.Column("histogram_data", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_schedule_risk_sims_project", "schedule_risk_simulations", ["project_id"])

    # ── Daily Reports ──────────────────────────────────────────────────────
    op.create_table(
        "daily_reports",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("content_markdown", sa.Text, nullable=True),
        sa.Column("content_html", sa.Text, nullable=True),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("sections", JSONB, nullable=False, server_default="{}"),
        sa.Column("generated_by", sa.Text, nullable=False, server_default="system"),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_daily_reports_project", "daily_reports", ["project_id"])

    # ── Meeting Minutes ────────────────────────────────────────────────────
    op.create_table(
        "meeting_minutes",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("meeting_type", sa.Text, nullable=False),
        sa.Column("meeting_date", sa.Date, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("attendees", JSONB, nullable=False, server_default="[]"),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("action_items", JSONB, nullable=False, server_default="[]"),
        sa.Column("decisions", JSONB, nullable=False, server_default="[]"),
        sa.Column("audio_url", sa.Text, nullable=True),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_meeting_minutes_project", "meeting_minutes", ["project_id"])

    # ── RFIs ───────────────────────────────────────────────────────────────
    op.create_table(
        "rfis",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rfi_number", sa.Text, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("submitted_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assigned_to", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("response", sa.Text, nullable=True),
        sa.Column("ai_suggested_response", sa.Text, nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_rfis_project", "rfis", ["project_id"])

    # ── Submittals ─────────────────────────────────────────────────────────
    op.create_table(
        "submittals",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("submittal_number", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("spec_section", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("submitted_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("document_urls", JSONB, nullable=False, server_default="[]"),
        sa.Column("review_comments", JSONB, nullable=False, server_default="[]"),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_submittals_project", "submittals", ["project_id"])

    # ── Inspections ────────────────────────────────────────────────────────
    op.create_table(
        "inspections",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inspection_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="scheduled"),
        sa.Column("inspector_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("location", sa.Text, nullable=True),
        sa.Column("checklist_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("findings", JSONB, nullable=False, server_default="[]"),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_inspections_project", "inspections", ["project_id"])

    # ── Defect Reports ─────────────────────────────────────────────────────
    op.create_table(
        "defect_reports",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "inspection_id",
            UUID,
            sa.ForeignKey("inspections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("defect_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False, server_default="minor"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=True),
        sa.Column("image_urls", JSONB, nullable=False, server_default="[]"),
        sa.Column("ai_classification", JSONB, nullable=False, server_default="{}"),
        sa.Column("assigned_to", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_defect_reports_project", "defect_reports", ["project_id"])

    # ── NCRs ───────────────────────────────────────────────────────────────
    op.create_table(
        "ncrs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ncr_number", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("severity", sa.Text, nullable=False, server_default="minor"),
        sa.Column("root_cause", sa.Text, nullable=True),
        sa.Column("corrective_action", sa.Text, nullable=True),
        sa.Column("cost_impact", sa.Numeric(14, 2), nullable=True),
        sa.Column("reported_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_ncrs_project", "ncrs", ["project_id"])

    # ── Compliance Checks ──────────────────────────────────────────────────
    op.create_table(
        "compliance_checks",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("regulation_code", sa.Text, nullable=False),
        sa.Column("regulation_title", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("check_result", sa.Text, nullable=True),
        sa.Column("findings", JSONB, nullable=False, server_default="[]"),
        sa.Column("checked_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_compliance_checks_project", "compliance_checks", ["project_id"])

    # ── Daily Logs ─────────────────────────────────────────────────────────
    op.create_table(
        "daily_logs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("log_date", sa.Date, nullable=False),
        sa.Column("weather", JSONB, nullable=False, server_default="{}"),
        sa.Column("crew_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("work_hours", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("activities_completed", JSONB, nullable=False, server_default="[]"),
        sa.Column("delays", JSONB, nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_daily_logs_project", "daily_logs", ["project_id"])

    # ── Crew Productivity ──────────────────────────────────────────────────
    op.create_table(
        "crew_productivity",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade", sa.Text, nullable=False),
        sa.Column("crew_size", sa.Integer, nullable=False),
        sa.Column("work_date", sa.Date, nullable=False),
        sa.Column("planned_units", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("actual_units", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("unit_of_measure", sa.Text, nullable=False),
        sa.Column("productivity_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("pf_ratio", sa.Numeric(6, 4), nullable=True),
        sa.Column("conditions", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_crew_productivity_project", "crew_productivity", ["project_id"])

    # ── Equipment Telemetry ────────────────────────────────────────────────
    op.create_table(
        "equipment_telemetry",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("equipment_id", sa.Text, nullable=False),
        sa.Column("equipment_type", sa.Text, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine_hours", sa.Numeric(10, 2), nullable=True),
        sa.Column("fuel_consumption", sa.Numeric(8, 2), nullable=True),
        sa.Column("idle_time_hours", sa.Numeric(8, 2), nullable=True),
        sa.Column("utilization_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("location_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_equipment_telemetry_project", "equipment_telemetry", ["project_id"])

    # ── Activity Recognitions ──────────────────────────────────────────────
    op.create_table(
        "activity_recognitions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("camera_id", sa.Text, nullable=False),
        sa.Column("activity_type", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("worker_count", sa.Integer, nullable=True),
        sa.Column("zone_id", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_activity_recognitions_project", "activity_recognitions", ["project_id"])

    # ── Team Workflow Runs ─────────────────────────────────────────────────
    op.create_table(
        "team_workflow_runs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_name", sa.Text, nullable=False),
        sa.Column("workflow_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("input_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("output_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("agent_results", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_team_workflow_runs_project", "team_workflow_runs", ["project_id"])

    # ── Cross Team Events ──────────────────────────────────────────────────
    op.create_table(
        "cross_team_events",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("source_team", sa.Text, nullable=False),
        sa.Column("source_agent", sa.Text, nullable=False),
        sa.Column("target_team", sa.Text, nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_cross_team_events_project", "cross_team_events", ["project_id"])


def downgrade() -> None:
    op.drop_table("cross_team_events")
    op.drop_table("team_workflow_runs")
    op.drop_table("activity_recognitions")
    op.drop_table("equipment_telemetry")
    op.drop_table("crew_productivity")
    op.drop_table("daily_logs")
    op.drop_table("compliance_checks")
    op.drop_table("ncrs")
    op.drop_table("defect_reports")
    op.drop_table("inspections")
    op.drop_table("submittals")
    op.drop_table("rfis")
    op.drop_table("meeting_minutes")
    op.drop_table("daily_reports")
    op.drop_table("schedule_risk_simulations")
    op.drop_table("change_orders")
    op.drop_table("eac_forecasts")
    op.drop_table("evm_snapshots")
