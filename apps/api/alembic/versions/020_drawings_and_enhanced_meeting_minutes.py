"""Add drawing management tables and enhanced meeting minutes columns.

Revision ID: 020
Revises: 019
Create Date: 2025-07-01

New tables: drawing_sets, drawings, drawing_revisions, drawing_markups,
drawing_rfi_links, drawing_submittal_links, drawing_punch_list_links.

Alters: meeting_minutes — adds meeting_location, start_time, end_time,
agenda_items, notes, status columns.
"""

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def upgrade() -> None:
    # ── drawing_sets ───────────────────────────────────────────────────────
    op.create_table(
        "drawing_sets",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "name", name="uq_project_drawing_set_name"),
    )
    op.create_index("idx_drawing_sets_project_id", "drawing_sets", ["project_id"])

    # ── drawings (without current_revision_id FK — added after revisions) ─
    op.create_table(
        "drawings",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawing_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sheet_number", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=False),
        sa.Column("current_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("metadata", JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "sheet_number", name="uq_project_sheet_number"),
    )
    op.create_index("idx_drawings_project_id", "drawings", ["project_id"])
    op.create_index("idx_drawings_drawing_set_id", "drawings", ["drawing_set_id"])
    op.create_index("idx_drawings_sheet_number", "drawings", ["sheet_number"])

    # ── drawing_revisions ──────────────────────────────────────────────────
    op.create_table(
        "drawing_revisions",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="current", nullable=False),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("drawing_id", "revision_number", name="uq_drawing_revision_number"),
    )
    op.create_index("idx_drawing_revisions_drawing_id", "drawing_revisions", ["drawing_id"])
    op.create_index("idx_drawing_revisions_status", "drawing_revisions", ["status"])

    # Now add the deferred FK from drawings.current_revision_id → drawing_revisions.id
    op.create_foreign_key(
        "fk_drawings_current_revision",
        "drawings",
        "drawing_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── drawing_markups ────────────────────────────────────────────────────
    op.create_table(
        "drawing_markups",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawing_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("markup_data", JSONB(), nullable=False),
        sa.Column("markup_type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), server_default="review", nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_drawing_markups_revision_id", "drawing_markups", ["drawing_revision_id"])
    op.create_index("idx_drawing_markups_layer", "drawing_markups", ["layer"])

    # ── drawing_rfi_links ──────────────────────────────────────────────────
    op.create_table(
        "drawing_rfi_links",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rfi_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rfis.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("drawing_id", "rfi_id", name="uq_drawing_rfi"),
    )

    # ── drawing_submittal_links ────────────────────────────────────────────
    op.create_table(
        "drawing_submittal_links",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submittal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("submittals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("drawing_id", "submittal_id", name="uq_drawing_submittal"),
    )

    # ── drawing_punch_list_links ───────────────────────────────────────────
    op.create_table(
        "drawing_punch_list_links",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "drawing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drawings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "punch_list_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("punch_list_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("drawing_id", "punch_list_item_id", name="uq_drawing_punch_list"),
    )

    # ── meeting_minutes enhancements ───────────────────────────────────────
    op.add_column("meeting_minutes", sa.Column("meeting_location", sa.Text(), nullable=True))
    op.add_column("meeting_minutes", sa.Column("start_time", sa.Time(), nullable=True))
    op.add_column("meeting_minutes", sa.Column("end_time", sa.Time(), nullable=True))
    op.add_column(
        "meeting_minutes", sa.Column("agenda_items", JSONB(), server_default="[]", nullable=False)
    )
    op.add_column("meeting_minutes", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "meeting_minutes", sa.Column("status", sa.Text(), server_default="draft", nullable=False)
    )


def downgrade() -> None:
    # ── meeting_minutes — remove added columns ─────────────────────────────
    op.drop_column("meeting_minutes", "status")
    op.drop_column("meeting_minutes", "notes")
    op.drop_column("meeting_minutes", "agenda_items")
    op.drop_column("meeting_minutes", "end_time")
    op.drop_column("meeting_minutes", "start_time")
    op.drop_column("meeting_minutes", "meeting_location")

    # ── drop link tables ───────────────────────────────────────────────────
    op.drop_table("drawing_punch_list_links")
    op.drop_table("drawing_submittal_links")
    op.drop_table("drawing_rfi_links")

    # ── drop drawing tables (respect FK order) ─────────────────────────────
    op.drop_table("drawing_markups")

    # Remove deferred FK before dropping revisions
    op.drop_constraint("fk_drawings_current_revision", "drawings", type_="foreignkey")

    op.drop_table("drawing_revisions")
    op.drop_table("drawings")
    op.drop_table("drawing_sets")
