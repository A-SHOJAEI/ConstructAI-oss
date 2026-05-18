"""Cross-encoder reranker for improving retrieval precision.

Default: BAAI/bge-reranker-base via sentence-transformers (local, no cloud).
Fallback: Cohere rerank-v3.5 if the local model fails to load AND
``COHERE_API_KEY`` is set.

The local model is small (~280 MB) and runs comfortably on CPU at demo
volumes; it lazy-loads on first use and is cached as a module-level
singleton.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local cross-encoder
# ---------------------------------------------------------------------------

_LOCAL_MODEL_NAME = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
_local_reranker: Any | None = None
_local_unavailable = False


def _get_local_reranker():
    """Lazy-load the local cross-encoder. Returns None if unavailable."""
    global _local_reranker, _local_unavailable
    if _local_reranker is not None:
        return _local_reranker
    if _local_unavailable:
        return None
    try:
        from sentence_transformers import CrossEncoder

        _local_reranker = CrossEncoder(_LOCAL_MODEL_NAME, max_length=512)
        logger.info("Loaded local cross-encoder reranker: %s", _LOCAL_MODEL_NAME)
        return _local_reranker
    except Exception as exc:
        _local_unavailable = True
        logger.warning(
            "Local cross-encoder unavailable (%s); will try Cohere fallback if configured",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Cohere fallback (only used if cloud fallback is enabled)
# ---------------------------------------------------------------------------

_cohere_client: Any | None = None


def _get_cohere_client():
    """Return a cached async Cohere client only if a key is configured."""
    global _cohere_client
    if _cohere_client is not None:
        return _cohere_client
    if not os.environ.get("COHERE_API_KEY"):
        return None
    try:
        import cohere

        _cohere_client = cohere.AsyncClientV2()
        return _cohere_client
    except Exception as exc:
        logger.warning("Cohere client init failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def rerank(
    query: str,
    results: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """Rerank retrieval results using a cross-encoder.

    Tries the local BGE cross-encoder first; falls back to Cohere only if
    the local model is unavailable AND ``COHERE_API_KEY`` is set; otherwise
    returns the original results truncated to *top_n*.

    Args:
        query: The user's search query.
        results: Candidate results from the retrieval stage; each must have a
            ``content`` key.
        top_n: Number of top results to return after reranking.

    Returns:
        A list of result dicts reordered by relevance score, each augmented
        with a ``rerank_score`` field.
    """
    if not results:
        return []
    if len(results) <= 1:
        return results[:top_n]

    documents = [r["content"] for r in results]

    # ---------- local cross-encoder ----------
    reranker = _get_local_reranker()
    if reranker is not None:
        try:
            import asyncio

            pairs = [[query, d] for d in documents]
            scores = await asyncio.to_thread(reranker.predict, pairs)
            ranked = sorted(
                zip(results, scores, strict=True),
                key=lambda x: float(x[1]),
                reverse=True,
            )[:top_n]
            out = [
                {**r, "rerank_score": float(s), "rerank_model": _LOCAL_MODEL_NAME}
                for r, s in ranked
            ]
            logger.info(
                "Local reranker scored %d -> %d (top score=%.4f)",
                len(results),
                len(out),
                out[0]["rerank_score"] if out else 0.0,
            )
            return out
        except Exception as exc:
            logger.warning("Local reranker failed at runtime: %s", exc)

    # ---------- Cohere fallback ----------
    client = _get_cohere_client()
    if client is not None:
        try:
            response = await client.rerank(
                model="rerank-v3.5",
                query=query,
                documents=documents,
                top_n=min(top_n, len(results)),
            )
            reranked: list[dict] = []
            for item in response.results:
                original = results[item.index]
                reranked.append(
                    {
                        **original,
                        "rerank_score": item.relevance_score,
                        "rerank_model": "cohere-rerank-v3.5",
                    }
                )
            logger.info("Cohere reranker scored %d -> %d", len(results), len(reranked))
            return reranked
        except Exception as exc:
            logger.warning("Cohere rerank failed: %s", exc)

    # ---------- pass-through ----------
    return results[:top_n]
