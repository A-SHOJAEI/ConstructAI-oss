"""Drawing management service: sets, revisions, markups, linking."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import UTC
from typing import Any

from fastapi import UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.communication import RFI, Submittal
from app.models.drawing import (
    Drawing,
    DrawingMarkup,
    DrawingPunchListLink,
    DrawingRevision,
    DrawingRfiLink,
    DrawingSet,
    DrawingSubmittalLink,
)
from app.models.field_management import PunchListItem
from app.utils.s3 import generate_presigned_url, upload_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_DRAWING_EXTENSIONS: set[str] = {".pdf", ".dwg", ".dxf"}

# Regex: discipline letter + optional separator + number (e.g., A-101, S200, M_301, E-1.1)
_SHEET_RE = re.compile(r"^([A-Z])[_\-]?(\d{1,4}(?:\.\d{1,2})?)", re.IGNORECASE)

DISCIPLINE_MAP: dict[str, str] = {
    "A": "architectural",
    "S": "structural",
    "M": "mechanical",
    "E": "electrical",
    "P": "plumbing",
    "C": "civil",
    "L": "landscape",
    "G": "general",
    "F": "fire_protection",
    "T": "telecom",
}

CONTENT_TYPE_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".dwg": "application/acad",
    ".dxf": "application/dxf",
}

MAX_DRAWING_FILE_SIZE_BYTES: int = 500 * 1024 * 1024  # 500 MB


# ---------------------------------------------------------------------------
# Filename Parsing
# ---------------------------------------------------------------------------


def parse_sheet_number(filename: str) -> str | None:
    """Extract and normalize sheet number from a filename.

    Examples:
        "A-101 Floor Plan.pdf" -> "A-101"
        "S200_Foundation.dwg" -> "S-200"
        "M_301 HVAC Plan.dxf" -> "M-301"
        "notes.pdf" -> None
    """
    base = os.path.splitext(filename)[0]
    # Strip leading/trailing whitespace and underscores
    base = base.strip().strip("_")
    match = _SHEET_RE.match(base)
    if not match:
        return None
    letter = match.group(1).upper()
    number = match.group(2)
    return f"{letter}-{number}"


def infer_discipline(sheet_number: str) -> str:
    """Map the sheet prefix letter to a discipline name."""
    if not sheet_number:
        return "general"
    letter = sheet_number[0].upper()
    return DISCIPLINE_MAP.get(letter, "general")


# ---------------------------------------------------------------------------
# Drawing Set CRUD
# ---------------------------------------------------------------------------


async def create_drawing_set(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID,
) -> DrawingSet:
    drawing_set = DrawingSet(
        project_id=project_id,
        name=data["name"],
        discipline=data["discipline"],
        description=data.get("description"),
        created_by=created_by,
    )
    db.add(drawing_set)
    await db.flush()
    await db.refresh(drawing_set)
    return drawing_set


async def list_drawing_sets(
    db: AsyncSession,
    project_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    query = (
        select(DrawingSet)
        .where(DrawingSet.project_id == project_id)
        .order_by(DrawingSet.created_at.desc())
        .limit(limit + 1)
    )
    if cursor:
        from datetime import datetime

        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            raise ValueError("Invalid cursor format: expected ISO 8601 datetime")
        # Ensure timezone-aware for comparison with DB timestamps
        if cursor_dt.tzinfo is None:
            cursor_dt = cursor_dt.replace(tzinfo=UTC)
        query = query.where(DrawingSet.created_at < cursor_dt)

    result = await db.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Attach drawing counts in a single query (avoid N+1)
    set_ids = [ds.id for ds in rows]
    counts_map: dict[uuid.UUID, int] = {}
    if set_ids:
        count_q = (
            select(Drawing.drawing_set_id, func.count(Drawing.id))
            .where(Drawing.drawing_set_id.in_(set_ids))
            .group_by(Drawing.drawing_set_id)
        )
        count_result = await db.execute(count_q)
        for set_id, count in count_result.all():
            counts_map[set_id] = count

    sets_with_counts = [{"set": ds, "drawing_count": counts_map.get(ds.id, 0)} for ds in rows]

    return {
        "data": sets_with_counts,
        "has_more": has_more,
        "next_cursor": rows[-1].created_at.isoformat() if rows and has_more else None,
    }


async def get_drawing_set_detail(
    db: AsyncSession,
    set_id: uuid.UUID,
    project_id: uuid.UUID,
) -> DrawingSet | None:
    query = (
        select(DrawingSet)
        .options(selectinload(DrawingSet.drawings).selectinload(Drawing.current_revision))
        .where(DrawingSet.id == set_id, DrawingSet.project_id == project_id)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_drawing_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
) -> DrawingSet | None:
    ds = await get_drawing_set_detail(db, set_id, project_id)
    if not ds:
        return None
    for key, value in data.items():
        if value is not None and hasattr(ds, key):
            setattr(ds, key, value)
    await db.flush()
    await db.refresh(ds)
    return ds


async def delete_drawing_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    project_id: uuid.UUID,
) -> bool:
    ds = await get_drawing_set_detail(db, set_id, project_id)
    if not ds:
        return False
    await db.delete(ds)
    await db.flush()
    return True


async def delete_drawing(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    project_id: uuid.UUID,
) -> bool:
    """Delete a drawing and all its revisions and markups."""
    drawing = await get_drawing_detail(db, drawing_id, project_id)
    if not drawing:
        return False
    await db.delete(drawing)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Bulk Upload
# ---------------------------------------------------------------------------


async def bulk_upload_drawings(
    db: AsyncSession,
    project_id: uuid.UUID,
    drawing_set_id: uuid.UUID,
    files: list[UploadFile],
    uploaded_by: uuid.UUID,
) -> dict:
    """Upload multiple drawing files. Parse sheet numbers, create/update drawings."""
    uploaded: list[dict] = []
    errors: list[dict] = []

    for file in files:
        filename = file.filename or "unknown"
        ext = os.path.splitext(filename)[1].lower()

        # Validate extension
        if ext not in ALLOWED_DRAWING_EXTENSIONS:
            errors.append({"filename": filename, "error": f"Invalid extension {ext}"})
            continue

        try:
            file_bytes = await file.read()
            file_size = len(file_bytes)

            # Server-side file size validation (never trust client-provided size)
            if file_size > MAX_DRAWING_FILE_SIZE_BYTES:
                errors.append(
                    {
                        "filename": filename,
                        "error": f"File exceeds max size of "
                        f"{MAX_DRAWING_FILE_SIZE_BYTES // (1024 * 1024)} MB",
                    }
                )
                continue

            content_hash = hashlib.sha256(file_bytes).hexdigest()

            # Parse sheet number
            sheet_number = parse_sheet_number(filename)
            warnings: list[str] = []
            if not sheet_number:
                # Use filename without extension as title fallback
                sheet_number = os.path.splitext(filename)[0][:20]
                warnings.append(f"Could not parse sheet number from '{filename}', using filename")

            discipline = infer_discipline(sheet_number)
            title = os.path.splitext(filename)[0]

            # Check if drawing with this sheet number already exists in project
            existing_q = select(Drawing).where(
                Drawing.project_id == project_id,
                Drawing.sheet_number == sheet_number,
            )
            existing_result = await db.execute(existing_q)
            existing_drawing = existing_result.scalar_one_or_none()

            if existing_drawing:
                # Add new revision to existing drawing
                drawing = existing_drawing
                warnings.append(f"Drawing {sheet_number} already exists, adding new revision")
            else:
                # Create new drawing
                drawing = Drawing(
                    drawing_set_id=drawing_set_id,
                    project_id=project_id,
                    sheet_number=sheet_number,
                    title=title,
                    discipline=discipline,
                )
                db.add(drawing)
                await db.flush()
                await db.refresh(drawing)

            # Determine next revision number
            max_rev_q = select(func.max(DrawingRevision.revision_number)).where(
                DrawingRevision.drawing_id == drawing.id
            )
            max_rev_result = await db.execute(max_rev_q)
            max_rev = max_rev_result.scalar() or 0
            new_rev_number = max_rev + 1

            # Supersede previous current revision
            if max_rev > 0:
                await _supersede_current_revisions(db, drawing.id)

            # Upload to S3
            s3_key = (
                f"drawings/{project_id}/{drawing_set_id}/{drawing.id}/rev_{new_rev_number}{ext}"
            )
            content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
            upload_file(s3_key, file_bytes, content_type)

            # Create revision record
            revision = DrawingRevision(
                drawing_id=drawing.id,
                revision_number=new_rev_number,
                s3_key=s3_key,
                original_filename=filename,
                file_size_bytes=file_size,
                content_hash=content_hash,
                status="current",
                uploaded_by=uploaded_by,
            )
            db.add(revision)
            await db.flush()
            await db.refresh(revision)

            # Update drawing's current revision
            drawing.current_revision_id = revision.id
            await db.flush()

            uploaded.append(
                {
                    "drawing": drawing,
                    "revision": revision,
                    "warnings": warnings,
                }
            )

        except Exception as exc:
            logger.error("Failed to upload %s: %s", filename, exc)
            errors.append({"filename": filename, "error": str(exc)})

    return {
        "uploaded": uploaded,
        "errors": errors,
        "total_files": len(files),
        "successful": len(uploaded),
        "failed": len(errors),
    }


# ---------------------------------------------------------------------------
# Revision Management
# ---------------------------------------------------------------------------


async def _supersede_current_revisions(db: AsyncSession, drawing_id: uuid.UUID) -> None:
    """Set all current revisions for a drawing to superseded."""
    query = select(DrawingRevision).where(
        DrawingRevision.drawing_id == drawing_id,
        DrawingRevision.status == "current",
    )
    result = await db.execute(query)
    for rev in result.scalars().all():
        rev.status = "superseded"
    await db.flush()


async def upload_revision(
    db: AsyncSession,
    project_id: uuid.UUID,
    drawing_id: uuid.UUID,
    file: UploadFile,
    uploaded_by: uuid.UUID,
) -> DrawingRevision | None:
    """Upload a new revision for an existing drawing."""
    # Verify drawing exists
    drawing_q = select(Drawing).where(Drawing.id == drawing_id, Drawing.project_id == project_id)
    result = await db.execute(drawing_q)
    drawing = result.scalar_one_or_none()
    if not drawing:
        return None

    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()
    file_bytes = await file.read()

    # Server-side file size validation (never trust client-provided size)
    if len(file_bytes) > MAX_DRAWING_FILE_SIZE_BYTES:
        raise ValueError(
            f"File exceeds max size of {MAX_DRAWING_FILE_SIZE_BYTES // (1024 * 1024)} MB"
        )

    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Next revision number
    max_rev_q = select(func.max(DrawingRevision.revision_number)).where(
        DrawingRevision.drawing_id == drawing_id
    )
    max_rev_result = await db.execute(max_rev_q)
    max_rev = max_rev_result.scalar() or 0
    new_rev_number = max_rev + 1

    # Supersede previous
    await _supersede_current_revisions(db, drawing_id)

    # Upload to S3
    s3_key = (
        f"drawings/{project_id}/{drawing.drawing_set_id}/{drawing_id}/rev_{new_rev_number}{ext}"
    )
    content_type = CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
    upload_file(s3_key, file_bytes, content_type)

    # Create revision
    revision = DrawingRevision(
        drawing_id=drawing_id,
        revision_number=new_rev_number,
        s3_key=s3_key,
        original_filename=filename,
        file_size_bytes=len(file_bytes),
        content_hash=content_hash,
        status="current",
        uploaded_by=uploaded_by,
    )
    db.add(revision)
    await db.flush()
    await db.refresh(revision)

    # Update drawing's current revision
    drawing.current_revision_id = revision.id
    await db.flush()

    return revision


async def get_drawing_detail(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Drawing | None:
    query = (
        select(Drawing)
        .options(selectinload(Drawing.revisions), selectinload(Drawing.current_revision))
        .where(Drawing.id == drawing_id, Drawing.project_id == project_id)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def list_revisions(
    db: AsyncSession,
    drawing_id: uuid.UUID,
) -> list[DrawingRevision]:
    query = (
        select(DrawingRevision)
        .where(DrawingRevision.drawing_id == drawing_id)
        .order_by(DrawingRevision.revision_number.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_revision_download_url(
    db: AsyncSession,
    revision_id: uuid.UUID,
    project_id: uuid.UUID,
) -> str | None:
    # Join through Drawing to verify project ownership
    query = (
        select(DrawingRevision)
        .join(Drawing, DrawingRevision.drawing_id == Drawing.id)
        .where(DrawingRevision.id == revision_id, Drawing.project_id == project_id)
    )
    result = await db.execute(query)
    revision = result.scalar_one_or_none()
    if not revision:
        return None
    return generate_presigned_url(revision.s3_key)


async def get_comparison_urls(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    rev_a_id: uuid.UUID,
    rev_b_id: uuid.UUID,
) -> dict | None:
    """Return presigned URLs and metadata for two revisions for side-by-side comparison."""
    rev_a_q = select(DrawingRevision).where(
        DrawingRevision.id == rev_a_id, DrawingRevision.drawing_id == drawing_id
    )
    rev_b_q = select(DrawingRevision).where(
        DrawingRevision.id == rev_b_id, DrawingRevision.drawing_id == drawing_id
    )
    result_a = await db.execute(rev_a_q)
    result_b = await db.execute(rev_b_q)
    rev_a = result_a.scalar_one_or_none()
    rev_b = result_b.scalar_one_or_none()

    if not rev_a or not rev_b:
        return None

    return {
        "rev_a": rev_a,
        "rev_a_url": generate_presigned_url(rev_a.s3_key),
        "rev_b": rev_b,
        "rev_b_url": generate_presigned_url(rev_b.s3_key),
    }


# ---------------------------------------------------------------------------
# Markup CRUD
# ---------------------------------------------------------------------------


async def create_markup(
    db: AsyncSession,
    drawing_revision_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID,
) -> DrawingMarkup:
    markup = DrawingMarkup(
        drawing_revision_id=drawing_revision_id,
        markup_data=data["markup_data"],
        markup_type=data["markup_type"],
        layer=data.get("layer", "review"),
        label=data.get("label"),
        created_by=created_by,
    )
    db.add(markup)
    await db.flush()
    await db.refresh(markup)
    return markup


async def list_markups(
    db: AsyncSession,
    drawing_revision_id: uuid.UUID,
    layer: str | None = None,
) -> list[DrawingMarkup]:
    query = select(DrawingMarkup).where(DrawingMarkup.drawing_revision_id == drawing_revision_id)
    if layer:
        query = query.where(DrawingMarkup.layer == layer)
    query = query.order_by(DrawingMarkup.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_markup(
    db: AsyncSession,
    markup_id: uuid.UUID,
    data: dict[str, Any],
    project_id: uuid.UUID,
) -> DrawingMarkup | None:
    # Join through DrawingRevision -> Drawing to verify project ownership
    query = (
        select(DrawingMarkup)
        .join(DrawingRevision, DrawingMarkup.drawing_revision_id == DrawingRevision.id)
        .join(Drawing, DrawingRevision.drawing_id == Drawing.id)
        .where(DrawingMarkup.id == markup_id, Drawing.project_id == project_id)
    )
    result = await db.execute(query)
    markup = result.scalar_one_or_none()
    if not markup:
        return None
    for key, value in data.items():
        if value is not None and hasattr(markup, key):
            setattr(markup, key, value)
    await db.flush()
    await db.refresh(markup)
    return markup


async def delete_markup(
    db: AsyncSession,
    markup_id: uuid.UUID,
    project_id: uuid.UUID,
) -> bool:
    # Join through DrawingRevision -> Drawing to verify project ownership
    query = (
        select(DrawingMarkup)
        .join(DrawingRevision, DrawingMarkup.drawing_revision_id == DrawingRevision.id)
        .join(Drawing, DrawingRevision.drawing_id == Drawing.id)
        .where(DrawingMarkup.id == markup_id, Drawing.project_id == project_id)
    )
    result = await db.execute(query)
    markup = result.scalar_one_or_none()
    if not markup:
        return False
    await db.delete(markup)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Drawing Links
# ---------------------------------------------------------------------------

# Typed as Any because each registry entry is a different ORM class that
# exposes the same `id` / `project_id` / `drawing_id` columns at the
# SQLAlchemy level — mypy can't see those columns through a
# ``type[Base]`` union without a plugin.
_LINK_MODELS: dict[str, Any] = {
    "rfi": DrawingRfiLink,
    "submittal": DrawingSubmittalLink,
    "punch_list": DrawingPunchListLink,
}

_LINK_FK_FIELDS: dict[str, str] = {
    "rfi": "rfi_id",
    "submittal": "submittal_id",
    "punch_list": "punch_list_item_id",
}


_ENTITY_MODELS: dict[str, Any] = {
    "rfi": RFI,
    "submittal": Submittal,
    "punch_list": PunchListItem,
}


async def link_drawing(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    link_type: str,
    entity_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    model = _LINK_MODELS.get(link_type)
    fk_field = _LINK_FK_FIELDS.get(link_type)
    entity_model = _ENTITY_MODELS.get(link_type)
    if not model or not fk_field or not entity_model:
        return None

    # Verify drawing belongs to the project
    drawing_q = select(Drawing).where(Drawing.id == drawing_id, Drawing.project_id == project_id)
    drawing_result = await db.execute(drawing_q)
    if drawing_result.scalar_one_or_none() is None:
        return None

    # Verify linked entity belongs to the same project
    entity_q = select(entity_model).where(
        entity_model.id == entity_id, entity_model.project_id == project_id
    )
    entity_result = await db.execute(entity_q)
    if entity_result.scalar_one_or_none() is None:
        return None

    link = model(drawing_id=drawing_id, **{fk_field: entity_id})
    db.add(link)
    await db.flush()
    await db.refresh(link)
    return {"id": link.id, "drawing_id": drawing_id, "link_type": link_type, "entity_id": entity_id}


async def unlink_drawing(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    link_type: str,
    entity_id: uuid.UUID,
    project_id: uuid.UUID,
) -> bool:
    model = _LINK_MODELS.get(link_type)
    fk_field = _LINK_FK_FIELDS.get(link_type)
    entity_model = _ENTITY_MODELS.get(link_type)
    if not model or not fk_field or not entity_model:
        return False

    # Verify drawing belongs to the project
    drawing_q = select(Drawing).where(Drawing.id == drawing_id, Drawing.project_id == project_id)
    drawing_result = await db.execute(drawing_q)
    if drawing_result.scalar_one_or_none() is None:
        return False

    # Verify linked entity belongs to the same project
    entity_q = select(entity_model).where(
        entity_model.id == entity_id, entity_model.project_id == project_id
    )
    entity_result = await db.execute(entity_q)
    if entity_result.scalar_one_or_none() is None:
        return False

    query = delete(model).where(
        model.drawing_id == drawing_id,
        getattr(model, fk_field) == entity_id,
    )
    result = await db.execute(query)
    await db.flush()
    return result.rowcount > 0


async def get_drawing_links(
    db: AsyncSession,
    drawing_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Return all linked entities for a drawing, scoped to project."""
    # RFIs
    rfi_q = (
        select(RFI.id, RFI.rfi_number, RFI.subject)
        .join(DrawingRfiLink, DrawingRfiLink.rfi_id == RFI.id)
        .where(DrawingRfiLink.drawing_id == drawing_id, RFI.project_id == project_id)
    )
    rfi_result = await db.execute(rfi_q)
    rfis = [{"id": str(r.id), "rfi_number": r.rfi_number, "subject": r.subject} for r in rfi_result]

    # Submittals
    sub_q = (
        select(Submittal.id, Submittal.submittal_number, Submittal.title)
        .join(DrawingSubmittalLink, DrawingSubmittalLink.submittal_id == Submittal.id)
        .where(DrawingSubmittalLink.drawing_id == drawing_id, Submittal.project_id == project_id)
    )
    sub_result = await db.execute(sub_q)
    submittals = [
        {"id": str(r.id), "submittal_number": r.submittal_number, "title": r.title}
        for r in sub_result
    ]

    # Punch list items
    pl_q = (
        select(PunchListItem.id, PunchListItem.item_number, PunchListItem.description)
        .join(DrawingPunchListLink, DrawingPunchListLink.punch_list_item_id == PunchListItem.id)
        .where(
            DrawingPunchListLink.drawing_id == drawing_id,
            PunchListItem.project_id == project_id,
        )
    )
    pl_result = await db.execute(pl_q)
    punch_list_items = [
        {"id": str(r.id), "item_number": r.item_number, "description": r.description}
        for r in pl_result
    ]

    return {
        "rfis": rfis,
        "submittals": submittals,
        "punch_list_items": punch_list_items,
    }
