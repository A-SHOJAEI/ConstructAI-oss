"""Phase 2 feature tables: progress tracking, contract intelligence, daily reports,
sustainability, subcontractor portal, workforce analytics, cross-project insights.

Adds 12 new tables and 2 columns to schedule_activities.

Revision ID: 029
Revises: 028
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. progress_photos
    # ------------------------------------------------------------------
    op.create_table(
        "progress_photos",
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
        sa.Column("photo_url", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("detections", JSONB(), nullable=False, server_default="[]"),
        sa.Column("matched_activities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("overall_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_progress_photos_project_id", "progress_photos", ["project_id"])

    # ------------------------------------------------------------------
    # 2. progress_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "progress_snapshots",
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
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("activities_progress", JSONB(), nullable=False, server_default="{}"),
        sa.Column("overall_progress", sa.Numeric(5, 2), nullable=True),
        sa.Column("photo_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_progress_snapshots_project_date",
        "progress_snapshots",
        ["project_id", "snapshot_date"],
    )

    # ------------------------------------------------------------------
    # 3. contract_documents
    # ------------------------------------------------------------------
    op.create_table(
        "contract_documents",
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
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("contract_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default="Untitled Contract"),
        sa.Column("parties", JSONB(), nullable=False, server_default="[]"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(16, 2), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_contract_documents_project_id", "contract_documents", ["project_id"])

    # ------------------------------------------------------------------
    # 4. contract_clauses
    # ------------------------------------------------------------------
    op.create_table(
        "contract_clauses",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "contract_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contract_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("clause_type", sa.Text(), nullable=False),
        sa.Column("clause_text", sa.Text(), nullable=False),
        sa.Column("parsed_value", JSONB(), nullable=False, server_default="{}"),
        sa.Column("section_reference", sa.Text(), nullable=True),
        sa.Column(
            "confidence",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="0.50",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_contract_clauses_document_id", "contract_clauses", ["contract_document_id"])

    # ------------------------------------------------------------------
    # 5. contract_comparisons
    # ------------------------------------------------------------------
    op.create_table(
        "contract_comparisons",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "contract_a_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contract_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contract_b_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contract_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("differences", JSONB(), nullable=False, server_default="{}"),
        sa.Column("deviations", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 6. generated_daily_reports
    # ------------------------------------------------------------------
    op.create_table(
        "generated_daily_reports",
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
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("aggregated_data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("narrative_markdown", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "generated_by",
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
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "daily_log_id",
            UUID(as_uuid=True),
            sa.ForeignKey("daily_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_generated_daily_reports_project_date",
        "generated_daily_reports",
        ["project_id", "report_date"],
    )

    # ------------------------------------------------------------------
    # 7. carbon_factors
    # ------------------------------------------------------------------
    op.create_table(
        "carbon_factors",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("csi_code", sa.Text(), nullable=False),
        sa.Column("material_name", sa.Text(), nullable=False),
        sa.Column("embodied_carbon_kgco2e", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("data_source", sa.Text(), nullable=False, server_default="ICE"),
        sa.Column("gwp_category", sa.Text(), nullable=False, server_default="A1-A3"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_carbon_factors_csi_code", "carbon_factors", ["csi_code"])

    # ------------------------------------------------------------------
    # 8. project_sustainability
    # ------------------------------------------------------------------
    op.create_table(
        "project_sustainability",
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
            unique=True,
        ),
        sa.Column(
            "total_embodied_carbon_kgco2e",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("carbon_per_sf", sa.Numeric(8, 2), nullable=True),
        sa.Column("salvaged_materials", JSONB(), nullable=False, server_default="[]"),
        sa.Column("recycled_content_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("leed_credits", JSONB(), nullable=False, server_default="[]"),
        sa.Column("energy_data", JSONB(), nullable=True),
        sa.Column("baseline_comparison_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "last_calculated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # 9. subcontractor_profiles
    # ------------------------------------------------------------------
    op.create_table(
        "subcontractor_profiles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("trade", sa.Text(), nullable=False),
        sa.Column("sov_item_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("contact_info", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("user_id", "project_id", name="uq_sub_user_project"),
    )
    op.create_index(
        "ix_subcontractor_profiles_project_id", "subcontractor_profiles", ["project_id"]
    )

    # ------------------------------------------------------------------
    # 10. subcontractor_submissions
    # ------------------------------------------------------------------
    op.create_table(
        "subcontractor_submissions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subcontractor_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("submission_type", sa.Text(), nullable=False),
        sa.Column("submission_date", sa.Date(), nullable=False),
        sa.Column("data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_subcontractor_submissions_profile_type",
        "subcontractor_submissions",
        ["profile_id", "submission_type"],
    )

    # ------------------------------------------------------------------
    # 11. workforce_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "workforce_snapshots",
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
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("total_workers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("workers_by_trade", JSONB(), nullable=False, server_default="{}"),
        sa.Column("total_manhours", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("overtime_hours", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("overtime_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("fatigue_flags", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_workforce_snapshots_project_date",
        "workforce_snapshots",
        ["project_id", "snapshot_date"],
    )

    # ------------------------------------------------------------------
    # 12. cross_project_insights
    # ------------------------------------------------------------------
    op.create_table(
        "cross_project_insights",
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
        sa.Column("insight_type", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.Text(), nullable=False),
        sa.Column("parameters", JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_project_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="0.50"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_cross_project_insights_org_type",
        "cross_project_insights",
        ["org_id", "insight_type"],
    )
    op.create_index(
        "ix_cross_project_insights_org_hash",
        "cross_project_insights",
        ["org_id", "query_hash"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # Add columns to schedule_activities
    # ------------------------------------------------------------------
    op.add_column(
        "schedule_activities",
        sa.Column("ai_pct_complete", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "schedule_activities",
        sa.Column("last_photo_update", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Remove columns from schedule_activities
    op.drop_column("schedule_activities", "last_photo_update")
    op.drop_column("schedule_activities", "ai_pct_complete")

    # 12. cross_project_insights
    op.drop_index("ix_cross_project_insights_org_hash", table_name="cross_project_insights")
    op.drop_index("ix_cross_project_insights_org_type", table_name="cross_project_insights")
    op.drop_table("cross_project_insights")

    # 11. workforce_snapshots
    op.drop_index("ix_workforce_snapshots_project_date", table_name="workforce_snapshots")
    op.drop_table("workforce_snapshots")

    # 10. subcontractor_submissions
    op.drop_index(
        "ix_subcontractor_submissions_profile_type",
        table_name="subcontractor_submissions",
    )
    op.drop_table("subcontractor_submissions")

    # 9. subcontractor_profiles
    op.drop_index("ix_subcontractor_profiles_project_id", table_name="subcontractor_profiles")
    op.drop_table("subcontractor_profiles")

    # 8. project_sustainability
    op.drop_table("project_sustainability")

    # 7. carbon_factors
    op.drop_index("ix_carbon_factors_csi_code", table_name="carbon_factors")
    op.drop_table("carbon_factors")

    # 6. generated_daily_reports
    op.drop_index(
        "ix_generated_daily_reports_project_date",
        table_name="generated_daily_reports",
    )
    op.drop_table("generated_daily_reports")

    # 5. contract_comparisons
    op.drop_table("contract_comparisons")

    # 4. contract_clauses
    op.drop_index("ix_contract_clauses_document_id", table_name="contract_clauses")
    op.drop_table("contract_clauses")

    # 3. contract_documents
    op.drop_index("ix_contract_documents_project_id", table_name="contract_documents")
    op.drop_table("contract_documents")

    # 2. progress_snapshots
    op.drop_index("ix_progress_snapshots_project_date", table_name="progress_snapshots")
    op.drop_table("progress_snapshots")

    # 1. progress_photos
    op.drop_index("ix_progress_photos_project_id", table_name="progress_photos")
    op.drop_table("progress_photos")
