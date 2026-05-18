"""Ingestion pipeline - orchestrates parsing, chunking, and storage for uploaded documents."""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk

# SECURITY [M-22]: Use smart chunker for spec-aware document processing
from app.services.ingestion.chunking import chunk_document_smart
from app.services.ingestion.embedder import embed_chunks, store_chunk_embeddings
from app.services.ingestion.ifc_parser import parse_ifc
from app.services.ingestion.pdf_parser import ParsedPage, parse_pdf
from app.services.ingestion.schedule_parser import parse_schedule
from app.utils.s3 import download_file

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# File-type helpers
# ---------------------------------------------------------------------------

_PDF_TYPES = {"pdf", "application/pdf"}
_IFC_TYPES = {"ifc", "application/x-step", "application/x-ifc"}
_SCHEDULE_TYPES = {"csv", "tsv", "text/csv"}


def _classify_document(document: Document) -> str:
    """Return a normalised format tag based on the document's type and filename."""
    doc_type = (document.type or "").lower().strip()
    filename = (document.original_filename or "").lower()

    if doc_type in _PDF_TYPES or filename.endswith(".pdf"):
        return "pdf"
    if doc_type in _IFC_TYPES or filename.endswith(".ifc"):
        return "ifc"
    if doc_type in _SCHEDULE_TYPES or filename.endswith((".csv", ".tsv")):
        return "schedule"
    # Default: treat as plain-text schedule (best-effort).
    return "schedule"


# ---------------------------------------------------------------------------
# Status management
# ---------------------------------------------------------------------------


async def _set_status(
    db: AsyncSession,
    document: Document,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Persist a processing status change."""
    document.processing_status = status
    if error is not None:
        document.processing_error = error
    await db.flush()
    logger.info(
        "document_status_changed",
        document_id=str(document.id),
        status=status,
        error=error,
    )


# ---------------------------------------------------------------------------
# Public pipeline entry-point
# ---------------------------------------------------------------------------


async def process_document(document_id: uuid.UUID, db: AsyncSession) -> None:
    """Run the full ingestion pipeline for a single document.

    Lifecycle
    ---------
    ``pending`` -> ``processing`` -> ``chunking`` -> ``embedding`` -> ``complete``

    On any unhandled error the status is set to ``failed`` with a descriptive
    message stored in ``processing_error``.
    """
    # --- Fetch document record ---
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        logger.error("document_not_found", document_id=str(document_id))
        return

    try:
        # --- Download from S3 ---
        await _set_status(db, document, "processing")
        file_bytes = await asyncio.to_thread(download_file, document.s3_key)

        # --- Parse based on file type ---
        format_tag = _classify_document(document)
        pages: list[ParsedPage] = []
        page_count: int | None = None
        doc_metadata: dict = {}

        if format_tag == "pdf":
            pdf_result = await asyncio.to_thread(parse_pdf, file_bytes)
            pages = pdf_result.pages
            page_count = pdf_result.page_count
            doc_metadata = pdf_result.metadata
        elif format_tag == "ifc":
            ifc_result = await asyncio.to_thread(parse_ifc, file_bytes)
            # Convert IFC entities into pseudo-pages so the chunker can handle them.
            if ifc_result.entities:
                combined_text = "\n".join(
                    f"{e['type']}: {e.get('name') or e['id']}" for e in ifc_result.entities
                )
                pages = [
                    ParsedPage(
                        page_number=1,
                        text=combined_text,
                        tables=[],
                        headings=[],
                    )
                ]
            doc_metadata = ifc_result.metadata
        elif format_tag == "schedule":
            sched_result = parse_schedule(file_bytes, document.original_filename)
            if sched_result.tasks:
                # Render tasks as a table-like page for the chunker.
                header = " | ".join(sched_result.columns)
                rows_text = "\n".join(
                    " | ".join(task.get(col, "") for col in sched_result.columns)
                    for task in sched_result.tasks
                )
                page_text = f"{header}\n{rows_text}"
                pages = [
                    ParsedPage(
                        page_number=1,
                        text=page_text,
                        tables=[],
                        headings=[],
                    )
                ]
            doc_metadata = {
                "columns": sched_result.columns,
                "row_count": sched_result.row_count,
            }

        # Update document metadata.
        document.metadata_ = doc_metadata
        if page_count is not None:
            document.page_count = page_count

        # --- Chunk ---
        await _set_status(db, document, "chunking")
        chunks = chunk_document_smart(pages)

        # Persist chunks and collect ORM objects for embedding.
        db_chunks: list[DocumentChunk] = []
        for idx, chunk in enumerate(chunks):
            db_chunk = DocumentChunk(
                document_id=document.id,
                chunk_index=idx,
                content=chunk.content,
                chunk_type=chunk.chunk_type,
                page_number=chunk.page_number,
                section_hierarchy=chunk.section_hierarchy,
                csi_section=chunk.csi_section,
                token_count=chunk.token_count,
            )
            db.add(db_chunk)
            db_chunks.append(db_chunk)

        # Flush so that each DocumentChunk gets a database-assigned id.
        await db.flush()

        # --- Embedding ---
        await _set_status(db, document, "embedding")
        embedding_count = 0

        embedding_failed = False
        if db_chunks:
            try:
                chunk_texts = [c.content for c in db_chunks]
                vectors = await embed_chunks(chunk_texts)
                embedding_count = await store_chunk_embeddings(db, db_chunks, vectors)
            except Exception as embed_exc:
                # Embedding failure is non-fatal: the document is still usable
                # for keyword search even without vectors.  Flag it so the UI
                # can show a warning that semantic search won't work for this doc.
                embedding_failed = True
                logger.warning(
                    "embedding_step_failed",
                    document_id=str(document.id),
                    error=str(embed_exc),
                )

        # Mark as complete, but flag embedding failure in metadata.
        if embedding_failed:
            doc_metadata = document.metadata_ or {}
            doc_metadata["embedding_failed"] = True
            doc_metadata["embedding_error"] = "Embedding generation failed; keyword search only."
            document.metadata_ = doc_metadata
        await _set_status(db, document, "complete")

        logger.info(
            "document_processing_complete",
            document_id=str(document.id),
            format=format_tag,
            chunk_count=len(chunks),
            embedding_count=embedding_count,
            page_count=page_count,
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        await _set_status(db, document, "failed", error=error_msg)
        logger.exception(
            "document_processing_failed",
            document_id=str(document.id),
            error=str(exc),
        )
