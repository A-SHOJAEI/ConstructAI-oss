"""Orchestrator workflow API endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.orchestrator import (
    EventRouteRequest,
    EventRouteResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
)

router = APIRouter()


@router.post(
    "/workflows",
    response_model=WorkflowRunResponse,
    status_code=201,
)
async def start_workflow(
    body: WorkflowRunRequest,
    user: Annotated[User, Depends(require_permission("reports", "create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Start a new orchestrator workflow."""
    await verify_project_access(body.project_id, user, db)

    from app.services.orchestration.orchestrator_agent import (
        execute_workflow,
    )

    result = await execute_workflow(
        workflow_type=body.workflow_type,
        project_id=str(body.project_id),
        input_data=body.input_data,
        db=db,
    )

    return WorkflowRunResponse(
        id=uuid.uuid4(),
        workflow_type=body.workflow_type,
        project_id=body.project_id,
        status=result.get("status", "completed"),
        steps_completed=result.get("steps_completed", []),
        input_data=body.input_data,
        output_data=result,
        started_at=result.get(
            "started_at",
            "2026-01-01T00:00:00Z",
        ),
    )


@router.get("/workflows")
async def list_workflows(
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    """List workflow executions."""
    if project_id is not None:
        await verify_project_access(project_id, user, db)

    # In production, queries workflow_executions table
    return {"items": [], "pagination": None}


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get workflow execution by ID."""
    raise HTTPException(status_code=404, detail="Workflow execution not found")


@router.post(
    "/events/route",
    response_model=EventRouteResponse,
)
async def route_event(
    body: EventRouteRequest,
    user: Annotated[User, Depends(require_permission("reports", "create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Route a CloudEvent to the appropriate workflow."""
    await verify_project_access(body.project_id, user, db)

    from app.services.orchestration.event_router import (
        EventRouter,
    )

    router_svc = EventRouter()
    result = await router_svc.route_event(
        {
            "type": body.event_type,
            "ce-projectid": str(body.project_id),
            "ce-orgid": str(user.org_id),
            "ce-agentsource": body.source_agent,
            "ce-priority": body.priority,
            "data": body.data,
            "ce-correlationid": body.correlation_id,
        }
    )

    return EventRouteResponse(
        workflow_execution_id=(
            uuid.UUID(result["workflow_execution_id"])
            if result.get("workflow_execution_id")
            else None
        ),
        routed_to=result["routed_to"],
        priority=result["priority"],
    )
