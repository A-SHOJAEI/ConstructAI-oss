"""Orchestrator and workflow schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class WorkflowRunRequest(BaseModel):
    workflow_type: str = Field(
        ...,
        pattern=r"^(new_project_onboarding|change_order_processing"
        r"|safety_incident_response|custom)$",
    )
    project_id: uuid.UUID
    input_data: dict = Field(default_factory=dict)
    priority: int = Field(default=3, ge=1, le=5)
    idempotency_key: str | None = None


class WorkflowStepInfo(BaseModel):
    step_name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowRunResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    workflow_type: str
    project_id: uuid.UUID
    status: str
    current_step: str | None = None
    steps_completed: list[dict] = Field(default_factory=list)
    input_data: dict = Field(default_factory=dict)
    output_data: dict = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    langgraph_thread_id: str | None = None


class WorkflowListResponse(BaseModel):
    items: list[WorkflowRunResponse]
    pagination: PaginationMeta | None = None


class EventRouteRequest(BaseModel):
    event_type: str
    project_id: uuid.UUID
    source_agent: str
    priority: int = Field(default=3, ge=1, le=5)
    data: dict = Field(default_factory=dict)
    correlation_id: str | None = None
    idempotency_key: str | None = None


class EventRouteResponse(BaseModel):
    workflow_execution_id: uuid.UUID | None = None
    routed_to: str
    priority: int
