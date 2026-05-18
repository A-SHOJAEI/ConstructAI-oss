"""Document comparison API — diff two document versions."""

from __future__ import annotations

import difflib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


class DiffSection(BaseModel):
    section: str = ""
    change_type: str = Field(description="added | removed | modified | unchanged")
    old_text: str = ""
    new_text: str = ""
    line_start: int = 0
    line_end: int = 0


class ComparisonResult(BaseModel):
    document_a_id: str
    document_b_id: str
    total_sections: int
    added: int
    removed: int
    modified: int
    unchanged: int
    similarity_ratio: float
    diffs: list[DiffSection]


class ComparisonRequest(BaseModel):
    document_a_id: str
    document_b_id: str
    context_lines: int = Field(default=3, ge=0, le=20)


def _extract_text_from_chunks(chunks: list[dict]) -> str:
    """Combine document chunks into full text."""
    sorted_chunks = sorted(chunks, key=lambda c: c.get("chunk_index", 0))
    return "\n".join(c.get("content", "") for c in sorted_chunks)


def _compute_diff(
    text_a: str,
    text_b: str,
    context_lines: int = 3,
) -> ComparisonResult:
    """Compute structured diff between two text documents."""
    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    ratio = matcher.ratio()

    diffs: list[DiffSection] = []
    added = 0
    removed = 0
    modified = 0
    unchanged = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_text = "".join(lines_a[i1:i2])
        new_text = "".join(lines_b[j1:j2])

        if tag == "equal":
            unchanged += 1
            # Only include if near a change for context
            continue
        elif tag == "insert":
            added += 1
            diffs.append(
                DiffSection(
                    change_type="added",
                    new_text=new_text.strip(),
                    line_start=j1 + 1,
                    line_end=j2,
                )
            )
        elif tag == "delete":
            removed += 1
            diffs.append(
                DiffSection(
                    change_type="removed",
                    old_text=old_text.strip(),
                    line_start=i1 + 1,
                    line_end=i2,
                )
            )
        elif tag == "replace":
            modified += 1
            diffs.append(
                DiffSection(
                    change_type="modified",
                    old_text=old_text.strip(),
                    new_text=new_text.strip(),
                    line_start=i1 + 1,
                    line_end=max(i2, j2),
                )
            )

    return ComparisonResult(
        document_a_id="",
        document_b_id="",
        total_sections=added + removed + modified + unchanged,
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
        similarity_ratio=round(ratio, 4),
        diffs=diffs,
    )


@router.post(
    "/projects/{project_id}/documents/compare",
    response_model=ComparisonResult,
)
async def compare_documents(
    project_id: uuid.UUID,
    body: ComparisonRequest,
    user: Annotated[User, Depends(require_permission("documents", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    max_diffs: int = Query(200, ge=1, le=1000),
):
    """Compare two document versions and return structured diff."""
    await verify_project_access(project_id, user, db)

    from sqlalchemy import select

    # Fetch document chunks for both documents
    try:
        from app.models.document import Document, DocumentChunk
    except ImportError as err:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Document models not available",
        ) from err

    doc_a = await db.execute(
        select(Document).where(
            Document.id == body.document_a_id,
            Document.project_id == project_id,
        )
    )
    doc_a_row = doc_a.scalar_one_or_none()
    if not doc_a_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {body.document_a_id} not found",
        )

    doc_b = await db.execute(
        select(Document).where(
            Document.id == body.document_b_id,
            Document.project_id == project_id,
        )
    )
    doc_b_row = doc_b.scalar_one_or_none()
    if not doc_b_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {body.document_b_id} not found",
        )

    # Get chunks
    chunks_a_result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == body.document_a_id)
        .order_by(DocumentChunk.chunk_index)
    )
    chunks_a = [
        {"content": c.content, "chunk_index": c.chunk_index}
        for c in chunks_a_result.scalars().all()
    ]

    chunks_b_result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == body.document_b_id)
        .order_by(DocumentChunk.chunk_index)
    )
    chunks_b = [
        {"content": c.content, "chunk_index": c.chunk_index}
        for c in chunks_b_result.scalars().all()
    ]

    text_a = _extract_text_from_chunks(chunks_a)
    text_b = _extract_text_from_chunks(chunks_b)

    result = _compute_diff(text_a, text_b, body.context_lines)
    result.document_a_id = body.document_a_id
    result.document_b_id = body.document_b_id
    result.diffs = result.diffs[:max_diffs]

    logger.info(
        "Document comparison: %s vs %s — %.1f%% similar, %d diffs",
        body.document_a_id[:8],
        body.document_b_id[:8],
        result.similarity_ratio * 100,
        len(result.diffs),
    )

    return result


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions",
)
async def list_document_versions(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("documents", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
):
    """List versions of a document for comparison."""
    await verify_project_access(project_id, user, db)

    from sqlalchemy import select

    try:
        from app.models.document import Document
    except ImportError as err:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Document models not available",
        ) from err

    # Find documents with same title/name in the project (different versions)
    doc = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    doc_row = doc.scalar_one_or_none()
    if not doc_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Search for related versions by filename stem
    base_name = doc_row.original_filename.rsplit(".", 1)[0] if doc_row.original_filename else ""
    # Escape SQL LIKE wildcards in filename
    escaped_name = base_name.replace("%", "\\%").replace("_", "\\_")
    versions_result = await db.execute(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.original_filename.ilike(f"{escaped_name}%"),
        )
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    versions = versions_result.scalars().all()

    return {
        "document_id": document_id,
        "versions": [
            {
                "id": str(v.id),
                "filename": v.original_filename,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "status": v.status if hasattr(v, "status") else "complete",
            }
            for v in versions
        ],
    }
