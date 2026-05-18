"""Submittal workflow API endpoints.

All routes are project-scoped: ``/projects/{project_id}/submittals/...``
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
from app.models.communication import SubmittalAttachment
from app.models.user import User
from app.schemas.communication import (
    SubmittalAttachmentItem,
    SubmittalCreateV2,
    SubmittalDetailListResponse,
    SubmittalDetailResponse,
    SubmittalRegisterResponse,
    SubmittalResubmitRequest,
    SubmittalReviewCreate,
    SubmittalReviewItem,
    SubmittalStatsResponse,
    SubmittalUpdate,
)
from app.services.communication.submittal_service import (
    create_submittal,
    export_submittals_csv,
    get_submittal_detail,
    get_submittal_register,
    get_submittal_stats,
    list_submittals,
    resubmit_submittal,
    review_submittal,
    update_submittal,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Submittal attachment validation constants
SUBMITTAL_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".doc",
    ".docx",
    ".xlsx",
    ".dwg",
    ".dxf",
    ".ifc",
    ".tiff",
    ".tif",
}
SUBMITTAL_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# IMPORTANT: /register, /export, /stats MUST come before /{submittal_id}
# so FastAPI doesn't try to parse those words as a UUID path parameter.
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/submittals",
    response_model=SubmittalDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submittal_endpoint(
    project_id: uuid.UUID,
    body: SubmittalCreateV2,
    current_user: User = Depends(require_permission("submittals", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new submittal with auto-generated number."""
    await verify_project_access(project_id, current_user, db)

    try:
        submittal = await create_submittal(
            db, project_id, body.model_dump(exclude_unset=True), current_user.id
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    detail = await get_submittal_detail(db, submittal.id, project_id)
    return detail


@router.get(
    "/{project_id}/submittals",
    response_model=SubmittalDetailListResponse,
)
async def list_submittals_endpoint(
    project_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    priority: str | None = Query(None),
    submittal_type: str | None = Query(None, alias="type"),
    spec_section: str | None = Query(None),
    ball_in_court: uuid.UUID | None = Query(None),
    overdue: bool = Query(False),
    search: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List submittals for a project with optional filters."""
    await verify_project_access(project_id, current_user, db)

    result = await list_submittals(
        db,
        project_id,
        status_filter=status_filter,
        priority_filter=priority,
        type_filter=submittal_type,
        spec_section_filter=spec_section,
        ball_in_court_filter=ball_in_court,
        overdue_only=overdue,
        search=search,
        cursor=cursor,
        limit=limit,
    )
    return result


@router.get(
    "/{project_id}/submittals/register",
    response_model=SubmittalRegisterResponse,
)
async def get_submittal_register_endpoint(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the submittal register — spec section x status matrix."""
    await verify_project_access(project_id, current_user, db)
    entries = await get_submittal_register(db, project_id)
    return {"data": entries}


@router.get(
    "/{project_id}/submittals/export",
)
async def export_submittals_endpoint(
    project_id: uuid.UUID,
    limit: int = Query(500, ge=1, le=5000, description="Max submittals to export"),
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export project submittals as a CSV file (bounded by limit)."""
    await verify_project_access(project_id, current_user, db)

    csv_bytes = await export_submittals_csv(db, project_id, limit=limit)

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=submittals_export.csv"},
    )


@router.get(
    "/{project_id}/submittals/stats",
    response_model=SubmittalStatsResponse,
)
async def get_submittal_stats_endpoint(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregate submittal statistics for a project."""
    await verify_project_access(project_id, current_user, db)
    return await get_submittal_stats(db, project_id)


@router.get(
    "/{project_id}/submittals/{submittal_id}",
    response_model=SubmittalDetailResponse,
)
async def get_submittal_detail_endpoint(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get full submittal detail including reviews and attachments."""
    await verify_project_access(project_id, current_user, db)
    return await get_submittal_detail(db, submittal_id, project_id)


@router.patch(
    "/{project_id}/submittals/{submittal_id}",
    response_model=SubmittalDetailResponse,
)
async def update_submittal_endpoint(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    body: SubmittalUpdate,
    current_user: User = Depends(require_permission("submittals", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a submittal with status transition validation."""
    await verify_project_access(project_id, current_user, db)

    try:
        await update_submittal(
            db, submittal_id, project_id, body.model_dump(exclude_unset=True), current_user.id
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return await get_submittal_detail(db, submittal_id, project_id)


@router.post(
    "/{project_id}/submittals/{submittal_id}/review",
    response_model=SubmittalReviewItem,
    status_code=status.HTTP_201_CREATED,
)
async def review_submittal_endpoint(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    body: SubmittalReviewCreate,
    current_user: User = Depends(require_permission("submittals", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a review action on a submittal."""
    await verify_project_access(project_id, current_user, db)

    try:
        review = await review_submittal(
            db, submittal_id, project_id, current_user.id, body.review_action, body.comments
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return review


@router.post(
    "/{project_id}/submittals/{submittal_id}/ai-review",
)
async def ai_review_submittal_endpoint(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    current_user: User = Depends(require_permission("submittals", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Run an AI-assisted compliance review against project specs.

    The result is informational only — no review row is created. A human
    reviewer must still call ``/review`` to make the formal disposition.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.communication.submittal_ai_review import review_submittal_ai
    from app.services.communication.submittal_service import _get_submittal_or_raise

    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)
    return await review_submittal_ai(db, submittal, project_id)


@router.post(
    "/{project_id}/submittals/{submittal_id}/resubmit",
    response_model=SubmittalDetailResponse,
)
async def resubmit_submittal_endpoint(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    body: SubmittalResubmitRequest,
    current_user: User = Depends(require_permission("submittals", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Resubmit a submittal with a new revision."""
    await verify_project_access(project_id, current_user, db)

    try:
        await resubmit_submittal(db, submittal_id, project_id, current_user.id, body.notes)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return await get_submittal_detail(db, submittal_id, project_id)


@router.post(
    "/{project_id}/submittals/{submittal_id}/attachments",
    response_model=SubmittalAttachmentItem,
    status_code=status.HTTP_201_CREATED,
)
async def upload_submittal_attachment(
    project_id: uuid.UUID,
    submittal_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("submittals", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to a submittal."""
    await verify_project_access(project_id, current_user, db)

    # Verify submittal exists
    from app.services.communication.submittal_service import _get_submittal_or_raise

    await _get_submittal_or_raise(db, submittal_id, project_id)

    # Validate file extension
    filename = file.filename or "attachment"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUBMITTAL_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(SUBMITTAL_ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' not allowed. Accepted: {allowed}",
        )

    # Enforce file size limit (50 MB)
    file_bytes = await file.read()
    if len(file_bytes) > SUBMITTAL_MAX_FILE_SIZE:
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
    s3_key = f"submittals/{project_id}/{submittal_id}/{file_id}{ext}"

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
        ".dxf": "application/dxf",
        ".ifc": "application/x-step",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }
    server_content_type = ext_to_ct.get(ext, "application/octet-stream")

    # Upload to S3/MinIO with Content-Disposition to prevent inline rendering
    try:
        from app.utils.s3 import upload_file

        upload_file(
            s3_key,
            file_bytes,
            content_type=server_content_type,
        )
    except Exception:
        logger.warning("S3 upload failed for %s, storing path only", s3_key)

    attachment = SubmittalAttachment(
        submittal_id=submittal_id,
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
        "submittal_id": attachment.submittal_id,
        "file_path": attachment.file_path,
        "file_name": attachment.file_name,
        "file_type": attachment.file_type,
        "file_size_bytes": attachment.file_size_bytes,
        "uploaded_by": attachment.uploaded_by,
        "uploaded_at": attachment.uploaded_at,
        "download_url": download_url,
    }
