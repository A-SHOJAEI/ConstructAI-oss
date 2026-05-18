"""Executive portfolio dashboard API endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

router = APIRouter()

_NOT_IMPLEMENTED_DETAIL = "Portfolio analytics not yet implemented."


def _raise_not_implemented():
    """Raise 501 for unimplemented endpoints."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.get("")
async def get_portfolio(
    _user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get all projects with health indicators."""
    _raise_not_implemented()


@router.get("/benchmarks")
async def get_benchmarks(
    _user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get cross-project benchmarking data."""
    _raise_not_implemented()


@router.get("/map")
async def get_portfolio_map(
    _user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get projects with geo coordinates for map view."""
    _raise_not_implemented()


@router.get("/{project_id}/health")
async def get_project_health(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get detailed health for a single project."""
    await verify_project_access(project_id, user, db)
    _raise_not_implemented()
