import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession


def encode_cursor(record) -> str:
    payload = {"id": str(record.id), "created_at": record.created_at.isoformat()}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())


async def paginate(db: AsyncSession, query, *, cursor: str | None, limit: int = 20, model):
    """Generic cursor-based paginator using keyset pagination."""
    limit = max(1, min(limit, 100))
    query = query.order_by(model.created_at.desc(), model.id.desc())
    if cursor:
        c = decode_cursor(cursor)
        cursor_ts = datetime.fromisoformat(c["created_at"])
        cursor_id = UUID(c["id"])
        query = query.where(
            or_(
                model.created_at < cursor_ts,
                and_(model.created_at == cursor_ts, model.id < cursor_id),
            )
        )
    results = (await db.execute(query.limit(limit + 1))).scalars().all()
    has_more = len(results) > limit
    items = list(results[:limit])
    return {
        "data": items,
        "meta": {
            "cursor": encode_cursor(items[-1]) if has_more and items else None,
            "has_more": has_more,
        },
    }
