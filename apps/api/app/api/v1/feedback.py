"""User feedback API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.schemas.feedback import FeedbackCreateRequest

router = APIRouter()

_NOT_IMPLEMENTED_DETAIL = "Feedback persistence not yet implemented."


@router.post("/", status_code=501)
async def submit_feedback(
    body: FeedbackCreateRequest,
    user: Annotated[User, Depends(require_permission("reports", "create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit user feedback (thumbs up/down + optional text)."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.get("/", status_code=501)
async def list_feedback(
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_name: str | None = Query(default=None),
):
    """List feedback with optional agent_name filter."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.get("/summary", status_code=501)
async def get_feedback_summary(
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get aggregated feedback statistics per agent."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)
