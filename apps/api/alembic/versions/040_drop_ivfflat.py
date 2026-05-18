"""Drop legacy IVFFlat vector index; keep HNSW only (M-18).

Revision ID: 040
Revises: 039
Create Date: 2026-04-24

Migration 002 created an IVFFlat index (`idx_embeddings_vector`) with
`lists=100`, sized for <100K rows. Migration 034 added an HNSW index
(`idx_document_embeddings_hnsw`) which is strictly better for our workload
(millions of vectors, high recall requirements). Keeping both indexes
doubles write amplification on every insert for no retrieval benefit —
pgvector only uses one index per query.

This migration drops the IVFFlat index. Recreated in downgrade in case of
rollback.
"""

from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embeddings_vector")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX idx_embeddings_vector "
        "ON document_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )
