"""Hybrid retrieval system combining vector search with BM25/trigram matching."""

import logging
import uuid
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    project_id: uuid.UUID,
    limit: int = 20,
) -> list[dict]:
    """Perform cosine-similarity search over document embeddings.

    Uses the pgvector ``<=>`` (cosine distance) operator so that a score of
    1.0 means identical and 0.0 means orthogonal.

    Args:
        db: Active async database session.
        query_embedding: The query vector (1024-dimensional).
        project_id: Restrict results to this project.
        limit: Maximum number of results to return.

    Returns:
        List of result dicts sorted by descending similarity score.
    """
    limit = min(limit, 100)  # Cap maximum results to prevent excessive queries

    # SECURITY [L-10]: Validate embedding vector dimensionality before passing
    # to pgvector. Mismatched dimensions cause DB errors or incorrect results.
    # Both BGE-large and Voyage produce 1024-dim vectors.
    _EXPECTED_EMBEDDING_DIM = 1024
    if len(query_embedding) != _EXPECTED_EMBEDDING_DIM:
        raise ValueError(
            f"Embedding vector has {len(query_embedding)} dimensions, "
            f"expected {_EXPECTED_EMBEDDING_DIM}"
        )

    vec_str = "[" + ", ".join(str(v) for v in query_embedding) + "]"

    stmt = text("""
        SELECT
            dc.id            AS chunk_id,
            dc.content       AS content,
            dc.document_id   AS document_id,
            d.title          AS document_title,
            dc.page_number   AS page_number,
            dc.section_hierarchy AS section_hierarchy,
            dc.csi_section   AS csi_section,
            1 - (de.embedding <=> CAST(:query_vec AS vector)) AS score
        FROM document_embeddings de
        JOIN document_chunks dc ON dc.id = de.chunk_id
        JOIN documents d         ON d.id = dc.document_id
        WHERE d.project_id = :project_id
        ORDER BY de.embedding <=> CAST(:query_vec AS vector) ASC
        LIMIT :limit
    """)

    result = await db.execute(
        stmt,
        {
            "query_vec": vec_str,
            "project_id": str(project_id),
            "limit": limit,
        },
    )

    rows = result.mappings().all()
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "content": row["content"],
            "document_id": str(row["document_id"]),
            "document_title": row["document_title"],
            "page_number": row["page_number"],
            "section_hierarchy": row["section_hierarchy"],
            "csi_section": row["csi_section"],
            "score": float(row["score"]),
        }
        for row in rows
    ]


async def bm25_search(
    db: AsyncSession,
    query: str,
    project_id: uuid.UUID,
    limit: int = 20,
) -> list[dict]:
    """Perform trigram-similarity search over document chunk content.

    Uses the PostgreSQL ``pg_trgm`` extension's ``similarity()`` function and
    the ``%`` operator for trigram-based matching, combined with a fallback
    ``ILIKE`` clause for short queries that may not trigger the trigram
    threshold.

    Args:
        db: Active async database session.
        query: The raw search query text.
        project_id: Restrict results to this project.
        limit: Maximum number of results to return.

    Returns:
        List of result dicts sorted by descending trigram similarity.
    """
    limit = min(limit, 100)  # Cap maximum results to prevent excessive queries

    query_escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query_pattern = f"%{query_escaped}%"

    stmt = text("""
        SELECT
            dc.id            AS chunk_id,
            dc.content       AS content,
            dc.document_id   AS document_id,
            d.title          AS document_title,
            dc.page_number   AS page_number,
            dc.section_hierarchy AS section_hierarchy,
            dc.csi_section   AS csi_section,
            similarity(dc.content, :query) AS score
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE d.project_id = :project_id
          AND (dc.content % :query OR dc.content ILIKE :query_pattern)
        ORDER BY similarity(dc.content, :query) DESC
        LIMIT :limit
    """)

    result = await db.execute(
        stmt,
        {
            "query": query,
            "project_id": str(project_id),
            "query_pattern": query_pattern,
            "limit": limit,
        },
    )

    rows = result.mappings().all()
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "content": row["content"],
            "document_id": str(row["document_id"]),
            "document_title": row["document_title"],
            "page_number": row["page_number"],
            "section_hierarchy": row["section_hierarchy"],
            "csi_section": row["csi_section"],
            "score": float(row["score"]),
        }
        for row in rows
    ]


def _reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    k: int = 60,
) -> list[dict]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion (RRF).

    For each result the RRF score is computed as:

        rrf_score = sum( 1 / (k + rank_i) )

    where *rank_i* is the 1-based rank of the result in the *i*-th list and
    *k* is a smoothing constant (default 60).

    Args:
        ranked_lists: Two or more ranked lists of result dicts.  Each dict
            **must** contain a ``chunk_id`` key.
        k: RRF smoothing constant.

    Returns:
        A single merged list sorted by descending RRF score.  Each dict is
        augmented with an ``rrf_score`` field.
    """
    scores: dict[str, float] = defaultdict(float)
    seen: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):
            cid = result["chunk_id"]
            scores[cid] += 1.0 / (k + rank)
            # Keep the first (highest-scoring) version of the result metadata
            if cid not in seen:
                seen[cid] = result

    merged: list[dict] = []
    for cid, rrf_score in scores.items():
        entry = {**seen[cid], "rrf_score": rrf_score}
        merged.append(entry)

    merged.sort(key=lambda r: r["rrf_score"], reverse=True)
    return merged


async def hybrid_search(
    db: AsyncSession,
    query: str,
    query_embedding: list[float],
    project_id: uuid.UUID,
    limit: int = 10,
) -> list[dict]:
    """Combine vector and BM25 search with Reciprocal Rank Fusion.

    Runs both retrieval strategies, merges results via RRF, de-duplicates
    by ``chunk_id``, and returns the top *limit* results.

    Args:
        db: Active async database session.
        query: The raw search query text.
        query_embedding: Pre-computed query embedding vector.
        project_id: Restrict results to this project.
        limit: Number of final results to return.

    Returns:
        Top-ranked results sorted by descending RRF score.
    """
    limit = min(limit, 100)  # Cap maximum results to prevent excessive queries
    vector_results = await vector_search(db, query_embedding, project_id, limit=20)
    bm25_results = await bm25_search(db, query, project_id, limit=20)

    logger.debug(
        "Hybrid search: %d vector hits, %d BM25 hits",
        len(vector_results),
        len(bm25_results),
    )

    merged = _reciprocal_rank_fusion([vector_results, bm25_results])
    return merged[:limit]


# ---------------------------------------------------------------------------
# RFI similarity search
# ---------------------------------------------------------------------------


async def search_similar_rfis(
    db: AsyncSession,
    question_text: str,
    project_id: uuid.UUID,
    *,
    similarity_threshold: float = 0.80,
    limit: int = 10,
    embed_fn=None,
) -> list[dict]:
    """Search for historically similar RFI questions using vector similarity.

    Embeds the incoming question and compares against the RFI embedding index
    (document_type = 'rfi' chunks). Results are always scoped to the given
    project to prevent cross-org data leakage.

    Parameters
    ----------
    db:
        Active async database session.
    question_text:
        The new RFI question to find similar past RFIs for.
    project_id:
        Mandatory project scope — prevents cross-org data leakage.
    similarity_threshold:
        Minimum cosine similarity to return (default 0.80).
    limit:
        Maximum results to return.
    embed_fn:
        Optional async callable that takes a query string and returns a
        1024-d vector. Falls back to Voyage AI embed_query.

    Returns
    -------
    list[dict]
        Each dict contains: rfi_chunk_id, question, answer, rfi_number,
        subject, similarity_score, project_id.
    """
    limit = min(limit, 100)  # Cap maximum results to prevent excessive queries

    # Generate query embedding
    if embed_fn:
        query_embedding = await embed_fn(question_text)
    else:
        from app.services.rag.embeddings import embed_query

        query_embedding = await embed_query(question_text)

    vec_str = "[" + ", ".join(str(v) for v in query_embedding) + "]"

    # Build query — searches RFI-specific chunks, always scoped to project
    params: dict = {
        "query_vec": vec_str,
        "threshold": similarity_threshold,
        "limit": limit,
        "project_id": str(project_id),
    }

    stmt = text("""
        SELECT
            dc.id              AS chunk_id,
            dc.content         AS content,
            dc.document_id     AS document_id,
            d.title            AS document_title,
            d.project_id       AS project_id,
            dc.metadata        AS chunk_metadata,
            1 - (de.embedding <=> CAST(:query_vec AS vector)) AS similarity_score
        FROM document_embeddings de
        JOIN document_chunks dc ON dc.id = de.chunk_id
        JOIN documents d         ON d.id = dc.document_id
        WHERE d.type = 'rfi'
          AND 1 - (de.embedding <=> CAST(:query_vec AS vector)) >= :threshold
          AND d.project_id = :project_id
        ORDER BY de.embedding <=> CAST(:query_vec AS vector) ASC
        LIMIT :limit
    """)

    result = await db.execute(stmt, params)
    rows = result.mappings().all()

    matches: list[dict] = []
    for row in rows:
        meta = row["chunk_metadata"] or {}
        matches.append(
            {
                "chunk_id": str(row["chunk_id"]),
                "question": meta.get("question", ""),
                "answer": meta.get("answer", ""),
                "rfi_number": meta.get("rfi_number", ""),
                "subject": meta.get("subject", row["document_title"]),
                "similarity_score": float(row["similarity_score"]),
                "project_id": str(row["project_id"]),
                "content": row["content"],
            }
        )

    logger.info(
        "RFI similarity search: %d matches above %.2f threshold",
        len(matches),
        similarity_threshold,
    )
    return matches


async def index_rfi_for_search(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
    subject: str,
    question: str,
    answer: str | None = None,
    rfi_number: str = "",
    *,
    embed_fn=None,
) -> str | None:
    """Index an RFI question-answer pair for similarity search.

    Creates a document chunk and embedding for the RFI so it can be found
    by search_similar_rfis().

    Parameters
    ----------
    db:
        Active async database session.
    rfi_id:
        UUID of the RFI record.
    project_id:
        Project UUID.
    subject:
        RFI subject line.
    question:
        The RFI question text.
    answer:
        Optional answer text (may be None for unanswered RFIs).
    rfi_number:
        The formatted RFI number (e.g., "RFI-042").
    embed_fn:
        Optional async callable to generate embeddings.

    Returns
    -------
    str | None
        The chunk_id if successfully indexed, None on failure.
    """
    import json

    # Build the text to embed: combine question and answer
    parts = [f"RFI {rfi_number}: {subject}", f"Question: {question}"]
    if answer:
        parts.append(f"Answer: {answer}")
    embed_text = "\n\n".join(parts)

    # Check if we already have a document for RFIs in this project
    find_doc = text("""
        SELECT id FROM documents
        WHERE project_id = :project_id AND type = 'rfi' AND data_source = 'rfi_index'
        LIMIT 1
    """)
    result = await db.execute(find_doc, {"project_id": str(project_id)})
    row = result.scalar_one_or_none()

    if row:
        doc_id = str(row)
    else:
        # Create an RFI index document for this project
        doc_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO documents (id, project_id, type, title, original_filename,
                    s3_key, processing_status, data_source)
                VALUES (:id, :project_id, 'rfi', 'RFI Index', 'rfi_index',
                    'rfi_index', 'complete', 'rfi_index')
            """),
            {"id": doc_id, "project_id": str(project_id)},
        )

    # Check for existing chunk for this RFI (upsert pattern).
    # The partial unique index uq_document_chunks_rfi_id
    # (see migration 037) guarantees at-most-one row per (doc_id, rfi_id)
    # — a concurrent racer gets a DB-level IntegrityError instead of a silent
    # duplicate.
    existing = text("""
        SELECT id FROM document_chunks
        WHERE document_id = :doc_id
          AND metadata->>'rfi_id' = :rfi_id
        LIMIT 1
    """)
    result = await db.execute(existing, {"doc_id": doc_id, "rfi_id": str(rfi_id)})
    existing_chunk_id = result.scalar_one_or_none()

    if existing_chunk_id:
        # Update existing chunk
        chunk_id = str(existing_chunk_id)
        await db.execute(
            text("""
                UPDATE document_chunks
                SET content = :content, metadata = CAST(:meta AS jsonb)
                WHERE id = :id
            """),
            {
                "id": chunk_id,
                "content": embed_text,
                "meta": json.dumps(
                    {
                        "rfi_id": str(rfi_id),
                        "rfi_number": rfi_number,
                        "subject": subject,
                        "question": question,
                        "answer": answer or "",
                    }
                ),
            },
        )
        # Delete old embedding
        await db.execute(
            text("DELETE FROM document_embeddings WHERE chunk_id = :id"),
            {"id": chunk_id},
        )
    else:
        # Create new chunk
        chunk_id = str(uuid.uuid4())
        # Get next chunk index
        max_idx = text(
            "SELECT COALESCE(MAX(chunk_index), -1) FROM document_chunks WHERE document_id = :doc_id"
        )
        result = await db.execute(max_idx, {"doc_id": doc_id})
        next_idx = (result.scalar() or -1) + 1

        await db.execute(
            text("""
                INSERT INTO document_chunks
                    (id, document_id, chunk_index, content, chunk_type, metadata)
                VALUES
                    (:id, :doc_id, :idx, :content, 'rfi', CAST(:meta AS jsonb))
            """),
            {
                "id": chunk_id,
                "doc_id": doc_id,
                "idx": next_idx,
                "content": embed_text,
                "meta": json.dumps(
                    {
                        "rfi_id": str(rfi_id),
                        "rfi_number": rfi_number,
                        "subject": subject,
                        "question": question,
                        "answer": answer or "",
                    }
                ),
            },
        )

    await db.flush()

    # Generate and store embedding
    try:
        if embed_fn:
            embedding = await embed_fn(embed_text)
        else:
            from app.services.rag.embeddings import embed_query

            embedding = await embed_query(embed_text)

        from app.services.rag.embeddings import get_active_model_name

        vec_str = "[" + ", ".join(str(v) for v in embedding) + "]"
        await db.execute(
            text("""
                INSERT INTO document_embeddings
                    (chunk_id, model_name, embedding_model, embedding, embedding_dim)
                VALUES
                    (:chunk_id, :model, :model, :embedding, :dim)
            """),
            {
                "chunk_id": chunk_id,
                "model": get_active_model_name(),
                "embedding": vec_str,
                "dim": len(embedding),
            },
        )
        await db.flush()
        logger.info("Indexed RFI %s for similarity search", rfi_number)
        return chunk_id
    except Exception as exc:
        logger.warning("Failed to embed RFI %s: %s", rfi_number, exc)
        return None


# ---------------------------------------------------------------------------
# OSHA standard search
# ---------------------------------------------------------------------------


async def search_osha_standards(
    db: AsyncSession,
    query: str,
    query_embedding: list[float] | None = None,
    *,
    limit: int = 10,
    org_id: uuid.UUID | None = None,
) -> list[dict]:
    """Search OSHA standards in the knowledge base.

    Uses hybrid search (vector + keyword) scoped to osha_standard documents.
    When org_id is provided, results are restricted to system-ingested OSHA
    standards (data_source='osha_ingestion') plus org-specific safety docs
    owned by that org. This prevents cross-tenant data leakage (C-02 fix).

    Parameters
    ----------
    db:
        Active async database session.
    query:
        Search query text.
    query_embedding:
        Optional pre-computed query embedding. If None, only keyword search
        is performed.
    limit:
        Maximum results.
    org_id:
        Optional organization ID to scope org-specific safety documents.
        System-ingested OSHA standards (project_id IS NULL) are always included.

    Returns
    -------
    list[dict]
        Each dict: chunk_id, content, standard_number, subpart, topic,
        applicability, score.
    """
    limit = min(limit, 100)  # Cap maximum results to prevent excessive queries
    results: list[dict] = []

    # SECURITY (C-02) / M-15: Scope OSHA search to prevent cross-org data
    # leakage. System-ingested OSHA standards have project_id IS NULL and
    # data_source='osha_ingestion' and are visible to everyone. Org-uploaded
    # safety docs must be filtered by org_id via the project relationship.
    #
    # When ``org_id`` is None we ONLY return system content (as before), but
    # log a WARN so production callers that forgot to pass org_id are
    # visible in operations.
    if org_id is None:
        logger.warning(
            "search_osha_standards called without org_id — returning "
            "system-ingested content only. Authenticated callers must pass org_id."
        )
    params: dict = {"limit": limit}
    has_org = org_id is not None
    params["org_id"] = str(org_id) if has_org else ""
    params["has_org"] = has_org

    if query_embedding:
        vec_str = "[" + ", ".join(str(v) for v in query_embedding) + "]"
        params["query_vec"] = vec_str
        stmt = text("""
            SELECT
                dc.id            AS chunk_id,
                dc.content       AS content,
                dc.metadata      AS chunk_metadata,
                1 - (de.embedding <=> CAST(:query_vec AS vector)) AS score
            FROM document_embeddings de
            JOIN document_chunks dc ON dc.id = de.chunk_id
            JOIN documents d         ON d.id = dc.document_id
            LEFT JOIN projects p     ON p.id = d.project_id
            WHERE d.type = 'osha_standard'
              AND (
                  CASE
                      WHEN :has_org THEN (d.project_id IS NULL OR p.org_id = :org_id)
                      ELSE d.project_id IS NULL
                  END
              )
            ORDER BY de.embedding <=> CAST(:query_vec AS vector) ASC
            LIMIT :limit
        """)
        result = await db.execute(stmt, params)
        rows = result.mappings().all()

        for row in rows:
            meta = row["chunk_metadata"] or {}
            results.append(
                {
                    "chunk_id": str(row["chunk_id"]),
                    "content": row["content"],
                    "standard_number": meta.get("standard_number", ""),
                    "subpart": meta.get("subpart", ""),
                    "topic": meta.get("topic", ""),
                    "applicability": meta.get("applicability", ""),
                    "score": float(row["score"]),
                }
            )
    else:
        # Keyword-only fallback
        osha_escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["query"] = query
        params["pattern"] = f"%{osha_escaped}%"
        stmt = text("""
            SELECT
                dc.id            AS chunk_id,
                dc.content       AS content,
                dc.metadata      AS chunk_metadata,
                similarity(dc.content, :query) AS score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            LEFT JOIN projects p ON p.id = d.project_id
            WHERE d.type = 'osha_standard'
              AND (dc.content % :query OR dc.content ILIKE :pattern)
              AND (
                  CASE
                      WHEN :has_org THEN (d.project_id IS NULL OR p.org_id = :org_id)
                      ELSE d.project_id IS NULL
                  END
              )
            ORDER BY similarity(dc.content, :query) DESC
            LIMIT :limit
        """)
        result = await db.execute(stmt, params)
        rows = result.mappings().all()

        for row in rows:
            meta = row["chunk_metadata"] or {}
            results.append(
                {
                    "chunk_id": str(row["chunk_id"]),
                    "content": row["content"],
                    "standard_number": meta.get("standard_number", ""),
                    "subpart": meta.get("subpart", ""),
                    "topic": meta.get("topic", ""),
                    "applicability": meta.get("applicability", ""),
                    "score": float(row["score"]),
                }
            )

    return results
