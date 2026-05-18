"""Submittal workflow expansion.

Adds review chains, attachments, ball-in-court tracking,
revision tracking, lead time, and submittal type/priority
to the existing ``submittals`` table.  Creates
``submittal_reviews`` and ``submittal_attachments`` tables.

Revision ID: 017
Revises: 016
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "017"
down_revision: str = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # ALTER submittals – add workflow columns
    # ---------------------------------------------------------------
    op.add_column("submittals", sa.Column("description", sa.Text, nullable=True))
    op.add_column("submittals", sa.Column("spec_section_name", sa.Text, nullable=True))
    op.add_column(
        "submittals",
        sa.Column("submittal_type", sa.Text, nullable=False, server_default="other"),
    )
    op.add_column(
        "submittals",
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
    )
    op.add_column(
        "submittals",
        sa.Column("revision_number", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "submittals",
        sa.Column(
            "current_reviewer",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "submittals",
        sa.Column(
            "ball_in_court",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("submittals", sa.Column("date_required", sa.Date, nullable=True))
    op.add_column(
        "submittals",
        sa.Column("date_submitted", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "submittals",
        sa.Column("date_returned", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("submittals", sa.Column("lead_time_days", sa.Integer, nullable=True))
    op.add_column(
        "submittals",
        sa.Column("distribution_list", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "submittals",
        sa.Column("linked_rfi_ids", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "submittals",
        sa.Column("review_chain", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "submittals",
        sa.Column("data_source", sa.Text, nullable=False, server_default="manual"),
    )
    op.add_column("submittals", sa.Column("procore_id", sa.BigInteger, nullable=True))

    # Change status default from "pending" to "not_submitted"
    op.alter_column(
        "submittals",
        "status",
        server_default="not_submitted",
    )

    # Indexes
    op.create_index("idx_submittals_project_status", "submittals", ["project_id", "status"])
    op.create_index("idx_submittals_ball_in_court", "submittals", ["ball_in_court"])
    op.create_index("idx_submittals_date_required", "submittals", ["date_required"])
    op.create_index("idx_submittals_spec_section", "submittals", ["spec_section"])

    # ---------------------------------------------------------------
    # submittal_reviews – multi-step approval chain
    # ---------------------------------------------------------------
    op.create_table(
        "submittal_reviews",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "submittal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("submittals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_action", sa.Text, nullable=False),
        sa.Column("comments", sa.Text, nullable=True),
        sa.Column("revision_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_submittal_reviews_submittal_id", "submittal_reviews", ["submittal_id"])

    # ---------------------------------------------------------------
    # submittal_attachments – files stored in MinIO / S3
    # ---------------------------------------------------------------
    op.create_table(
        "submittal_attachments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "submittal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("submittals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("file_name", sa.Text, nullable=False),
        sa.Column("file_type", sa.Text, nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_submittal_attachments_submittal_id",
        "submittal_attachments",
        ["submittal_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_submittal_attachments_submittal_id")
    op.drop_table("submittal_attachments")

    op.drop_index("idx_submittal_reviews_submittal_id")
    op.drop_table("submittal_reviews")

    op.drop_index("idx_submittals_spec_section")
    op.drop_index("idx_submittals_date_required")
    op.drop_index("idx_submittals_ball_in_court")
    op.drop_index("idx_submittals_project_status")

    op.alter_column("submittals", "status", server_default="pending")

    op.drop_column("submittals", "procore_id")
    op.drop_column("submittals", "data_source")
    op.drop_column("submittals", "review_chain")
    op.drop_column("submittals", "linked_rfi_ids")
    op.drop_column("submittals", "distribution_list")
    op.drop_column("submittals", "lead_time_days")
    op.drop_column("submittals", "date_returned")
    op.drop_column("submittals", "date_submitted")
    op.drop_column("submittals", "date_required")
    op.drop_column("submittals", "ball_in_court")
    op.drop_column("submittals", "current_reviewer")
    op.drop_column("submittals", "revision_number")
    op.drop_column("submittals", "priority")
    op.drop_column("submittals", "submittal_type")
    op.drop_column("submittals", "spec_section_name")
    op.drop_column("submittals", "description")
