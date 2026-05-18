import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import _validate_email_format

_KNOWN_SETTINGS_KEYS = {
    "theme",
    "language",
    "timezone",
    "notifications",
    "default_project_id",
    "dashboard_layout",
    "date_format",
    "units",
}


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    org_id: uuid.UUID
    is_active: bool
    email_verified: bool = False
    mfa_enabled: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    settings: dict | None = None

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("settings must be a JSON object (dict)")
        unknown = set(v.keys()) - _KNOWN_SETTINGS_KEYS
        if unknown:
            raise ValueError(
                f"Unknown settings keys: {sorted(unknown)}. Allowed: {sorted(_KNOWN_SETTINGS_KEYS)}"
            )
        return v


class AdminUserCreate(BaseModel):
    """Admin creates a new user within their organization."""

    email: str = Field(max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    role: Literal[
        "org_admin",
        "project_admin",
        "project_manager",
        "superintendent",
        "engineer",
        "readonly",
    ] = "readonly"

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return _validate_email_format(v)


class AdminUserPatch(BaseModel):
    """Admin updates a user's profile, role, or active status."""

    full_name: str | None = Field(default=None, max_length=255)
    role: (
        Literal[
            "org_admin",
            "project_admin",
            "project_manager",
            "superintendent",
            "engineer",
            "readonly",
        ]
        | None
    ) = None
    is_active: bool | None = None


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int


class NotificationPreferences(BaseModel):
    email_notifications: bool = True
    safety_alerts: bool = True
    schedule_changes: bool = True
    daily_digest: bool = False
