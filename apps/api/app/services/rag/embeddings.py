"""Embedding service supporting fine-tuned construction model and Voyage AI.

Primary: Fine-tuned construction embedding model (BAAI/bge-large-en-v1.5 based)
Fallback: Voyage AI (voyage-3-large, 1024-dimensional vectors)

The active embedding provider is selected at module load time based on
whether a fine-tuned model checkpoint is available on disk.
"""

import logging
import threading
from pathlib import Path

import voyageai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fine-tuned construction model (loaded lazily)
# ---------------------------------------------------------------------------

_construction_embedder = None
_construction_model_checked = False
_embedder_init_lock = threading.Lock()

# Default path; can be overridden via CONSTRUCTION_EMBEDDING_MODEL_PATH env var
_CONSTRUCTION_MODEL_PATH = "models/construction-bge-large"


def _get_construction_embedder():
    """Return the fine-tuned ConstructionEmbedder if available, else None."""
    global _construction_embedder, _construction_model_checked

    if _construction_model_checked:
        return _construction_embedder

    with _embedder_init_lock:
        # Double-checked locking: re-check after acquiring lock
        if _construction_model_checked:
            return _construction_embedder

        _construction_model_checked = True
        import os

        model_path = os.environ.get("CONSTRUCTION_EMBEDDING_MODEL_PATH", _CONSTRUCTION_MODEL_PATH)

        if not Path(model_path).exists():
            logger.info(
                "Fine-tuned construction model not found at %s; using Voyage AI",
                model_path,
            )
            return None

        try:
            from app.ml.training.construction_embeddings import ConstructionEmbedder

            _construction_embedder = ConstructionEmbedder(
                model_path=model_path,
                fallback_to_voyage=True,
            )
            logger.info("Loaded fine-tuned construction embedding model: %s", model_path)
        except Exception as exc:
            logger.warning("Failed to load construction embedder: %s", exc)

    return _construction_embedder


# ---------------------------------------------------------------------------
# Voyage AI client (fallback — lazy singleton)
# ---------------------------------------------------------------------------

_voyage_client: voyageai.AsyncClient | None = None


def _get_voyage_client() -> voyageai.AsyncClient:
    """Return a cached async Voyage AI client, creating it on first call."""
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.AsyncClient(timeout=30.0)
    return _voyage_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_active_model_name() -> str:
    """Return the name of the currently active embedding model."""
    embedder = _get_construction_embedder()
    if embedder is not None:
        return embedder.model_name
    return "voyage-3-large"


async def generate_embeddings(
    texts: list[str],
    model: str = "voyage-3-large",
) -> list[list[float]]:
    """Generate embeddings for a batch of texts.

    Uses the fine-tuned construction model if available, otherwise Voyage AI.

    Args:
        texts: List of text strings to embed.
        model: Voyage AI model name (used only when fine-tuned model unavailable).

    Returns:
        List of embedding vectors.
    """
    if not texts:
        return []

    # Try fine-tuned model first
    embedder = _get_construction_embedder()
    if embedder is not None:
        try:
            return await embedder.aembed_documents(texts)
        except Exception as exc:
            logger.warning("Construction embedder failed, falling back to Voyage: %s", exc)

    # Fallback to Voyage AI
    client = _get_voyage_client()

    try:
        response = await client.embed(
            texts=texts,
            model=model,
            input_type="document",
        )
        return [[float(v) for v in emb] for emb in response.embeddings]
    except Exception as exc:
        logger.error("Voyage AI embedding request failed: %s", exc)
        raise


async def store_embeddings(
    db: AsyncSession,
    chunks: list[DocumentChunk],
    embeddings: list[list[float]],
    model_name: str | None = None,
) -> None:
    """Persist embedding vectors alongside their document chunks.

    Uses raw SQL to handle the pgvector ``vector`` column type which is not
    natively supported by the SQLAlchemy ORM column mapper.

    Args:
        db: Active async database session.
        chunks: ``DocumentChunk`` records that were embedded.
        embeddings: Corresponding embedding vectors (same order as *chunks*).
        model_name: Name of the model used to generate the embeddings.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks and embeddings length mismatch: {len(chunks)} vs {len(embeddings)}"
        )

    if model_name is None:
        model_name = get_active_model_name()

    # C-10: persist dim + model so retrieval can reject cross-model joins
    # and operators can see mixed-generation rows at a glance.
    dims = {len(e) for e in embeddings}
    if len(dims) > 1:
        raise ValueError(f"store_embeddings received vectors with mixed dimensions: {sorted(dims)}")
    embedding_dim = next(iter(dims)) if dims else 0

    stmt = text(
        "INSERT INTO document_embeddings "
        "(chunk_id, model_name, embedding_model, embedding_dim, embedding) "
        "VALUES (:chunk_id, :model_name, :embedding_model, :embedding_dim, :embedding)"
    )

    for chunk, embedding in zip(chunks, embeddings, strict=True):
        # Convert Python list to pgvector literal string: "[0.1, 0.2, ...]"
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
        "Stored %d embeddings (model=%s, dim=%d)", len(embeddings), model_name, embedding_dim
    )


async def embed_query(
    query: str,
    model: str = "voyage-3-large",
) -> list[float]:
    """Embed a single query string for retrieval.

    Uses the fine-tuned construction model if available, otherwise Voyage AI
    with ``input_type="query"`` for asymmetric transformation.

    Args:
        query: The search query text.
        model: Voyage AI model name (fallback).

    Returns:
        A single embedding vector.
    """
    # Try fine-tuned model first
    embedder = _get_construction_embedder()
    if embedder is not None:
        try:
            vectors = await embedder.aembed_queries([query])
            return [float(v) for v in vectors[0]]
        except Exception as exc:
            logger.warning("Construction embedder query failed, falling back: %s", exc)

    client = _get_voyage_client()

    try:
        response = await client.embed(
            texts=[query],
            model=model,
            input_type="query",
        )
        return [float(v) for v in response.embeddings[0]]
    except Exception as exc:
        logger.error("Voyage AI query embedding failed: %s", exc)
        raise
