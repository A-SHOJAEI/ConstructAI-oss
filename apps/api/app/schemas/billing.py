"""Pydantic schemas for billing and subscription endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    plan_tier: str
    products_enabled: list[str]
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None

    model_config = {"from_attributes": True}


class ProductUsageResponse(BaseModel):
    product: str
    event_type: str
    quantity: int
    created_at: datetime

    model_config = {"from_attributes": True}
