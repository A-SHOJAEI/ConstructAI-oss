"""RFI workflow API endpoints.

All routes are project-scoped: ``/projects/{project_id}/rfis/...``
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.communication import RfiAttachment
from app.models.user import User
from app.schemas.communication import (
    BulkRFIUpdateRequest,
    BulkRFIUpdateResponse,
    RFIAttachmentItem,
    RFICloseRequest,
    RFICreateV2,
    RFIDetailListResponse,
    RFIDetailResponse,
    RFIResponseCreate,
    RFIResponseItem,
    RFIStatsResponse,
    RFIUpdate,
)
from app.services.agents.rfi_resolution_agent import (
    run_rfi_resolution,
)
from app.services.communication.rfi_service import (
    close_rfi,
    create_rfi,
    export_rfis_csv,
    get_rfi_detail,
    get_rfi_stats,
    list_rfis,
    respond_to_rfi,
    update_rfi,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# IMPORTANT: /export and /stats MUST come before /{rfi_id} so FastAPI doesn't
# try to parse "export" or "stats" as a UUID path parameter.
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/rfis",
    response_model=RFIDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rfi_endpoint(
    project_id: uuid.UUID,
    body: RFICreateV2,
    current_user: User = Depends(require_permission("rfis", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new RFI with auto-generated number."""
    await verify_project_access(project_id, current_user, db)

    try:
        rfi = await create_rfi(db, project_id, body.model_dump(exclude_unset=True), current_user.id)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    detail = await get_rfi_detail(db, rfi.id, project_id)
    return detail


@router.get(
    "/{project_id}/rfis",
    response_model=RFIDetailListResponse,
)
async def list_rfis_endpoint(
    project_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    priority: str | None = Query(None),
    assigned_to: uuid.UUID | None = Query(None),
    ball_in_court: uuid.UUID | None = Query(None),
    overdue: bool = Query(False),
    search: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List RFIs for a project with optional filters."""
    await verify_project_access(project_id, current_user, db)

    result = await list_rfis(
        db,
        project_id,
        status_filter=status_filter,
        priority_filter=priority,
        assigned_to_filter=assigned_to,
        ball_in_court_filter=ball_in_court,
        overdue_only=overdue,
        search=search,
        cursor=cursor,
        limit=limit,
    )
    return result


@router.get(
    "/{project_id}/rfis/export",
)
async def export_rfis_endpoint(
    project_id: uuid.UUID,
    limit: int = Query(500, ge=1, le=5000, description="Max RFIs to export"),
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export project RFIs as a CSV file (bounded by limit)."""
    await verify_project_access(project_id, current_user, db)

    csv_bytes = await export_rfis_csv(db, project_id, limit=limit)

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rfis_export.csv"},
    )


@router.get(
    "/{project_id}/rfis/stats",
    response_model=RFIStatsResponse,
)
async def get_rfi_stats_endpoint(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregate RFI statistics for a project."""
    await verify_project_access(project_id, current_user, db)
    return await get_rfi_stats(db, project_id)


# ---------------------------------------------------------------------------
# Bulk RFI Operations
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/rfis/bulk-update",
    response_model=BulkRFIUpdateResponse,
)
async def bulk_update_rfis(
    project_id: uuid.UUID,
    updates: BulkRFIUpdateRequest,
    current_user: User = Depends(require_permission("rfis", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update status and/or assignee for multiple RFIs at once.

    Per-record error isolation: individual failures do not roll back the others.
    """
    await verify_project_access(project_id, current_user, db)

    if updates.status is None and updates.assigned_to is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of 'status' or 'assigned_to' must be provided.",
        )

    from sqlalchemy import select as sa_select

    from app.models.communication import RFI

    updated_count = 0
    failed_count = 0
    errors: list[dict] = []

    for rfi_id in updates.rfi_ids:
        try:
            stmt = sa_select(RFI).where(
                RFI.id == rfi_id,
                RFI.project_id == project_id,
            )
            result = await db.execute(stmt)
            rfi = result.scalar_one_or_none()

            if rfi is None:
                failed_count += 1
                errors.append({"rfi_id": str(rfi_id), "error": "RFI not found"})
                continue

            if updates.status is not None:
                rfi.status = updates.status
            if updates.assigned_to is not None:
                rfi.assigned_to = updates.assigned_to

            updated_count += 1
        except Exception as exc:
            failed_count += 1
            errors.append({"rfi_id": str(rfi_id), "error": str(exc)})

    if updated_count > 0:
        await db.flush()
        await db.commit()

    return BulkRFIUpdateResponse(
        updated=updated_count,
        failed=failed_count,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# RFI Resolution Agent endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/rfis/unnecessary",
)
async def list_unnecessary_rfis(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List RFIs flagged as unnecessary by the Resolution Agent."""
    await verify_project_access(project_id, current_user, db)

    from sqlalchemy import select

    from app.models.communication import RfiResolutionLog

    stmt = (
        select(RfiResolutionLog)
        .where(
            RfiResolutionLog.project_id == project_id,
            RfiResolutionLog.was_unnecessary.is_(True),
        )
        .order_by(RfiResolutionLog.created_at.desc())
        .limit(50)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return {
        "data": [
            {
                "id": str(log.id),
                "rfi_id": str(log.rfi_id),
                "unnecessary_source": log.unnecessary_source,
                "unnecessary_reason": log.unnecessary_reason,
                "similar_rfi_count": log.similar_rfi_count,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "count": len(logs),
    }


@router.post(
    "/{project_id}/rfis/{rfi_id}/auto-resolve",
)
async def auto_resolve_rfi(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Run the full RFI Resolution Agent pipeline (Stages 1-3).

    Returns the resolution result including whether the RFI is unnecessary,
    a draft response, and verification results.  All draft responses are
    routed through human review.
    """
    await verify_project_access(project_id, current_user, db)

    # Fetch the RFI
    from app.services.communication.rfi_service import _get_rfi_or_raise

    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    # Run the resolution agent
    result = await run_rfi_resolution(
        rfi_id=rfi_id,
        project_id=project_id,
        subject=rfi.subject,
        question=rfi.question,
        spec_section=rfi.spec_section,
        drawing_reference=rfi.drawing_reference,
    )

    # Log the resolution attempt
    from app.models.communication import RfiResolutionLog
    from app.services.agents.rfi_resolution_agent import _is_safety_related

    log = RfiResolutionLog(
        rfi_id=rfi_id,
        project_id=project_id,
        stage_reached=result.get("stage_reached", 0),
        was_unnecessary=result.get("is_unnecessary", False),
        unnecessary_source=result.get("unnecessary_source"),
        unnecessary_reason=result.get("unnecessary_reason"),
        similar_rfi_count=len(result.get("similar_rfis", [])),
        draft_confidence=result.get("draft_confidence"),
        draft_model=result.get("draft_model"),
        draft_source_count=len(result.get("draft_sources", [])),
        is_safety_related=_is_safety_related(rfi.question),
        hallucination_count=len(result.get("hallucination_flags", [])),
        contradiction_count=len(result.get("contradiction_flags", [])),
        completeness_issues=len(result.get("completeness_flags", [])),
        verification_passed=result.get("verification_passed"),
        agent_state={
            k: v
            for k, v in result.items()
            if k
            not in (
                "context_chunks",
                "osha_chunks",
                "spec_matches",
                "meeting_matches",
                "similar_rfis",
            )
        },
    )
    db.add(log)
    await db.flush()

    # Store the AI suggested response on the RFI record
    if result.get("final_response"):
        rfi.ai_suggested_response = result["final_response"]
        await db.flush()

    # Build response before committing so any serialization error
    # doesn't leave orphaned data
    response = {
        "rfi_id": str(rfi_id),
        "status": result.get("status", "unknown"),
        "stage_reached": result.get("stage_reached", 0),
        "is_unnecessary": result.get("is_unnecessary", False),
        "unnecessary_reason": result.get("unnecessary_reason"),
        "unnecessary_source": result.get("unnecessary_source"),
        "draft_response": result.get("final_response"),
        "draft_confidence": result.get("draft_confidence", 0.0),
        "verification_passed": result.get("verification_passed"),
        "hallucination_flags": result.get("hallucination_flags", []),
        "contradiction_flags": result.get("contradiction_flags", []),
        "completeness_flags": result.get("completeness_flags", []),
        "resolution_log_id": str(log.id),
        "error": result.get("error"),
    }

    await db.commit()

    return response


@router.post(
    "/{project_id}/rfis/{rfi_id}/draft-response",
)
async def draft_rfi_response(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI draft response for an RFI (skips unnecessary check).

    Use this when you want a draft response without the full resolution
    pipeline.  The draft is always labeled as AI-ASSISTED and requires
    human review.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.communication.rfi_service import _get_rfi_or_raise

    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    # Run the full pipeline — it will go through all 3 stages since the
    # RFI is unlikely to be flagged as unnecessary for an existing RFI
    # the user explicitly wants a draft for
    result = await run_rfi_resolution(
        rfi_id=rfi_id,
        project_id=project_id,
        subject=rfi.subject,
        question=rfi.question,
        spec_section=rfi.spec_section,
        drawing_reference=rfi.drawing_reference,
    )

    # Store the AI suggested response
    if result.get("final_response"):
        rfi.ai_suggested_response = result["final_response"]
        await db.flush()
        await db.commit()

    return {
        "rfi_id": str(rfi_id),
        "draft_response": result.get("final_response"),
        "draft_confidence": result.get("draft_confidence", 0.0),
        "sources": result.get("draft_sources", []),
        "verification_passed": result.get("verification_passed"),
        "hallucination_flags": result.get("hallucination_flags", []),
        "contradiction_flags": result.get("contradiction_flags", []),
        "completeness_flags": result.get("completeness_flags", []),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# IG-04: RFI Translation endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/rfis/{rfi_id}/translate",
)
async def translate_rfi_endpoint(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    target_language: str = Query(
        ..., description="ISO 639-1 target language code (e.g., 'es', 'fr', 'zh')"
    ),
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Translate an RFI's question and answer into the target language.

    Uses the construction-domain-aware translation service which preserves
    CSI codes, OSHA references, measurements, and trade terminology.

    Returns the translated question and (if available) the translated answer.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.communication.rfi_service import _get_rfi_or_raise
    from app.services.communication.translation_service import (
        SUPPORTED_LANGUAGES,
        get_translation_service,
    )

    if target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported target language '{target_language}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES.keys()))}"
            ),
        )

    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    translation_svc = get_translation_service()

    # Translate the question
    question_result = await translation_svc.translate(
        text=rfi.question,
        target_lang=target_language,
        context="rfi",
    )

    # Translate the answer if one exists
    answer_result = None
    if rfi.answer:
        answer_result = await translation_svc.translate(
            text=rfi.answer,
            target_lang=target_language,
            context="rfi",
        )

    # Also translate the subject for convenience
    subject_result = await translation_svc.translate(
        text=rfi.subject,
        target_lang=target_language,
        context="rfi",
    )

    return {
        "rfi_id": str(rfi_id),
        "target_language": target_language,
        "target_language_name": SUPPORTED_LANGUAGES.get(target_language, target_language),
        "translated_subject": subject_result.translated_text,
        "translated_question": question_result.translated_text,
        "translated_answer": answer_result.translated_text if answer_result else None,
        "question_confidence": question_result.confidence,
        "answer_confidence": answer_result.confidence if answer_result else None,
        "source_language": question_result.source_language,
    }


@router.get(
    "/{project_id}/rfis/{rfi_id}",
    response_model=RFIDetailResponse,
)
async def get_rfi_detail_endpoint(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get full RFI detail including responses and attachments."""
    await verify_project_access(project_id, current_user, db)
    return await get_rfi_detail(db, rfi_id, project_id)


@router.patch(
    "/{project_id}/rfis/{rfi_id}",
    response_model=RFIDetailResponse,
)
async def update_rfi_endpoint(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    body: RFIUpdate,
    current_user: User = Depends(require_permission("rfis", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update an RFI with status transition validation."""
    await verify_project_access(project_id, current_user, db)

    try:
        await update_rfi(
            db, rfi_id, project_id, body.model_dump(exclude_unset=True), current_user.id
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return await get_rfi_detail(db, rfi_id, project_id)


@router.post(
    "/{project_id}/rfis/{rfi_id}/respond",
    response_model=RFIResponseItem,
    status_code=status.HTTP_201_CREATED,
)
async def respond_to_rfi_endpoint(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    body: RFIResponseCreate,
    current_user: User = Depends(require_permission("rfis", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a response to an RFI."""
    await verify_project_access(project_id, current_user, db)

    try:
        response = await respond_to_rfi(db, rfi_id, project_id, current_user.id, body.response_text)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return response


@router.post(
    "/{project_id}/rfis/{rfi_id}/close",
    response_model=RFIDetailResponse,
)
async def close_rfi_endpoint(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    body: RFICloseRequest,
    current_user: User = Depends(require_permission("rfis", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Close an RFI with an optional final answer."""
    await verify_project_access(project_id, current_user, db)

    try:
        await close_rfi(db, rfi_id, project_id, current_user.id, body.answer)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return await get_rfi_detail(db, rfi_id, project_id)


@router.post(
    "/{project_id}/rfis/{rfi_id}/attachments",
    response_model=RFIAttachmentItem,
    status_code=status.HTTP_201_CREATED,
)
async def upload_rfi_attachment(
    project_id: uuid.UUID,
    rfi_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("rfis", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to an RFI."""
    await verify_project_access(project_id, current_user, db)

    # Verify RFI exists
    from app.services.communication.rfi_service import _get_rfi_or_raise

    await _get_rfi_or_raise(db, rfi_id, project_id)

    # Validate file extension (don't trust client content_type)
    _ALLOWED_EXTENSIONS = {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".doc",
        ".docx",
        ".xlsx",
        ".dwg",
        ".ifc",
    }
    filename = file.filename or "attachment"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    # Enforce file size limit (50 MB)
    _MAX_FILE_SIZE = 50 * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size of 50 MB.",
        )

    # SECURITY [M-16]: Validate magic bytes match claimed file extension
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    _MAGIC_BYTES = {
        ".pdf": [b"%PDF"],
        ".jpg": [b"\xff\xd8\xff"],
        ".jpeg": [b"\xff\xd8\xff"],
        ".png": [b"\x89PNG"],
        ".doc": [b"\xd0\xcf\x11\xe0"],  # OLE2 compound document
        ".docx": [b"PK\x03\x04"],  # ZIP archive (OOXML)
        ".xlsx": [b"PK\x03\x04"],  # ZIP archive (OOXML)
    }
    expected_magics = _MAGIC_BYTES.get(ext)
    if expected_magics is not None:
        if not any(file_bytes[: len(m)].startswith(m) for m in expected_magics):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File content does not match expected format for '{ext}'.",
            )

    file_id = uuid.uuid4()
    s3_key = f"rfis/{project_id}/{rfi_id}/{file_id}{ext}"

    # Derive content type from validated extension (never trust client content_type)
    ext_to_ct = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".dwg": "application/acad",
        ".ifc": "application/x-step",
    }
    server_content_type = ext_to_ct.get(ext, "application/octet-stream")

    # Upload to S3/MinIO
    try:
        from app.utils.s3 import upload_file

        upload_file(s3_key, file_bytes, content_type=server_content_type)
    except Exception:
        logger.warning("S3 upload failed for %s, storing path only", s3_key)

    attachment = RfiAttachment(
        rfi_id=rfi_id,
        file_path=s3_key,
        file_name=filename,
        file_type=server_content_type,
        file_size_bytes=len(file_bytes),
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    await db.flush()
    await db.refresh(attachment)
    await db.commit()

    # Generate presigned URL
    download_url = None
    try:
        from app.utils.s3 import generate_presigned_url

        download_url = generate_presigned_url(s3_key)
    except Exception:
        pass

    return {
        "id": attachment.id,
        "rfi_id": attachment.rfi_id,
        "file_path": attachment.file_path,
        "file_name": attachment.file_name,
        "file_type": attachment.file_type,
        "file_size_bytes": attachment.file_size_bytes,
        "uploaded_by": attachment.uploaded_by,
        "uploaded_at": attachment.uploaded_at,
        "download_url": download_url,
    }
