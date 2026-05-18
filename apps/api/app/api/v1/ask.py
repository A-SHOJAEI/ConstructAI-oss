"""Ask ConstructAI — natural language interface API endpoints.

All routes are project-scoped: ``/projects/{project_id}/ask/...``
"""

from __future__ import annotations

import collections
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.ask import AskRequest, AskResponse, CitationSchema, SuggestionsResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory rate limiter: 30 questions per user per hour
# ---------------------------------------------------------------------------

_RATE_LIMIT_MAX = 30
_RATE_LIMIT_WINDOW_SECONDS = 3600
_RATE_LIMIT_MAX_USERS = 10_000  # cap tracked users to prevent memory bloat

# {user_id_str: [timestamp, ...]}
_rate_limit_log: dict[str, list[float]] = collections.defaultdict(list)
_rate_limit_last_cleanup: float = 0.0


def _check_rate_limit(user_id: uuid.UUID) -> None:
    """Enforce per-user rate limit. Raises 429 if exceeded.

    Tracks up to 30 requests per user per hour. Returns 429 if exceeded.
    Periodically prunes stale user entries to cap memory usage.
    """
    global _rate_limit_last_cleanup

    uid = str(user_id)
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS

    # Prune old entries for this user
    _rate_limit_log[uid] = [ts for ts in _rate_limit_log[uid] if ts > cutoff]

    # Periodic global cleanup: evict users with no recent activity
    # Run at most once per minute to avoid overhead
    if now - _rate_limit_last_cleanup > 60:
        stale_users = [
            u
            for u, timestamps in _rate_limit_log.items()
            if not timestamps or max(timestamps) < cutoff
        ]
        for u in stale_users:
            del _rate_limit_log[u]
        _rate_limit_last_cleanup = now

        # Hard cap: if still too many users, evict oldest
        if len(_rate_limit_log) > _RATE_LIMIT_MAX_USERS:
            sorted_users = sorted(
                _rate_limit_log.items(),
                key=lambda item: max(item[1]) if item[1] else 0,
            )
            to_evict = len(_rate_limit_log) - _RATE_LIMIT_MAX_USERS
            for u, _ in sorted_users[:to_evict]:
                del _rate_limit_log[u]

    if len(_rate_limit_log[uid]) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {_RATE_LIMIT_MAX} questions per hour. "
                "Please wait before asking another question."
            ),
        )

    _rate_limit_log[uid].append(now)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ask",
    response_model=AskResponse,
)
async def ask_question(
    project_id: uuid.UUID,
    body: AskRequest,
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Ask ConstructAI a natural language question about a project.

    The system classifies the question, gathers relevant data from the
    project database, and returns an AI-generated answer with citations.
    """
    await verify_project_access(project_id, current_user, db)

    _check_rate_limit(current_user.id)

    from app.services.intelligence.ask_service import AskService

    service = AskService()
    result = await service.ask(
        question=body.question,
        project_id=project_id,
        org_id=str(current_user.org_id),
        db=db,
        conversation_id=body.conversation_id,
    )

    return AskResponse(
        answer=result.answer,
        intent=result.intent,
        confidence=result.confidence,
        citations=[
            CitationSchema(
                source=c.source,
                page=c.page,
                section=c.section,
                excerpt=c.excerpt,
            )
            for c in result.citations
        ],
        data_sources=result.data_sources,
        follow_up_suggestions=result.follow_up_suggestions,
        processing_time_ms=result.processing_time_ms,
    )


@router.get(
    "/{project_id}/ask/suggestions",
    response_model=SuggestionsResponse,
)
async def get_suggestions(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Return starter question suggestions based on available project data.

    Checks which data types exist for the project (EVM, RFIs, schedule,
    inspections, etc.) and suggests relevant questions.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.ask_service import get_project_suggestions

    suggestions = await get_project_suggestions(project_id, db)
    return SuggestionsResponse(suggestions=suggestions)
