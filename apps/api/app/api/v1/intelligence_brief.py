"""API endpoints for project intelligence briefs."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.evm import IntelligenceBrief
from app.models.user import User
from app.schemas.intelligence_brief import (
    IntelligenceBriefListResponse,
    IntelligenceBriefResponse,
    IntelligenceBriefSummary,
)
from app.schemas.pagination import PaginationMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/{project_id}/intelligence-brief",
    response_model=IntelligenceBriefResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_intelligence_brief(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an on-demand intelligence brief for a project."""
    await verify_project_access(project_id, current_user, db)

    # Load project data
    from app.models.project import Project

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_data: dict[str, Any] = {
        "name": project.name,
        "project_number": project.project_number,
        "type": project.type or "commercial",
        "address": project.address or "",
        "contract_value": str(project.contract_value or 0),
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "end_date": project.end_date.isoformat() if project.end_date else None,
    }

    # Load latest EVM snapshot
    from app.models.evm import EVMSnapshot

    evm_stmt = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(desc(EVMSnapshot.snapshot_date))
        .limit(1)
    )
    evm_result = await db.execute(evm_stmt)
    latest_evm = evm_result.scalar_one_or_none()
    if latest_evm:
        project_data["latest_evm"] = {
            "bac": str(latest_evm.bac),
            "pv": str(latest_evm.pv),
            "ev": str(latest_evm.ev),
            "ac": str(latest_evm.ac),
            "spi": str(latest_evm.spi),
            "cpi": str(latest_evm.cpi),
            "percent_complete": str(latest_evm.percent_complete),
        }

    # Load EVM snapshot history (last 4 for SPI trend)
    evm_hist_stmt = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(desc(EVMSnapshot.snapshot_date))
        .limit(4)
    )
    evm_hist_result = await db.execute(evm_hist_stmt)
    snapshots = evm_hist_result.scalars().all()
    project_data["evm_snapshots"] = [
        {"snapshot_date": s.snapshot_date.isoformat(), "spi": str(s.spi), "cpi": str(s.cpi)}
        for s in reversed(snapshots)
    ]

    # Load change orders
    from app.models.evm import ChangeOrder

    co_stmt = (
        select(ChangeOrder)
        .where(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.created_at.desc())
        .limit(50)
    )
    co_result = await db.execute(co_stmt)
    cos = co_result.scalars().all()
    project_data["change_orders"] = [
        {
            "co_number": co.co_number,
            "title": co.title,
            "status": co.status,
            "cost_impact": str(co.cost_impact),
            "schedule_impact_days": co.schedule_impact_days,
            "submitted_at": co.submitted_at.isoformat() if co.submitted_at else None,
        }
        for co in cos
    ]

    # Load schedule activities (if available)
    try:
        from app.models.scheduling import ScheduleActivity, ScheduleBaseline

        baseline_stmt = (
            select(ScheduleBaseline)
            .where(ScheduleBaseline.project_id == project_id)
            .order_by(desc(ScheduleBaseline.created_at))
            .limit(1)
        )
        baseline_result = await db.execute(baseline_stmt)
        baseline = baseline_result.scalar_one_or_none()
        if baseline:
            act_stmt = (
                select(ScheduleActivity)
                .where(ScheduleActivity.baseline_id == baseline.id)
                .limit(200)
            )
            act_result = await db.execute(act_stmt)
            acts = act_result.scalars().all()
            project_data["activities"] = [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "duration_days": a.duration_days,
                    "predecessors": a.predecessors or [],
                    "wbs_path": getattr(a, "wbs_path", None),
                    "total_float": getattr(a, "total_float", None),
                }
                for a in acts
            ]
            project_data["planned_duration"] = sum(
                a.duration_days for a in acts if not a.predecessors
            )
    except Exception as exc:
        logger.warning("Failed to load schedule data: %s", exc)

    # Run the agent
    from app.services.agents.weekly_brief_agent import generate_weekly_brief

    org_id = str(project.org_id) if project.org_id else None
    brief_result = await generate_weekly_brief(
        project_id=str(project_id),
        project_data=project_data,
        org_id=org_id,
        generated_by=str(current_user.id),
    )

    # Generate PDF
    pdf_s3_key = None
    try:
        from app.services.agents.brief_pdf_generator import generate_brief_pdf
        from app.utils.s3 import upload_file

        pdf_bytes = generate_brief_pdf(
            brief_data=brief_result,
            project_name=project.name,
            project_number=project.project_number or "",
        )
        pdf_s3_key = f"intelligence-briefs/{project_id}/{date.today().isoformat()}.pdf"
        upload_file(pdf_s3_key, pdf_bytes, "application/pdf")
    except Exception as exc:
        logger.warning("PDF generation/upload failed: %s", exc)

    # Upload JSON
    json_s3_key = None
    try:
        from app.utils.s3 import upload_file as s3_upload

        json_bytes = json.dumps(brief_result, default=str).encode()
        json_s3_key = f"intelligence-briefs/{project_id}/{date.today().isoformat()}.json"
        s3_upload(json_s3_key, json_bytes, "application/json")
    except Exception as exc:
        logger.warning("JSON upload failed: %s", exc)

    # Save to DB
    brief = IntelligenceBrief(
        project_id=project_id,
        generated_by=current_user.id,
        report_date=date.today(),
        overall_health_score=brief_result.get("overall_health_score", 50),
        project_status=brief_result.get("project_status", "YELLOW"),
        schedule_health_score=brief_result.get("schedule_health_score", 50),
        cost_health_score=brief_result.get("cost_health_score", 50),
        risk_score=brief_result.get("risk_score", 50),
        productivity_score=brief_result.get("productivity_score", 50),
        executive_summary=brief_result.get("executive_summary", ""),
        schedule_intelligence=brief_result.get("schedule_intelligence", {}),
        cost_intelligence=brief_result.get("cost_intelligence", {}),
        risk_intelligence=brief_result.get("risk_intelligence", {}),
        productivity_intelligence=brief_result.get("productivity_intelligence", {}),
        action_items=brief_result.get("action_items", []),
        metrics_dashboard=brief_result.get("metrics_dashboard", {}),
        narrative_report=brief_result.get("narrative_report", ""),
        guardrails_result=brief_result.get("guardrails_result", {}),
        pdf_s3_key=pdf_s3_key,
        json_s3_key=json_s3_key,
    )
    db.add(brief)
    await db.commit()
    await db.refresh(brief)

    # Send notifications (fire and forget)
    try:
        from app.services.agents.notification_service import send_brief_notifications

        await send_brief_notifications(
            project_id=str(project_id),
            brief_id=str(brief.id),
            pdf_bytes=pdf_bytes if pdf_s3_key else None,
            json_summary=brief_result,
            db=db,
        )
    except Exception as exc:
        logger.warning("Notification sending failed: %s", exc)

    return brief


@router.get(
    "/{project_id}/intelligence-brief/latest",
    response_model=IntelligenceBriefResponse,
)
async def get_latest_brief(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent intelligence brief for a project."""
    await verify_project_access(project_id, current_user, db)

    stmt = (
        select(IntelligenceBrief)
        .where(IntelligenceBrief.project_id == project_id)
        .order_by(desc(IntelligenceBrief.created_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    brief = result.scalar_one_or_none()

    if not brief:
        raise HTTPException(status_code=404, detail="No intelligence brief found for this project")

    # Add presigned PDF URL if available
    response = IntelligenceBriefResponse.model_validate(brief)
    if brief.pdf_s3_key:
        try:
            from app.utils.s3 import generate_presigned_url

            response.pdf_url = generate_presigned_url(brief.pdf_s3_key, expires_in=3600)
        except Exception:
            pass

    return response


@router.get(
    "/{project_id}/intelligence-brief/history",
    response_model=IntelligenceBriefListResponse,
)
async def list_briefs(
    project_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all intelligence briefs for a project with pagination."""
    await verify_project_access(project_id, current_user, db)

    stmt = (
        select(IntelligenceBrief)
        .where(IntelligenceBrief.project_id == project_id)
        .order_by(desc(IntelligenceBrief.created_at))
        .offset(skip)
        .limit(limit + 1)  # fetch one extra to check has_more
    )
    result = await db.execute(stmt)
    briefs = result.scalars().all()

    has_more = len(briefs) > limit
    briefs = briefs[:limit]

    summaries = [IntelligenceBriefSummary.model_validate(b) for b in briefs]
    cursor = str(briefs[-1].id) if briefs and has_more else None

    return IntelligenceBriefListResponse(
        data=summaries,
        meta=PaginationMeta(cursor=cursor, has_more=has_more),
    )
