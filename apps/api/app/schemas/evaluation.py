"""Evaluation and agent metrics schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class AgentEvaluationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    agent_name: str
    metric_name: str
    metric_value: float
    benchmark_target: float | None = None
    dataset_name: str | None = None
    dataset_size: int | None = None
    evaluation_date: date
    details: dict = Field(default_factory=dict)
    created_at: datetime


class AgentEvaluationListResponse(BaseModel):
    items: list[AgentEvaluationResponse]
    pagination: PaginationMeta | None = None


class AgentMetricsSummary(BaseModel):
    agent_name: str
    accuracy: float | None = None
    avg_latency_ms: float | None = None
    total_cost_usd: float | None = None
    error_rate: float | None = None
    total_invocations: int = 0
    last_evaluation_date: date | None = None


class AgentMetricsListResponse(BaseModel):
    items: list[AgentMetricsSummary]


class LLMUsageResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    time: datetime
    agent_name: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int | None = None
    cached: bool


class LLMUsageSummary(BaseModel):
    provider: str
    model: str
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float | None = None
    cache_hit_rate: float = 0.0


class EvaluationRunRequest(BaseModel):
    agent_names: list[str] | None = None
    dataset_name: str | None = None


class EvaluationRunResponse(BaseModel):
    evaluation_id: str
    status: str = "started"
    agents_queued: list[str] = Field(default_factory=list)
