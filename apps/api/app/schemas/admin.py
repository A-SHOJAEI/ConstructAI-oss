"""Admin and tenant management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TenantCreateRequest(BaseModel):
    org_name: str
    billing_plan: str = "startup"
    admin_email: str


class TenantCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    org_id: uuid.UUID
    billing_plan: str
    created_at: datetime


class TenantConfigResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    org_id: uuid.UUID
    feature_flags: dict = Field(default_factory=dict)
    model_preferences: dict = Field(default_factory=dict)
    billing_plan: str
    created_at: datetime
    updated_at: datetime


class TenantListResponse(BaseModel):
    items: list[TenantConfigResponse]


class FeatureFlagCreateRequest(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = False
    rollout_percentage: int = Field(default=0, ge=0, le=100)


class FeatureFlagResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    description: str | None = None
    enabled: bool
    rollout_percentage: int
    tenant_overrides: dict = Field(default_factory=dict)
    created_at: datetime

    @field_validator("tenant_overrides")
    @classmethod
    def validate_tenant_overrides(cls, v: dict) -> dict:
        """Ensure tenant_overrides is a dict mapping tenant IDs to booleans."""
        if not isinstance(v, dict):
            raise ValueError("tenant_overrides must be a JSON object (dict)")
        for key, val in v.items():
            if not isinstance(key, str):
                raise ValueError("tenant_overrides keys must be strings (tenant IDs)")
            if not isinstance(val, bool):
                raise ValueError(
                    f"tenant_overrides values must be booleans, got {type(val).__name__} "
                    f"for key '{key}'"
                )
        return v


class FeatureFlagListResponse(BaseModel):
    items: list[FeatureFlagResponse]
