"""Team supervisor API endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


class TeamRunRequest(BaseModel):
    project_id: uuid.UUID
    request: str = ""
    task_type: str = "full"


class TeamRunResponse(BaseModel):
    status: str
    project_id: uuid.UUID
    task_type: str
    results: dict


@router.post(
    "/execution/run",
    response_model=TeamRunResponse,
)
async def run_execution_team(
    request: TeamRunRequest,
    current_user: User = Depends(require_permission("members", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Run the Execution Team supervisor."""
    await verify_project_access(request.project_id, current_user, db)

    from app.services.agents.execution_team import (
        run_execution_team as _run,
    )

    result = await _run(
        project_id=str(request.project_id),
        request=request.request,
        task_type=request.task_type,
    )
    return TeamRunResponse(
        status=result.get("status", "unknown"),
        project_id=request.project_id,
        task_type=result.get(
            "task_type",
            request.task_type,
        ),
        results=result.get("final_report") or {},
    )


@router.post(
    "/compliance/run",
    response_model=TeamRunResponse,
)
async def run_compliance_team(
    request: TeamRunRequest,
    current_user: User = Depends(require_permission("members", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Run the Compliance Team supervisor."""
    await verify_project_access(request.project_id, current_user, db)

    from app.services.agents.compliance_team import (
        run_compliance_team as _run,
    )

    result = await _run(
        project_id=str(request.project_id),
        request=request.request,
        task_type=request.task_type,
    )
    return TeamRunResponse(
        status=result.get("status", "unknown"),
        project_id=request.project_id,
        task_type=result.get(
            "task_type",
            request.task_type,
        ),
        results=result.get("final_report") or {},
    )
