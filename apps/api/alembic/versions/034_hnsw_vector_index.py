"""Add HNSW vector index alongside existing IVFFlat for better recall at scale.

HNSW doesn't require training and provides better recall than IVFFlat at large scale
(500K+ embeddings). Both indexes coexist -- the query planner picks the optimal one.

Revision ID: 034
Revises: 033
Create Date: 2026-03-27
"""

from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HNSW index creation — regular (non-CONCURRENTLY) CREATE INDEX.
    # This works inside Alembic transactions but locks the table during build.
    # For large deployments (500K+ embeddings), consider running manually with
    # CREATE INDEX CONCURRENTLY outside Alembic instead.
    try:
        op.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_embeddings_hnsw
        ON document_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 200)
        """)
    except Exception:
        pass  # Index may already exist or pgvector not available


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_document_embeddings_hnsw")
