"""RFI Resolution Agent effectiveness tracking.

Revision ID: 024
Revises: 023
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rfi_resolution_logs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "rfi_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rfis.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_reached", sa.Integer(), nullable=False, server_default="0"),
        # Stage 1 outputs
        sa.Column("was_unnecessary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("unnecessary_source", sa.Text(), nullable=True),  # "rfi" | "spec" | "meeting"
        sa.Column("unnecessary_reason", sa.Text(), nullable=True),
        sa.Column("similar_rfi_count", sa.Integer(), nullable=False, server_default="0"),
        # Stage 2 outputs
        sa.Column("draft_confidence", sa.Float(), nullable=True),
        sa.Column("draft_model", sa.Text(), nullable=True),
        sa.Column("draft_source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_safety_related", sa.Boolean(), nullable=False, server_default="false"),
        # Stage 3 outputs
        sa.Column("hallucination_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contradiction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completeness_issues", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verification_passed", sa.Boolean(), nullable=True),
        # Human feedback
        sa.Column("human_accepted_draft", sa.Boolean(), nullable=True),
        sa.Column("human_edit_distance", sa.Integer(), nullable=True),
        sa.Column("human_feedback", sa.Text(), nullable=True),
        # Timing
        sa.Column("time_to_resolution_hours", sa.Float(), nullable=True),
        sa.Column("traditional_avg_hours", sa.Float(), nullable=True),
        # Full state snapshot for debugging
        sa.Column("agent_state", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_rfi_resolution_logs_rfi_id", "rfi_resolution_logs", ["rfi_id"])
    op.create_index("ix_rfi_resolution_logs_project_id", "rfi_resolution_logs", ["project_id"])
    op.create_index(
        "ix_rfi_resolution_logs_was_unnecessary", "rfi_resolution_logs", ["was_unnecessary"]
    )


def downgrade() -> None:
    op.drop_index("ix_rfi_resolution_logs_was_unnecessary")
    op.drop_index("ix_rfi_resolution_logs_project_id")
    op.drop_index("ix_rfi_resolution_logs_rfi_id")
    op.drop_table("rfi_resolution_logs")
