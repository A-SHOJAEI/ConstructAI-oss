"""User feedback schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class FeedbackCreateRequest(BaseModel):
    agent_name: str
    output_type: str | None = None
    rating: int
    feedback_text: str | None = None
    agent_trace_id: str | None = None
    project_id: uuid.UUID | None = None

    @field_validator("rating")
    @classmethod
    def rating_must_be_thumbs(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("rating must be 1 or -1")
        return v


class FeedbackResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    agent_name: str | None = None
    rating: int
    feedback_text: str | None = None
    created_at: datetime


class FeedbackListResponse(BaseModel):
    items: list[FeedbackResponse]
    total: int


class FeedbackSummaryResponse(BaseModel):
    agent_name: str
    total_ratings: int
    positive_count: int
    negative_count: int
    approval_rate: float
