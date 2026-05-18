"""Embedding step for the ingestion pipeline.

Delegates to ``app.services.rag.embeddings.generate_embeddings`` so that
both ingestion and query-time use the **same** embedding model (fine-tuned
BGE when available, Voyage AI otherwise).  This prevents the vector-space
mismatch that occurs when documents are embedded with one model but queries
use another.

The resulting vectors are stored in the ``document_embeddings`` table via
raw SQL to work with the pgvector ``vector(1024)`` column type.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk
from app.services.rag.embeddings import generate_embeddings, get_active_model_name

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BATCH_SIZE: int = 128


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


# M-17: Voyage AI silently truncates inputs above ~128K chars, and a very
# long single chunk can blow up the embedding request. Cap at 32KB per
# chunk (≈8k BPE tokens worst case) — specs/OSHA sections comfortably fit
# below this, so the cap only hits pathological inputs.
_MAX_CHARS_PER_CHUNK = 32_000


def _sanitize_for_embedding(text: str) -> str:
    """Strip and bound a chunk before embedding.

    Prevents silent truncation from the provider, strips stray NUL bytes
    (which break Voyage's JSON encoding), and collapses unusually long
    whitespace runs.
    """
    if not text:
        return ""
    cleaned = text.replace("\x00", " ").strip()
    if len(cleaned) > _MAX_CHARS_PER_CHUNK:
        logger.warning(
            "chunk_truncated_for_embedding",
            original=len(cleaned),
            truncated=_MAX_CHARS_PER_CHUNK,
        )
        cleaned = cleaned[:_MAX_CHARS_PER_CHUNK]
    return cleaned


async def embed_chunks(
    texts: list[str],
    *,
    batch_size: int = BATCH_SIZE,
) -> list[list[float]]:
    """Generate embeddings for a list of text chunks, batched for throughput.

    Delegates to the shared RAG embedding service so the same model is used
    for both ingestion and retrieval.

    Args:
        texts: The raw text content of each chunk.
        batch_size: Maximum number of texts to send in a single API call.

    Returns:
        A list of embedding vectors aligned with the input *texts* list.

    Raises:
        RuntimeError: When embedding fails after exhausting retries.
    """
    if not texts:
        return []

    # M-17: sanitize (strip + cap length) before any provider call so a
    # pathological chunk doesn't silently get truncated or rejected.
    sanitized = [_sanitize_for_embedding(t) for t in texts]

    all_embeddings: list[list[float]] = []

    for start in range(0, len(sanitized), batch_size):
        batch = sanitized[start : start + batch_size]
        logger.debug(
            "embedding_batch",
            batch_start=start,
            batch_size=len(batch),
            total=len(sanitized),
        )
        batch_embeddings = await generate_embeddings(batch)
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


# ---------------------------------------------------------------------------
# Storage helper
# ---------------------------------------------------------------------------


async def store_chunk_embeddings(
    db: AsyncSession,
    chunks: Sequence[DocumentChunk],
    embeddings: list[list[float]],
    *,
    model_name: str | None = None,
) -> int:
    """Persist embedding vectors for the given chunks.

    Uses raw SQL to write into the pgvector ``vector(1024)`` column.

    Args:
        db: Active async database session.
        chunks: The ``DocumentChunk`` ORM objects (must already be flushed so
            they have valid ``id`` values).
        embeddings: Corresponding embedding vectors (same length / order).
        model_name: Model identifier stored alongside each embedding.

    Returns:
        The number of embedding rows inserted.

    Raises:
        ValueError: If *chunks* and *embeddings* have different lengths.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}")

    if model_name is None:
        model_name = get_active_model_name()

    # C-10: persist embedding_dim + embedding_model. See migration 037.
    dims = {len(e) for e in embeddings}
    if len(dims) > 1:
        raise ValueError(
            f"store_chunk_embeddings received vectors with mixed dimensions: {sorted(dims)}"
        )
    embedding_dim = next(iter(dims)) if dims else 0

    stmt = text(
        "INSERT INTO document_embeddings "
        "(chunk_id, model_name, embedding_model, embedding_dim, embedding) "
        "VALUES (:chunk_id, :model_name, :embedding_model, :embedding_dim, :embedding)"
    )

    for chunk, embedding in zip(chunks, embeddings, strict=True):
        vec_str = "[" + ", ".join(str(v) for v in embedding) + "]"
        await db.execute(
            stmt,
            {
                "chunk_id": str(chunk.id),
                "model_name": model_name,
                "embedding_model": model_name,
                "embedding_dim": embedding_dim,
                "embedding": vec_str,
            },
        )

    await db.flush()
    logger.info(
        "stored_embeddings",
        count=len(embeddings),
        model=model_name,
        dim=embedding_dim,
    )
    return len(embeddings)
