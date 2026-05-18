"""Intelligence briefs and notification preferences.

Revision ID: 022
Revises: 021
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intelligence_briefs",
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
            "generated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("overall_health_score", sa.Integer, nullable=False),
        sa.Column("project_status", sa.Text, nullable=False),
        sa.Column("schedule_health_score", sa.Integer, nullable=False, server_default="50"),
        sa.Column("cost_health_score", sa.Integer, nullable=False, server_default="50"),
        sa.Column("risk_score", sa.Integer, nullable=False, server_default="50"),
        sa.Column("productivity_score", sa.Integer, nullable=False, server_default="50"),
        sa.Column("executive_summary", sa.Text, nullable=False),
        sa.Column("schedule_intelligence", JSONB, nullable=False, server_default="{}"),
        sa.Column("cost_intelligence", JSONB, nullable=False, server_default="{}"),
        sa.Column("risk_intelligence", JSONB, nullable=False, server_default="{}"),
        sa.Column("productivity_intelligence", JSONB, nullable=False, server_default="{}"),
        sa.Column("action_items", JSONB, nullable=False, server_default="[]"),
        sa.Column("metrics_dashboard", JSONB, nullable=False, server_default="{}"),
        sa.Column("narrative_report", sa.Text, nullable=False, server_default=""),
        sa.Column("guardrails_result", JSONB, nullable=False, server_default="{}"),
        sa.Column("pdf_s3_key", sa.Text, nullable=True),
        sa.Column("json_s3_key", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "notification_preferences",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
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
        sa.Column("notification_type", sa.Text, nullable=False),
        sa.Column("email_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("webhook_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("webhook_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "project_id", "notification_type", name="uq_notif_pref"),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_table("intelligence_briefs")
