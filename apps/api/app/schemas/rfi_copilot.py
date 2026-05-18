"""Pydantic schemas for RFI Copilot product endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class RfiAnalyticsResponse(BaseModel):
    total: int = 0
    open_count: int = 0
    overdue_count: int = 0
    responded_count: int = 0
    closed_count: int = 0
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
