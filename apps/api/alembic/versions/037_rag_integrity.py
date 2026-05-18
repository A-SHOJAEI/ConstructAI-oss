"""RAG integrity: RFI upsert uniqueness + embedding metadata.

Revision ID: 037
Revises: 036
Create Date: 2026-04-24

Addresses C-10 / C-11 from PRE_LAUNCH_REVIEW_FINDINGS:
- Records ``embedding_dim`` and ``embedding_model`` on every row so model
  swaps can't silently corrupt vectors.
- Adds a GIN index on ``document_chunks.metadata`` plus a partial unique
  index that prevents duplicate RFI chunks for the same parent document
  when ``metadata->>'rfi_id'`` is set.
"""

from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- document_embeddings: persist dim + model ------------------------
    with op.batch_alter_table("document_embeddings") as batch:
        batch.add_column(sa.Column("embedding_dim", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.Text(), nullable=True))

    # Backfill: existing rows predate the rollout — assume the legacy 1024-dim
    # Voyage config until overwritten by the next indexing run.
    op.execute(
        "UPDATE document_embeddings "
        "SET embedding_dim = 1024, embedding_model = 'legacy-1024' "
        "WHERE embedding_dim IS NULL"
    )

    with op.batch_alter_table("document_embeddings") as batch:
        batch.alter_column("embedding_dim", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("embedding_model", existing_type=sa.Text(), nullable=False)

    # --- document_chunks.metadata: GIN + partial unique ------------------
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_chunks_metadata_gin "
        "ON document_chunks USING gin (metadata jsonb_path_ops)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_chunks_rfi_id "
        "ON document_chunks (document_id, (metadata->>'rfi_id')) "
        "WHERE metadata ? 'rfi_id'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_document_chunks_rfi_id")
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_metadata_gin")

    with op.batch_alter_table("document_embeddings") as batch:
        batch.drop_column("embedding_model")
        batch.drop_column("embedding_dim")
