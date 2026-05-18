"""RFI workflow expansion.

Adds response chains, attachments, ball-in-court tracking,
cost/schedule impact fields, and distribution lists to the
existing ``rfis`` table.  Creates ``rfi_responses`` and
``rfi_attachments`` tables.

Revision ID: 016
Revises: 015
Create Date: 2026-03-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "016"
down_revision: str = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # ALTER rfis – add workflow columns
    # ---------------------------------------------------------------
    op.add_column("rfis", sa.Column("answer", sa.Text, nullable=True))
    op.add_column(
        "rfis",
        sa.Column(
            "ball_in_court",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("rfis", sa.Column("cost_impact", sa.Boolean, nullable=True))
    op.add_column("rfis", sa.Column("schedule_impact", sa.Boolean, nullable=True))
    op.add_column("rfis", sa.Column("cost_impact_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("rfis", sa.Column("schedule_impact_days", sa.Integer, nullable=True))
    op.add_column("rfis", sa.Column("spec_section", sa.Text, nullable=True))
    op.add_column("rfis", sa.Column("drawing_reference", sa.Text, nullable=True))
    op.add_column(
        "rfis",
        sa.Column("distribution_list", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column("rfis", sa.Column("date_sent", sa.Date, nullable=True))
    op.add_column(
        "rfis",
        sa.Column("date_answered", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rfis",
        sa.Column("date_closed", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("idx_rfis_project_status", "rfis", ["project_id", "status"])
    op.create_index("idx_rfis_ball_in_court", "rfis", ["ball_in_court"])
    op.create_index("idx_rfis_due_date", "rfis", ["due_date"])

    # ---------------------------------------------------------------
    # rfi_responses – multi-step review chain
    # ---------------------------------------------------------------
    op.create_table(
        "rfi_responses",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "rfi_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rfis.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "responder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("response_text", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column(
            "responded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_rfi_responses_rfi_id", "rfi_responses", ["rfi_id"])

    # ---------------------------------------------------------------
    # rfi_attachments – files stored in MinIO / S3
    # ---------------------------------------------------------------
    op.create_table(
        "rfi_attachments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "rfi_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rfis.id", ondelete="CASCADE"),
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
    op.create_index("idx_rfi_attachments_rfi_id", "rfi_attachments", ["rfi_id"])


def downgrade() -> None:
    op.drop_index("idx_rfi_attachments_rfi_id")
    op.drop_table("rfi_attachments")

    op.drop_index("idx_rfi_responses_rfi_id")
    op.drop_table("rfi_responses")

    op.drop_index("idx_rfis_due_date")
    op.drop_index("idx_rfis_ball_in_court")
    op.drop_index("idx_rfis_project_status")

    op.drop_column("rfis", "date_closed")
    op.drop_column("rfis", "date_answered")
    op.drop_column("rfis", "date_sent")
    op.drop_column("rfis", "distribution_list")
    op.drop_column("rfis", "drawing_reference")
    op.drop_column("rfis", "spec_section")
    op.drop_column("rfis", "schedule_impact_days")
    op.drop_column("rfis", "cost_impact_amount")
    op.drop_column("rfis", "schedule_impact")
    op.drop_column("rfis", "cost_impact")
    op.drop_column("rfis", "ball_in_court")
    op.drop_column("rfis", "answer")
