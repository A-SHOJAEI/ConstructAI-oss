"""Cash flow models and translation cache.

Adds:
- lien_waivers table for payment compliance tracking
- cash_flow_snapshots table for forecast history
- translation_cache table for multi-language support

Revision ID: 028
Revises: 027
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Lien waivers table
    op.create_table(
        "lien_waivers",
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
            "pay_application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pay_applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("waiver_type", sa.Text(), nullable=False),
        sa.Column("vendor_name", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("through_date", sa.Date(), nullable=True),
        sa.Column("signed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
    op.create_index("ix_lien_waivers_project_id", "lien_waivers", ["project_id"])
    op.create_index("ix_lien_waivers_status", "lien_waivers", ["status"])
    op.create_index("ix_lien_waivers_vendor_name", "lien_waivers", ["vendor_name"])

    # 2. Cash flow snapshots table
    op.create_table(
        "cash_flow_snapshots",
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
        sa.Column("forecast_data", JSONB(), nullable=False),
        sa.Column("config", JSONB(), nullable=True),
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
    op.create_index("ix_cash_flow_snapshots_project_id", "cash_flow_snapshots", ["project_id"])
    op.create_index(
        "ix_cash_flow_snapshots_snapshot_date",
        "cash_flow_snapshots",
        ["snapshot_date"],
    )

    # 3. Translation cache table
    op.create_table(
        "translation_cache",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_lang", sa.Text(), nullable=False),
        sa.Column("target_lang", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_translation_cache_lookup",
        "translation_cache",
        ["source_lang", "target_lang", "source_hash"],
        unique=True,
    )
    op.create_index("ix_translation_cache_expires_at", "translation_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_translation_cache_expires_at", table_name="translation_cache")
    op.drop_index("ix_translation_cache_lookup", table_name="translation_cache")
    op.drop_table("translation_cache")

    op.drop_index("ix_cash_flow_snapshots_snapshot_date", table_name="cash_flow_snapshots")
    op.drop_index("ix_cash_flow_snapshots_project_id", table_name="cash_flow_snapshots")
    op.drop_table("cash_flow_snapshots")

    op.drop_index("ix_lien_waivers_vendor_name", table_name="lien_waivers")
    op.drop_index("ix_lien_waivers_status", table_name="lien_waivers")
    op.drop_index("ix_lien_waivers_project_id", table_name="lien_waivers")
    op.drop_table("lien_waivers")
