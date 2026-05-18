"""RFI Copilot product service.

Thin wrapper around the existing RFI service and RFI resolution agent,
exposing a product-oriented API for the frontend.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import RFI

logger = logging.getLogger(__name__)


async def get_rfi_analytics(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Return RFI analytics for the project dashboard widget.

    Returns counts by status, by priority, overdue count,
    and average response time in days.
    """
    # Count by status
    status_q = (
        select(RFI.status, func.count(RFI.id))
        .where(RFI.project_id == project_id)
        .group_by(RFI.status)
    )
    status_result = await db.execute(status_q)
    by_status = {row[0]: row[1] for row in status_result.all()}

    # Count by priority
    priority_q = (
        select(RFI.priority, func.count(RFI.id))
        .where(RFI.project_id == project_id)
        .group_by(RFI.priority)
    )
    priority_result = await db.execute(priority_q)
    by_priority = {row[0]: row[1] for row in priority_result.all()}

    # Overdue count
    now = datetime.now(UTC)
    overdue_q = select(func.count(RFI.id)).where(
        RFI.project_id == project_id,
        RFI.due_date < now,
        RFI.status.notin_(["closed", "responded", "void"]),
    )
    overdue_result = await db.execute(overdue_q)
    overdue_count = overdue_result.scalar() or 0

    total = sum(by_status.values())
    open_count = by_status.get("draft", 0) + by_status.get("submitted", 0)

    return {
        "total": total,
        "open_count": open_count,
        "overdue_count": overdue_count,
        "responded_count": by_status.get("responded", 0),
        "closed_count": by_status.get("closed", 0),
        "by_status": by_status,
        "by_priority": by_priority,
    }
