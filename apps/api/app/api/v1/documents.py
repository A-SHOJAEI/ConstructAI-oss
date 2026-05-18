"""Document API endpoints for upload, retrieval, search, and analysis."""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.document import (
    AskRequest,
    AskResponse,
    ClassificationResponse,
    ClassifyRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusResponse,
    DocumentUploadResponse,
    EntitiesListResponse,
    EntityResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SourceCitation,
)
from app.services import document_service
from app.services.agents.classifier import classify_document
from app.services.rag.embeddings import embed_query
from app.utils.s3 import upload_file

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed MIME types and extensions for upload validation
ALLOWED_EXTENSIONS: set[str] = {".pdf", ".ifc", ".csv", ".docx"}
MAX_FILE_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB

# Magic byte signatures for allowed file types
_MAGIC_BYTES: dict[str, list[bytes]] = {
    ".pdf": [b"%PDF"],
    ".docx": [b"PK\x03\x04"],  # ZIP-based (OOXML)
    ".csv": [],  # Text-based; no reliable magic bytes
    ".ifc": [b"ISO-10303-21", b"STEP;"],  # IFC STEP format
}

# Server-determined content types (never trust the client)
_EXT_TO_CONTENT_TYPE: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".ifc": "application/x-step",
}


def _validate_file_extension(filename: str) -> str:
    """Return the lowercased file extension or raise 422 if not allowed."""
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{ext}' is not supported. "
            f"Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return ext


def _validate_file_content(file_bytes: bytes, ext: str) -> None:
    """Validate file content matches the claimed extension via magic bytes."""
    signatures = _MAGIC_BYTES.get(ext, [])
    if signatures:
        header = file_bytes[:32]
        if not any(header.startswith(sig) for sig in signatures):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File content does not match the '{ext}' file type.",
            )
        return

    # L-6: CSV has no magic bytes, but we can still catch a binary blob
    # renamed to .csv by sampling the first 200 bytes. A legitimate CSV is
    # printable ASCII/UTF-8 and contains at least one common delimiter.
    if ext == ".csv":
        sample = file_bytes[:200]
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="CSV file must be valid UTF-8.",
            ) from exc
        if not any(delim in text for delim in (",", ";", "\t")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="File does not appear to be CSV (no delimiter found).",
            )


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    project_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("documents", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document to a project.

    Validates file type and size, uploads to S3, creates a database record,
    and optionally triggers async processing.
    """
    await verify_project_access(project_id, current_user, db)

    # Validate file extension
    ext = _validate_file_extension(file.filename or "")

    # Check declared size first (if available from client) to reject
    # obviously oversized uploads before reading into memory.
    if file.size and file.size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    # Read file content
    file_bytes = await file.read()

    # Validate file content matches claimed extension via magic bytes
    # (never trust client-provided content_type — it can be trivially spoofed)
    _validate_file_content(file_bytes, ext)

    # Log a warning if the client-declared content_type diverges from what
    # the server determined from the extension + magic bytes.  This does not
    # block the request (the server-side value is authoritative) but aids
    # in detecting potential abuse or misconfigured clients.
    server_content_type = _EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream")
    if file.content_type and file.content_type != server_content_type:
        logger.warning(
            "Client content_type mismatch: client=%s, server=%s, filename=%s",
            file.content_type,
            server_content_type,
            file.filename,
        )

    # Validate file size
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds the maximum allowed size of "
            f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    # Generate S3 key
    file_id = uuid.uuid4()
    s3_key = f"documents/{project_id}/{file_id}{ext}"

    # Upload to S3 with server-determined content type (never trust client)
    content_type = _EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream")
    upload_file(s3_key, file_bytes, content_type)

    # Determine initial doc type from extension
    ext_to_type = {
        ".pdf": "specification",
        ".ifc": "bim_model",
        ".csv": "schedule",
        ".docx": "other",
    }
    doc_type = ext_to_type.get(ext, "other")

    # Create document record
    document = await document_service.create_document(
        db,
        project_id=project_id,
        title=file.filename or "Untitled",
        original_filename=file.filename or "upload",
        doc_type=doc_type,
        s3_key=s3_key,
        file_size_bytes=len(file_bytes),
        uploaded_by=current_user.id,
    )

    # Trigger async processing (best-effort; do not fail the upload)
    try:
        from app.workers.document_worker import process_document_task

        process_document_task.delay(str(document.id))
    except Exception as e:
        logger.error("Failed to dispatch document processing task for %s: %s", document.id, e)
        document.processing_status = "pending_retry"
        await db.commit()

    return document


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    project_id: uuid.UUID = Query(..., description="Project to list documents for"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List documents for a project with cursor-based pagination."""
    await verify_project_access(project_id, current_user, db)

    result = await document_service.list_documents(
        db, project_id=project_id, cursor=cursor, limit=limit
    )
    return result


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single document by ID."""
    document = await document_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await verify_project_access(document.project_id, current_user, db)
    return document


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: uuid.UUID,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the processing status of a document."""
    # First verify the user has access to the document's project
    document = await document_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await verify_project_access(document.project_id, current_user, db)

    status_data = await document_service.get_document_status(db, document_id)
    if status_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return status_data


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Search documents within a project using hybrid search."""
    await verify_project_access(request.project_id, current_user, db)

    query_embedding = await embed_query(request.query)

    results = await document_service.search_documents(
        db,
        query=request.query,
        query_embedding=query_embedding,
        project_id=request.project_id,
        limit=request.limit,
    )

    return SearchResponse(
        results=[
            SearchResultItem(
                chunk_id=r.get("chunk_id"),
                content=r.get("content", ""),
                document_id=r.get("document_id"),
                document_title=r.get("document_title", ""),
                page_number=r.get("page_number"),
                section_hierarchy=r.get("section_hierarchy", []),
                csi_section=r.get("csi_section"),
                score=r.get("score", 0.0),
            )
            for r in results
        ],
        query=request.query,
        total=len(results),
    )


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Ask a natural language question over project documents."""
    await verify_project_access(request.project_id, current_user, db)

    result = await document_service.ask_question(
        db, question=request.question, project_id=request.project_id
    )

    return AskResponse(
        answer=result["answer"],
        confidence=result["confidence"],
        sources=[
            SourceCitation(
                chunk_id=s["chunk_id"],
                document_name=s["document_name"],
                page_number=s.get("page_number"),
                section=s.get("section"),
                relevance_score=s.get("relevance_score", 0.0),
            )
            for s in result.get("sources", [])
        ],
        model_used=result["model_used"],
    )


@router.post("/classify", response_model=ClassificationResponse)
async def classify_document_endpoint(
    request: ClassifyRequest,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Classify a document using LLM analysis."""
    document = await document_service.get_document(db, request.document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await verify_project_access(document.project_id, current_user, db)

    # Gather text from the first few chunks for classification
    text_sample = ""
    if document.chunks:
        text_sample = "\n".join(chunk.content for chunk in document.chunks[:5] if chunk.content)

    if not text_sample:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document has no extracted text content for classification.",
        )

    classification = await classify_document(
        text_sample=text_sample,
        filename=document.original_filename,
    )

    return ClassificationResponse(
        document_id=document.id,
        classified_type=classification["classified_type"],
        csi_division=classification.get("csi_division"),
        discipline=classification.get("discipline"),
        confidence=classification["confidence"],
        model_used=classification["model_used"],
    )


@router.get("/{document_id}/entities", response_model=EntitiesListResponse)
async def get_document_entities(
    document_id: uuid.UUID,
    current_user: User = Depends(require_permission("documents", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get all extracted entities for a document."""
    document = await document_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await verify_project_access(document.project_id, current_user, db)

    entities = document.entities or []

    return EntitiesListResponse(
        document_id=document.id,
        entities=[
            EntityResponse(
                id=entity.id,
                entity_type=entity.entity_type,
                entity_value=entity.entity_value,
                section_reference=entity.section_reference,
                confidence=float(entity.confidence) if entity.confidence else None,
            )
            for entity in entities
        ],
        total=len(entities),
    )
