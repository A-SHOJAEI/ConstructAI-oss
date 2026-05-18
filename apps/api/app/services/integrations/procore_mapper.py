"""Pure mapping functions between Procore API models and ConstructAI DB models.

All functions are stateless and side-effect-free.
Each 'map_*' function takes a Procore Pydantic model instance and returns
a dict of keyword arguments suitable for constructing the corresponding
ConstructAI SQLAlchemy model.

Reverse mappings (ConstructAI -> Procore format) are prefixed 'to_procore_*'.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.integrations.procore_api import (
    ProcoreBudgetLineItem,
    ProcoreChangeOrder,
    ProcoreDailyLog,
    ProcoreDocument,
    ProcoreProject,
    ProcoreRFI,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Procore -> ConstructAI mappings
# ---------------------------------------------------------------------------


def map_procore_project(p: ProcoreProject, org_id: Any) -> dict[str, Any]:
    """Map ProcoreProject -> kwargs for Project model."""
    status_map = {
        "Active": "active",
        "Inactive": "archived",
        "Pending": "preconstruction",
    }
    return {
        "org_id": org_id,
        "name": p.name,
        "project_number": p.project_number,
        "status": status_map.get(p.status or "", "preconstruction"),
        "address": _build_address(p),
        "start_date": _parse_date(p.start_date),
        "end_date": _parse_date(p.completion_date),
        "data_source": "procore",
        "procore_id": p.id,
        "metadata_": {"procore": {"city": p.city, "state_code": p.state_code}},
    }


def map_procore_rfi(r: ProcoreRFI, project_id: Any) -> dict[str, Any]:
    """Map ProcoreRFI -> kwargs for RFI model."""
    status_map = {
        "Open": "open",
        "Closed": "closed",
        "Draft": "draft",
    }
    priority_map = {
        "High": "high",
        "Normal": "normal",
        "Low": "low",
    }
    return {
        "project_id": project_id,
        "rfi_number": str(r.number) if r.number is not None else str(r.id),
        "subject": r.subject,
        "question": r.subject,
        "status": status_map.get(r.status or "", "open"),
        "priority": priority_map.get(r.priority or "", "normal"),
        "due_date": _parse_date(r.due_date),
        "data_source": "procore",
        "procore_id": r.id,
    }


def map_procore_document(d: ProcoreDocument, project_id: Any) -> dict[str, Any]:
    """Map ProcoreDocument -> kwargs for Document model."""
    return {
        "project_id": project_id,
        "type": d.document_type or "general",
        "title": d.name,
        "original_filename": d.filename or d.name,
        "file_size_bytes": d.file_size,
        "processing_status": "pending",
        "data_source": "procore",
        "procore_id": d.id,
        "metadata_": {
            "procore": {
                "description": d.description,
                "content_type": d.content_type,
            }
        },
    }


def map_procore_change_order(co: ProcoreChangeOrder, project_id: Any) -> dict[str, Any]:
    """Map ProcoreChangeOrder -> kwargs for ChangeOrder model."""
    return {
        "project_id": project_id,
        "co_number": str(co.number) if co.number is not None else str(co.id),
        "title": co.title,
        "description": co.title,
        "status": (co.status or "pending").lower(),
        "change_type": "owner_request",
        "cost_impact": Decimal(str(co.grand_total or 0)),
        "data_source": "procore",
        "procore_id": co.id,
    }


def map_procore_daily_log(dl: ProcoreDailyLog, project_id: Any) -> dict[str, Any]:
    """Map ProcoreDailyLog -> kwargs for DailyLog model."""
    return {
        "project_id": project_id,
        "log_date": _parse_date(dl.log_date) or date.today(),
        "weather": dl.weather or {},
        "notes": dl.notes,
        "data_source": "procore",
        "procore_id": dl.id,
    }


def map_procore_budget_to_evm(
    line_items: list[ProcoreBudgetLineItem],
) -> dict[str, Decimal]:
    """Aggregate budget line items into EVM base values.

    Returns dict with planned_value (sum of original_budget_amount).
    This directly feeds BAC for Earned Value Management calculations.
    """
    planned: Decimal = sum(
        (Decimal(str(li.original_budget_amount or 0)) for li in line_items),
        Decimal(0),
    )
    return {
        "planned_value": planned,
        "original_budget": planned,
    }


# ---------------------------------------------------------------------------
# ConstructAI -> Procore reverse mappings
# ---------------------------------------------------------------------------


def to_procore_project(project: Any) -> dict[str, Any]:
    """Map ConstructAI Project -> Procore API project format."""
    return {
        "name": project.name,
        "project_number": project.project_number,
        "address": project.address,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "completion_date": project.end_date.isoformat() if project.end_date else None,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_date(val: str | None) -> date | None:
    """Parse an ISO date string to a date object."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _build_address(p: ProcoreProject) -> str | None:
    """Build a formatted address from Procore project fields."""
    parts = [p.address, p.city, p.state_code]
    filtered = [x for x in parts if x]
    return ", ".join(filtered) if filtered else None
