"""Document CRUD operations and processing orchestration service."""

from __future__ import annotations

import contextlib
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk, DocumentEntity
from app.services.rag.embeddings import embed_query
from app.utils.pagination import paginate
from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)


async def create_document(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    title: str,
    original_filename: str,
    doc_type: str,
    s3_key: str,
    file_size_bytes: int,
    uploaded_by: uuid.UUID,
) -> Document:
    """Create a new document record.

    Args:
        db: Active async database session.
        project_id: UUID of the parent project.
        title: Display title for the document.
        original_filename: Original name of the uploaded file.
        doc_type: Document type (e.g., specification, drawing).
        s3_key: S3 object key where the file is stored.
        file_size_bytes: Size of the uploaded file in bytes.
        uploaded_by: UUID of the user who uploaded the document.

    Returns:
        The newly created ``Document`` instance.
    """
    document = Document(
        project_id=project_id,
        title=title,
        original_filename=original_filename,
        type=doc_type,
        s3_key=s3_key,
        file_size_bytes=file_size_bytes,
        uploaded_by=uploaded_by,
        processing_status="pending",
    )
    db.add(document)
    await db.flush()
    await db.refresh(document)
    return document


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    """Retrieve a single document by its ID.

    Returns:
        The ``Document`` instance, or ``None`` if not found.
    """
    return await db.get(Document, document_id)


async def list_documents(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List documents for a project with cursor-based pagination.

    Returns:
        A dict with ``data`` (list of documents) and ``meta`` (pagination info).
    """
    query = select(Document).where(Document.project_id == project_id)
    return await paginate(db, query, cursor=cursor, limit=limit, model=Document)


async def get_document_status(db: AsyncSession, document_id: uuid.UUID) -> dict | None:
    """Get the processing status of a document, including chunk and entity counts.

    Returns:
        A dict with id, processing_status, processing_error, page_count,
        chunk_count, and entity_count.
    """
    document = await db.get(Document, document_id)
    if document is None:
        return None

    chunk_count_result = await db.execute(
        select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
    )
    chunk_count = chunk_count_result.scalar() or 0

    entity_count_result = await db.execute(
        select(func.count(DocumentEntity.id)).where(DocumentEntity.document_id == document_id)
    )
    entity_count = entity_count_result.scalar() or 0

    return {
        "id": document.id,
        "processing_status": document.processing_status,
        "processing_error": document.processing_error,
        "page_count": document.page_count,
        "chunk_count": chunk_count,
        "entity_count": entity_count,
    }


async def search_documents(
    db: AsyncSession,
    query: str,
    query_embedding: list[float],
    project_id: uuid.UUID,
    limit: int = 10,
) -> list[dict]:
    """Search documents using hybrid search and reranking.

    Args:
        db: Active async database session.
        query: The search query text.
        query_embedding: Pre-computed embedding vector for the query.
        project_id: UUID of the project to search within.
        limit: Maximum number of results to return.

    Returns:
        A list of reranked search result dicts.
    """
    from app.services.rag.retrieval import hybrid_search

    results = await hybrid_search(
        db,
        query=query,
        query_embedding=query_embedding,
        project_id=project_id,
        limit=limit,
    )

    try:
        from app.services.rag.reranker import rerank

        results = await rerank(query=query, results=results, top_n=limit)
    except ImportError:
        logger.warning("Reranker module not available; returning unranked results")

    return results


async def ask_question(
    db: AsyncSession,
    question: str,
    project_id: uuid.UUID,
) -> dict:
    """Answer a question using RAG over project documents.

    Embeds the question, retrieves relevant chunks, and generates an
    answer with source citations.

    Args:
        db: Active async database session.
        question: The user's natural language question.
        project_id: UUID of the project to search within.

    Returns:
        A dict with answer, confidence, sources, and model_used.
    """
    from langchain_openai import ChatOpenAI

    # Embed the question for vector search
    question_embedding = await embed_query(question)

    # Retrieve relevant document chunks
    search_results = await search_documents(
        db,
        query=question,
        query_embedding=question_embedding,
        project_id=project_id,
        limit=10,
    )

    if not search_results:
        return {
            "answer": "No relevant information found in the project documents.",
            "confidence": 0.0,
            "sources": [],
            "model_used": "gpt-4o-mini",
        }

    # Build context from search results (sanitize to prevent prompt injection)
    sanitized_question = sanitize_for_prompt(question)
    context_parts = []
    sources = []
    for idx, result in enumerate(search_results):
        sanitized_title = sanitize_for_prompt(result.get("document_title", "Unknown"))
        sanitized_content = sanitize_for_prompt(result.get("content", ""))
        context_parts.append(
            f"[Source {idx + 1}] (Document: {sanitized_title}, "
            f"Page: {result.get('page_number', 'N/A')})\n{sanitized_content}"
        )
        sources.append(
            {
                "chunk_id": result.get("chunk_id"),
                "document_name": result.get("document_title", "Unknown"),
                "page_number": result.get("page_number"),
                "section": result.get("csi_section"),
                "relevance_score": result.get("score", 0.0),
            }
        )

    context = "\n\n".join(context_parts)

    prompt = (
        "You are an expert construction document assistant. Answer the following "
        "question based ONLY on the provided context from project documents. "
        "If the context does not contain enough information, say so clearly.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {sanitized_question}\n\n"
        "Provide a clear, concise answer. Reference the source documents when possible."
    )

    model_name = "gpt-4o-mini"
    llm = ChatOpenAI(model_name=model_name, temperature=0)

    try:
        response = await llm.ainvoke(prompt)
        answer = (
            response.content if isinstance(response.content, str) else str(response.content)
        ).strip()

        # Try to parse confidence from the LLM response metadata;
        # otherwise use 0.5 as a neutral default.
        # TODO: Calculate confidence from retrieval scores and answer quality metrics.
        confidence = 0.5
        resp_metadata = getattr(response, "response_metadata", {}) or {}
        if "confidence" in resp_metadata:
            with contextlib.suppress(TypeError, ValueError):
                confidence = float(resp_metadata["confidence"])

        return {
            "answer": answer,
            "confidence": confidence,
            "sources": sources,
            "model_used": model_name,
        }
    except Exception as exc:
        logger.error("Question answering failed: %s", exc)
        return {
            "answer": "An error occurred while generating the answer.",
            "confidence": 0.0,
            "sources": [],
            "model_used": model_name,
        }
