"""Pydantic schemas for magic link endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class MagicLinkCreateRequest(BaseModel):
    purpose: str
    entity_id: uuid.UUID | None = None
    recipient_email: str
    recipient_name: str | None = None
    expires_in_days: int = 7


class MagicLinkResponse(BaseModel):
    token: str
    expires_at: datetime
    purpose: str
    recipient_email: str | None = None


class MagicLinkUploadResponse(BaseModel):
    success: bool
    requirement_id: uuid.UUID | None = None
    message: str
