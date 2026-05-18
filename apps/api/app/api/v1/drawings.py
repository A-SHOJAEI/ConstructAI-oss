"""Drawing management API endpoints: sets, drawings, revisions, markups, links."""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.drawing import (
    BulkUploadResponse,
    BulkUploadResult,
    DrawingDetailResponse,
    DrawingLinkCreate,
    DrawingLinksResponse,
    DrawingMarkupCreate,
    DrawingMarkupResponse,
    DrawingMarkupUpdate,
    DrawingResponse,
    DrawingRevisionResponse,
    DrawingSetCreate,
    DrawingSetDetailResponse,
    DrawingSetListResponse,
    DrawingSetResponse,
    DrawingSetUpdate,
    RevisionComparisonResponse,
)
from app.schemas.pagination import PaginationMeta
from app.services.communication import drawing_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Drawing file validation constants
DRAWING_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".dwg",
    ".dxf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".tif",
    ".svg",
}
DRAWING_MAX_FILES = 50
MAX_DRAWING_FILE_SIZE = 200 * 1024 * 1024  # 200 MB
DRAWING_MAX_FILE_SIZE = MAX_DRAWING_FILE_SIZE  # backward compat alias

# Magic bytes for validating drawing file content matches extension
_DRAWING_MAGIC_BYTES = {
    b"%PDF": {".pdf"},
    b"\x89PNG": {".png"},
    b"\xff\xd8\xff": {".jpg", ".jpeg"},
    b"II\x2a\x00": {".tif", ".tiff"},  # TIFF little-endian
    b"MM\x00\x2a": {".tif", ".tiff"},  # TIFF big-endian
    b"AC10": {".dwg"},
}


def _validate_magic_bytes(file_bytes: bytes, ext: str) -> bool:
    """Check that file content magic bytes match the declared extension.

    Returns True if magic bytes match or if no magic-byte rule exists for this
    extension (e.g. .dxf, .svg are text-based formats).
    """
    for magic, allowed_exts in _DRAWING_MAGIC_BYTES.items():
        if ext in allowed_exts and file_bytes[: len(magic)] == magic:
            return True
    # If the extension is not covered by any magic byte rule, allow it
    has_rule = any(ext in exts for exts in _DRAWING_MAGIC_BYTES.values())
    return not has_rule


# ---------------------------------------------------------------------------
# Drawing Sets
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drawing-sets",
    response_model=DrawingSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_drawing_set(
    project_id: uuid.UUID,
    request: DrawingSetCreate,
    current_user: User = Depends(require_permission("drawings", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a drawing set for a project."""
    await verify_project_access(project_id, current_user, db)
    try:
        ds = await drawing_service.create_drawing_set(
            db, project_id, request.model_dump(), current_user.id
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Drawing set '{request.name}' already exists in this project",
        )
    return DrawingSetResponse(
        id=ds.id,
        project_id=ds.project_id,
        name=ds.name,
        discipline=ds.discipline,
        description=ds.description,
        drawing_count=0,
        created_by=ds.created_by,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


@router.get(
    "/{project_id}/drawing-sets",
    response_model=DrawingSetListResponse,
)
async def list_drawing_sets(
    project_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List drawing sets for a project."""
    await verify_project_access(project_id, current_user, db)
    result = await drawing_service.list_drawing_sets(db, project_id, cursor, limit)

    data = [
        DrawingSetResponse(
            id=item["set"].id,
            project_id=item["set"].project_id,
            name=item["set"].name,
            discipline=item["set"].discipline,
            description=item["set"].description,
            drawing_count=item["drawing_count"],
            created_by=item["set"].created_by,
            created_at=item["set"].created_at,
            updated_at=item["set"].updated_at,
        )
        for item in result["data"]
    ]
    return DrawingSetListResponse(
        data=data,
        meta=PaginationMeta(cursor=result["next_cursor"], has_more=result["has_more"]),
    )


@router.get(
    "/{project_id}/drawing-sets/{set_id}",
    response_model=DrawingSetDetailResponse,
)
async def get_drawing_set(
    project_id: uuid.UUID,
    set_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a drawing set with its drawings."""
    await verify_project_access(project_id, current_user, db)
    ds = await drawing_service.get_drawing_set_detail(db, set_id, project_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Drawing set not found")
    return ds


@router.patch(
    "/{project_id}/drawing-sets/{set_id}",
    response_model=DrawingSetResponse,
)
async def update_drawing_set(
    project_id: uuid.UUID,
    set_id: uuid.UUID,
    request: DrawingSetUpdate,
    current_user: User = Depends(require_permission("drawings", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a drawing set."""
    await verify_project_access(project_id, current_user, db)
    ds = await drawing_service.update_drawing_set(
        db, set_id, project_id, request.model_dump(exclude_unset=True)
    )
    if not ds:
        raise HTTPException(status_code=404, detail="Drawing set not found")
    return ds


@router.delete(
    "/{project_id}/drawing-sets/{set_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_drawing_set(
    project_id: uuid.UUID,
    set_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a drawing set and all its drawings."""
    await verify_project_access(project_id, current_user, db)
    deleted = await drawing_service.delete_drawing_set(db, set_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Drawing set not found")


# ---------------------------------------------------------------------------
# Bulk Upload
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drawing-sets/{set_id}/upload",
    response_model=BulkUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_upload_drawings(
    project_id: uuid.UUID,
    set_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_permission("drawings", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk upload drawing files to a set."""
    await verify_project_access(project_id, current_user, db)

    # Limit number of files per bulk upload
    if len(files) > DRAWING_MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {DRAWING_MAX_FILES} files per upload. Got {len(files)}.",
        )

    # Validate file extensions, actual sizes, and magic bytes
    for f in files:
        fname = f.filename or "unknown"
        ext = os.path.splitext(fname)[1].lower()
        if ext not in DRAWING_ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(DRAWING_ALLOWED_EXTENSIONS))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File '{fname}': type '{ext}' not allowed. Accepted: {allowed}",
            )
        # Read actual bytes to validate real size and magic bytes
        file_bytes = await f.read()
        await f.seek(0)  # reset for downstream consumption
        if len(file_bytes) > MAX_DRAWING_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{fname}' exceeds maximum allowed size of 200 MB.",
            )
        if not _validate_magic_bytes(file_bytes, ext):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File '{fname}': content does not match expected format for '{ext}'.",
            )

    # Verify drawing set exists
    ds = await drawing_service.get_drawing_set_detail(db, set_id, project_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Drawing set not found")

    result = await drawing_service.bulk_upload_drawings(
        db, project_id, set_id, files, current_user.id
    )

    uploaded = []
    for item in result["uploaded"]:
        uploaded.append(
            BulkUploadResult(
                drawing=DrawingResponse.model_validate(item["drawing"]),
                revision=DrawingRevisionResponse.model_validate(item["revision"]),
                warnings=item["warnings"],
            )
        )

    return BulkUploadResponse(
        uploaded=uploaded,
        errors=result["errors"],
        total_files=result["total_files"],
        successful=result["successful"],
        failed=result["failed"],
    )


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/drawings/{drawing_id}",
    response_model=DrawingDetailResponse,
)
async def get_drawing(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a drawing with all revisions."""
    await verify_project_access(project_id, current_user, db)
    drawing = await drawing_service.get_drawing_detail(db, drawing_id, project_id)
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    # Get links
    links_data = await drawing_service.get_drawing_links(db, drawing_id, drawing.project_id)

    return DrawingDetailResponse(
        id=drawing.id,
        drawing_set_id=drawing.drawing_set_id,
        project_id=drawing.project_id,
        sheet_number=drawing.sheet_number,
        title=drawing.title,
        discipline=drawing.discipline,
        status=drawing.status,
        current_revision=(
            DrawingRevisionResponse.model_validate(drawing.current_revision)
            if drawing.current_revision
            else None
        ),
        revisions=[DrawingRevisionResponse.model_validate(r) for r in drawing.revisions],
        links=DrawingLinksResponse(**links_data),
        created_at=drawing.created_at,
        updated_at=drawing.updated_at,
    )


@router.delete(
    "/{project_id}/drawings/{drawing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_drawing(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a drawing and all its revisions and markups."""
    await verify_project_access(project_id, current_user, db)
    deleted = await drawing_service.delete_drawing(db, drawing_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Drawing not found")


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drawings/{drawing_id}/revisions",
    response_model=DrawingRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_revision(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("drawings", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new revision for a drawing."""
    await verify_project_access(project_id, current_user, db)

    # Validate file extension
    fname = file.filename or "unknown"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in DRAWING_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(DRAWING_ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' not allowed. Accepted: {allowed}",
        )

    # Read actual bytes to validate real size and magic bytes
    file_bytes = await file.read()
    await file.seek(0)  # reset for downstream consumption
    if len(file_bytes) > MAX_DRAWING_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size of 200 MB.",
        )
    if not _validate_magic_bytes(file_bytes, ext):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File content does not match expected format for '{ext}'.",
        )

    revision = await drawing_service.upload_revision(
        db, project_id, drawing_id, file, current_user.id
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return revision


@router.get(
    "/{project_id}/drawings/{drawing_id}/revisions",
    response_model=list[DrawingRevisionResponse],
)
async def list_revisions(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all revisions for a drawing."""
    await verify_project_access(project_id, current_user, db)
    revisions = await drawing_service.list_revisions(db, drawing_id)
    return revisions[:limit]


@router.get(
    "/{project_id}/drawings/{drawing_id}/revisions/{rev_id}/download",
)
async def get_revision_download_url(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    rev_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a presigned download URL for a revision."""
    await verify_project_access(project_id, current_user, db)

    # Verify the revision belongs to the requested drawing/project
    from app.models.drawing import Drawing, DrawingRevision

    revision = await db.get(DrawingRevision, rev_id)
    if not revision or revision.drawing_id != drawing_id:
        raise HTTPException(status_code=404, detail="Revision not found")
    drawing = await db.get(Drawing, drawing_id)
    if not drawing or drawing.drawing_set_id is None:
        raise HTTPException(status_code=404, detail="Drawing not found")

    url = await drawing_service.get_revision_download_url(db, rev_id, drawing.project_id)
    if not url:
        raise HTTPException(status_code=404, detail="Revision not found")
    return {"download_url": url}


# ---------------------------------------------------------------------------
# Revision Comparison
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/drawings/{drawing_id}/compare",
    response_model=RevisionComparisonResponse,
)
async def compare_revisions(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    rev_a: uuid.UUID = Query(...),
    rev_b: uuid.UUID = Query(...),
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get presigned URLs for two revisions for side-by-side comparison."""
    await verify_project_access(project_id, current_user, db)
    result = await drawing_service.get_comparison_urls(db, drawing_id, rev_a, rev_b)
    if not result:
        raise HTTPException(status_code=404, detail="One or both revisions not found")

    rev_a_resp = DrawingRevisionResponse.model_validate(result["rev_a"])
    rev_a_resp.download_url = result["rev_a_url"]
    rev_b_resp = DrawingRevisionResponse.model_validate(result["rev_b"])
    rev_b_resp.download_url = result["rev_b_url"]

    return RevisionComparisonResponse(rev_a=rev_a_resp, rev_b=rev_b_resp)


# ---------------------------------------------------------------------------
# Markups
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
    response_model=DrawingMarkupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_markup(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    rev_id: uuid.UUID,
    request: DrawingMarkupCreate,
    current_user: User = Depends(require_permission("drawings", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a markup on a drawing revision."""
    await verify_project_access(project_id, current_user, db)
    markup = await drawing_service.create_markup(db, rev_id, request.model_dump(), current_user.id)
    return markup


@router.get(
    "/{project_id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
    response_model=list[DrawingMarkupResponse],
)
async def list_markups(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    rev_id: uuid.UUID,
    layer: str | None = Query(default=None),
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List markups for a revision, optionally filtered by layer."""
    await verify_project_access(project_id, current_user, db)
    return await drawing_service.list_markups(db, rev_id, layer)


@router.patch(
    "/{project_id}/markups/{markup_id}",
    response_model=DrawingMarkupResponse,
)
async def update_markup(
    project_id: uuid.UUID,
    markup_id: uuid.UUID,
    request: DrawingMarkupUpdate,
    current_user: User = Depends(require_permission("drawings", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a markup."""
    await verify_project_access(project_id, current_user, db)

    # Pass project_id to service so it verifies markup belongs to this project
    markup = await drawing_service.update_markup(
        db, markup_id, request.model_dump(exclude_unset=True), project_id
    )
    if not markup:
        raise HTTPException(status_code=404, detail="Markup not found")
    return markup


@router.delete(
    "/{project_id}/markups/{markup_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_markup(
    project_id: uuid.UUID,
    markup_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a markup."""
    await verify_project_access(project_id, current_user, db)

    # Pass project_id to service so it verifies markup belongs to this project
    deleted = await drawing_service.delete_markup(db, markup_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Markup not found")


# ---------------------------------------------------------------------------
# Drawing Links
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drawings/{drawing_id}/links",
    status_code=status.HTTP_201_CREATED,
)
async def link_drawing(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    request: DrawingLinkCreate,
    current_user: User = Depends(require_permission("drawings", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Link a drawing to an RFI, submittal, or punch list item."""
    await verify_project_access(project_id, current_user, db)
    if request.link_type not in ("rfi", "submittal", "punch_list"):
        raise HTTPException(status_code=422, detail="Invalid link_type")
    result = await drawing_service.link_drawing(
        db, drawing_id, request.link_type, request.entity_id, project_id
    )
    if not result:
        raise HTTPException(
            status_code=422,
            detail="Invalid link_type or entity not found in project",
        )
    return result


@router.get(
    "/{project_id}/drawings/{drawing_id}/links",
    response_model=DrawingLinksResponse,
)
async def get_drawing_links(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    current_user: User = Depends(require_permission("drawings", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all linked entities for a drawing."""
    await verify_project_access(project_id, current_user, db)
    links = await drawing_service.get_drawing_links(db, drawing_id, project_id)
    return DrawingLinksResponse(**links)


@router.delete(
    "/{project_id}/drawings/{drawing_id}/links/{link_type}/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_drawing(
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    link_type: str = Path(...),
    entity_id: uuid.UUID = Path(...),
    current_user: User = Depends(require_permission("drawings", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Remove a link between a drawing and an entity."""
    await verify_project_access(project_id, current_user, db)
    deleted = await drawing_service.unlink_drawing(db, drawing_id, link_type, entity_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Link not found")
