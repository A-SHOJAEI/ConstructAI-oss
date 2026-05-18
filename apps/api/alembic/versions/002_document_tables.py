"""Document tables for Phase 1 Knowledge Engine

Revision ID: 002
Revises: 001
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "002"
down_revision: str = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Documents table
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("discipline", sa.Text, nullable=True),
        sa.Column("revision", sa.Text, server_default="A"),
        sa.Column("cde_status", sa.Text, nullable=False, server_default="wip"),
        sa.Column("s3_key", sa.Text, nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("content_hash", sa.Text, nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("processing_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "type IN ('specification', 'drawing', 'contract', 'rfi', 'submittal', "
            "'daily_log', 'meeting_minutes', 'photo', 'change_order', 'schedule', 'bim_model', 'other')",
            name="ck_document_type",
        ),
        sa.CheckConstraint(
            "discipline IN ('structural', 'architectural', 'mep', 'civil', 'general')",
            name="ck_document_discipline",
        ),
        sa.CheckConstraint(
            "cde_status IN ('wip', 'shared', 'published', 'archived')",
            name="ck_document_cde_status",
        ),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'processing', 'chunking', 'embedding', 'complete', 'failed')",
            name="ck_document_processing_status",
        ),
    )
    op.create_index("idx_documents_project", "documents", ["project_id"])
    op.create_index("idx_documents_type", "documents", ["type"])
    op.create_index("idx_documents_status", "documents", ["processing_status"])

    # Document chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_type", sa.Text, nullable=False, server_default="text"),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("section_hierarchy", JSONB, server_default="[]"),
        sa.Column("csi_section", sa.Text, nullable=True),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "chunk_type IN ('text', 'table', 'heading', 'list')",
            name="ck_chunk_type",
        ),
    )
    op.create_index("idx_chunks_document", "document_chunks", ["document_id"])
    op.execute(
        "CREATE INDEX idx_chunks_content_trgm ON document_chunks USING GIN (content gin_trgm_ops)"
    )

    # Document embeddings (pgvector)
    op.create_table(
        "document_embeddings",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "chunk_id",
            UUID,
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text, nullable=False, server_default="voyage-3-large"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.execute("ALTER TABLE document_embeddings ADD COLUMN embedding vector(1024)")
    op.create_index("idx_embeddings_chunk", "document_embeddings", ["chunk_id"])
    op.execute(
        "CREATE INDEX idx_embeddings_vector ON document_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # Document entities
    op.create_table(
        "document_entities",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_value", sa.Text, nullable=False),
        sa.Column("section_reference", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "entity_type IN ('product', 'manufacturer', 'standard', 'requirement', "
            "'submittal_required', 'test_required', 'risk_clause')",
            name="ck_entity_type",
        ),
    )
    op.create_index("idx_entities_document", "document_entities", ["document_id"])
    op.create_index("idx_entities_type", "document_entities", ["entity_type"])

    # Document classifications
    op.create_table(
        "document_classifications",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("classified_type", sa.Text, nullable=False),
        sa.Column("csi_division", sa.Text, nullable=True),
        sa.Column("discipline", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("model_used", sa.Text, nullable=False),
        sa.Column("raw_output", JSONB, nullable=True),
        sa.Column("is_human_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_classifications_document", "document_classifications", ["document_id"])


def downgrade() -> None:
    op.drop_table("document_classifications")
    op.drop_table("document_entities")
    op.drop_table("document_embeddings")
    op.drop_table("document_chunks")
    op.drop_table("documents")
