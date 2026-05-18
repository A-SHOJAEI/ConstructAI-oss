"""Punch list field-data-capture service.

Provides: auto-numbering (PLI-NNN), create / update, bulk create / bulk
status update, stats, and CSV export grouped by responsible company.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import UTC, date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.field_management import PunchList, PunchListItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = {"open", "in_progress", "resolved", "verified"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

# ---------------------------------------------------------------------------
# Auto-numbering
# ---------------------------------------------------------------------------

_PLI_PATTERN = re.compile(r"PLI-(\d+)")


async def generate_item_number(db: AsyncSession, project_id: uuid.UUID) -> str:
    """Generate next PLI-NNN item number for the project."""
    result = await db.execute(
        select(PunchListItem.item_number).where(PunchListItem.project_id == project_id)
    )
    numbers = result.scalars().all()
    max_num = 0
    for num in numbers:
        m = _PLI_PATTERN.match(num or "")
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"PLI-{max_num + 1:03d}"


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


async def create_punch_list_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> PunchListItem:
    """Create a single punch list item with auto-numbering."""
    item_number = await generate_item_number(db, project_id)

    item = PunchListItem(
        project_id=project_id,
        item_number=item_number,
        description=data["description"],
        location=data.get("location"),
        category=data.get("category"),
        priority=data.get("priority", "medium"),
        status="open",
        assigned_to=data.get("assigned_to"),
        due_date=data.get("due_date"),
        photos=data.get("photos", []),
        notes=data.get("notes"),
        gps_lat=data.get("gps_lat"),
        gps_lon=data.get("gps_lon"),
        drawing_reference=data.get("drawing_reference"),
        company=data.get("company"),
        spec_section=data.get("spec_section"),
        punch_list_id=data.get("punch_list_id"),
        created_by=created_by,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


async def update_punch_list_item(
    db: AsyncSession,
    item_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
) -> PunchListItem:
    """Update an existing punch list item."""
    item = await _get_item(db, item_id, project_id)

    updatable = {
        "description",
        "location",
        "category",
        "priority",
        "status",
        "assigned_to",
        "due_date",
        "completed_date",
        "photos",
        "notes",
        "gps_lat",
        "gps_lon",
        "drawing_reference",
        "company",
        "spec_section",
        "punch_list_id",
        "verified_by",
    }

    for key, val in data.items():
        if key in updatable and val is not None:
            setattr(item, key, val)

    # Auto-set completed_date when status → verified/resolved
    if data.get("status") in ("resolved", "verified") and item.completed_date is None:
        item.completed_date = date.today()

    # Auto-set date_verified when status → verified
    if data.get("status") == "verified" and item.date_verified is None:
        from datetime import datetime

        item.date_verified = datetime.now(UTC)

    await db.flush()
    await db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


async def bulk_create(
    db: AsyncSession,
    project_id: uuid.UUID,
    items_data: list[dict[str, Any]],
    created_by: uuid.UUID | None = None,
) -> list[PunchListItem]:
    """Create multiple punch list items with sequential numbering."""
    created = []
    for data in items_data:
        item = await create_punch_list_item(db, project_id, data, created_by)
        created.append(item)
    return created


async def bulk_status_update(
    db: AsyncSession,
    project_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    new_status: str,
) -> list[PunchListItem]:
    """Update status of multiple punch list items at once."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    result = await db.execute(
        select(PunchListItem).where(
            PunchListItem.project_id == project_id,
            PunchListItem.id.in_(item_ids),
        )
    )
    items = list(result.scalars().all())

    for item in items:
        item.status = new_status
        if new_status in ("resolved", "verified") and item.completed_date is None:
            item.completed_date = date.today()

    await db.flush()
    for item in items:
        await db.refresh(item)
    return items


# ---------------------------------------------------------------------------
# List / detail / stats
# ---------------------------------------------------------------------------


async def get_punch_list_item_detail(
    db: AsyncSession,
    item_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    item = await _get_item(db, item_id, project_id)
    return _item_to_dict(item)


async def list_punch_list_items(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    priority: str | None = None,
    category: str | None = None,
    company: str | None = None,
    assigned_to: uuid.UUID | None = None,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List punch list items with filters."""
    query = (
        select(PunchListItem)
        .where(PunchListItem.project_id == project_id)
        .order_by(PunchListItem.created_at.desc())
    )
    if status:
        query = query.where(PunchListItem.status == status)
    if priority:
        query = query.where(PunchListItem.priority == priority)
    if category:
        query = query.where(PunchListItem.category == category)
    if company:
        query = query.where(PunchListItem.company == company)
    if assigned_to:
        query = query.where(PunchListItem.assigned_to == assigned_to)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            PunchListItem.description.ilike(pattern)
            | PunchListItem.item_number.ilike(pattern)
            | PunchListItem.location.ilike(pattern)
        )

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(PunchListItem, cursor_uuid)
            if cursor_obj:
                query = query.where(PunchListItem.created_at < cursor_obj.created_at)
        except ValueError:
            pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return {
        "data": [_item_to_dict(i) for i in items],
        "meta": {"cursor": next_cursor, "has_more": has_more},
    }


async def get_punch_list_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Get aggregated stats for punch list items."""
    result = await db.execute(select(PunchListItem).where(PunchListItem.project_id == project_id))
    items = list(result.scalars().all())

    status_counts = {"open": 0, "in_progress": 0, "resolved": 0, "verified": 0}
    priority_counts: dict[str, int] = {}
    company_counts: dict[str, int] = {}
    overdue = 0
    today = date.today()

    for item in items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1

        p = item.priority or "medium"
        priority_counts[p] = priority_counts.get(p, 0) + 1

        c = item.company or "Unassigned"
        company_counts[c] = company_counts.get(c, 0) + 1

        if item.due_date and item.due_date < today and item.status in ("open", "in_progress"):
            overdue += 1

    return {
        "total": len(items),
        "open": status_counts.get("open", 0),
        "in_progress": status_counts.get("in_progress", 0),
        "resolved": status_counts.get("resolved", 0),
        "verified": status_counts.get("verified", 0),
        "by_priority": priority_counts,
        "by_company": company_counts,
        "overdue": overdue,
    }


# ---------------------------------------------------------------------------
# CSV Export (grouped by company)
# ---------------------------------------------------------------------------


def export_punch_list_csv(items: list[PunchListItem]) -> bytes:
    """Export punch list items to CSV, sorted by company."""
    sorted_items = sorted(items, key=lambda i: (i.company or "ZZZ_Unassigned", i.item_number))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Item #",
            "Description",
            "Location",
            "Category",
            "Priority",
            "Status",
            "Company",
            "Assigned To",
            "Due Date",
            "Completed Date",
            "Drawing Ref",
            "GPS Lat",
            "GPS Lon",
            "Notes",
        ]
    )
    for item in sorted_items:
        writer.writerow(
            [
                item.item_number,
                item.description,
                item.location or "",
                item.category or "",
                item.priority,
                item.status,
                item.company or "",
                str(item.assigned_to) if item.assigned_to else "",
                item.due_date.isoformat() if item.due_date else "",
                item.completed_date.isoformat() if item.completed_date else "",
                item.drawing_reference or "",
                float(item.gps_lat) if item.gps_lat else "",
                float(item.gps_lon) if item.gps_lon else "",
                item.notes or "",
            ]
        )
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Punch List (walkthrough) CRUD
# ---------------------------------------------------------------------------

VALID_PUNCH_LIST_STATUSES = {"open", "closed"}


async def create_punch_list(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> PunchList:
    """Create a punch list (walkthrough grouping)."""
    pl = PunchList(
        project_id=project_id,
        name=data["name"],
        description=data.get("description"),
        walk_date=data.get("walk_date"),
        participants=data.get("participants", []),
        created_by=created_by,
    )
    db.add(pl)
    await db.flush()
    await db.refresh(pl)
    return pl


async def update_punch_list(
    db: AsyncSession,
    punch_list_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
) -> PunchList:
    """Update a punch list."""
    pl = await _get_punch_list(db, punch_list_id, project_id)
    updatable = {"name", "description", "walk_date", "status", "participants"}
    for key, val in data.items():
        if key in updatable and val is not None:
            setattr(pl, key, val)
    await db.flush()
    await db.refresh(pl)
    return pl


async def list_punch_lists(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List punch lists for a project."""
    query = (
        select(PunchList)
        .where(PunchList.project_id == project_id)
        .order_by(PunchList.created_at.desc())
    )
    if status:
        query = query.where(PunchList.status == status)
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(PunchList, cursor_uuid)
            if cursor_obj:
                query = query.where(PunchList.created_at < cursor_obj.created_at)
        except ValueError:
            pass
    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    data_list = []
    for pl in items:
        count_result = await db.execute(
            select(func.count())
            .select_from(PunchListItem)
            .where(PunchListItem.punch_list_id == pl.id)
        )
        item_count = count_result.scalar() or 0
        d = _punch_list_to_dict(pl)
        d["item_count"] = item_count
        data_list.append(d)

    return {
        "data": data_list,
        "meta": {"cursor": next_cursor, "has_more": has_more},
    }


async def get_punch_list_detail(
    db: AsyncSession,
    punch_list_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    pl = await _get_punch_list(db, punch_list_id, project_id)
    count_result = await db.execute(
        select(func.count()).select_from(PunchListItem).where(PunchListItem.punch_list_id == pl.id)
    )
    d = _punch_list_to_dict(pl)
    d["item_count"] = count_result.scalar() or 0
    return d


async def _get_punch_list(
    db: AsyncSession, punch_list_id: uuid.UUID, project_id: uuid.UUID
) -> PunchList:
    result = await db.execute(
        select(PunchList).where(
            PunchList.id == punch_list_id,
            PunchList.project_id == project_id,
        )
    )
    pl = result.scalars().first()
    if pl is None:
        raise ValueError("Punch list not found")
    return pl


def _punch_list_to_dict(pl: PunchList) -> dict:
    return {
        "id": str(pl.id),
        "project_id": str(pl.project_id),
        "name": pl.name,
        "description": pl.description,
        "walk_date": pl.walk_date.isoformat() if pl.walk_date else None,
        "status": pl.status,
        "participants": pl.participants,
        "created_by": str(pl.created_by) if pl.created_by else None,
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_item(db: AsyncSession, item_id: uuid.UUID, project_id: uuid.UUID) -> PunchListItem:
    result = await db.execute(
        select(PunchListItem).where(
            PunchListItem.id == item_id,
            PunchListItem.project_id == project_id,
        )
    )
    item = result.scalars().first()
    if item is None:
        raise ValueError("Punch list item not found")
    return item


def _item_to_dict(item: PunchListItem) -> dict:
    return {
        "id": str(item.id),
        "project_id": str(item.project_id),
        "punch_list_id": str(item.punch_list_id) if item.punch_list_id else None,
        "item_number": item.item_number,
        "description": item.description,
        "location": item.location,
        "category": item.category,
        "priority": item.priority,
        "status": item.status,
        "assigned_to": str(item.assigned_to) if item.assigned_to else None,
        "created_by": str(item.created_by) if item.created_by else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "completed_date": item.completed_date.isoformat() if item.completed_date else None,
        "photos": item.photos,
        "notes": item.notes,
        "gps_lat": float(item.gps_lat) if item.gps_lat else None,
        "gps_lon": float(item.gps_lon) if item.gps_lon else None,
        "drawing_reference": item.drawing_reference,
        "company": item.company,
        "spec_section": item.spec_section,
        "verified_by": str(item.verified_by) if item.verified_by else None,
        "date_verified": item.date_verified.isoformat() if item.date_verified else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
