"""API endpoints for Bid/No-Bid Decision Intelligence.

All endpoints are org-scoped — bids exist before projects.
"""

from __future__ import annotations

import contextlib
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.bid import BidDecision, BidOpportunity
from app.models.user import User
from app.schemas.bid import (
    BidAnalyticsResponse,
    BidCSVUploadResponse,
    BidDecisionResponse,
    BidOpportunityCreate,
    BidOpportunityListResponse,
    BidOpportunityResponse,
    BidOpportunityWithDecision,
    CSVRowError,
    RecordDecisionRequest,
    RecordOutcomeRequest,
)
from app.schemas.pagination import PaginationMeta

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_org_access(org_id: uuid.UUID, user: User) -> None:
    """Verify user belongs to the requested org."""
    if user.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this organization",
        )


async def _get_opportunity(
    org_id: uuid.UUID, opportunity_id: uuid.UUID, db: AsyncSession
) -> BidOpportunity:
    """Fetch a bid opportunity or raise 404."""
    result = await db.execute(
        select(BidOpportunity).where(
            BidOpportunity.id == opportunity_id,
            BidOpportunity.org_id == org_id,
        )
    )
    opp = result.scalar_one_or_none()
    if opp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bid opportunity not found",
        )
    return opp


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/bid-opportunities",
    response_model=BidOpportunityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bid_opportunity(
    org_id: uuid.UUID,
    body: BidOpportunityCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "create")),
):
    """Create a new bid opportunity."""
    await _verify_org_access(org_id, current_user)

    opp = BidOpportunity(
        org_id=org_id,
        name=body.name,
        owner_name=body.owner_name,
        project_type=body.project_type,
        delivery_method=body.delivery_method,
        estimated_value=body.estimated_value,
        location=body.location,
        latitude=body.latitude,
        longitude=body.longitude,
        bid_due_date=body.bid_due_date,
        description=body.description,
        metadata_json=body.metadata_json or {},
    )
    db.add(opp)
    await db.commit()
    await db.refresh(opp)
    return opp


@router.get(
    "/{org_id}/bid-opportunities",
    response_model=BidOpportunityListResponse,
)
async def list_bid_opportunities(
    org_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """List bid opportunities for the organization."""
    await _verify_org_access(org_id, current_user)

    stmt = select(BidOpportunity).where(BidOpportunity.org_id == org_id)
    if status_filter:
        stmt = stmt.where(BidOpportunity.status == status_filter)

    stmt = stmt.order_by(BidOpportunity.created_at.desc())
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(BidOpportunity, cursor_id)
        if cursor_obj:
            stmt = stmt.where(BidOpportunity.created_at < cursor_obj.created_at)

    stmt = stmt.limit(limit + 1)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    items = rows[:limit]

    return BidOpportunityListResponse(
        data=[BidOpportunityResponse.model_validate(item) for item in items],
        meta=PaginationMeta(
            cursor=str(items[-1].id) if items and has_more else None,
            has_more=has_more,
        ),
    )


@router.get(
    "/{org_id}/bid-opportunities/{opportunity_id}",
    response_model=BidOpportunityWithDecision,
)
async def get_bid_opportunity(
    org_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Get a single bid opportunity with its latest decision."""
    await _verify_org_access(org_id, current_user)
    opp = await _get_opportunity(org_id, opportunity_id, db)

    # Get latest decision
    dec_result = await db.execute(
        select(BidDecision)
        .where(BidDecision.opportunity_id == opportunity_id)
        .order_by(BidDecision.created_at.desc())
        .limit(1)
    )
    latest_dec = dec_result.scalar_one_or_none()

    response = BidOpportunityWithDecision.model_validate(opp)
    if latest_dec:
        response.latest_decision = BidDecisionResponse.model_validate(latest_dec)
    return response


# ---------------------------------------------------------------------------
# AI Scoring
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/bid-opportunities/{opportunity_id}/score",
    response_model=BidDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def score_bid_opportunity(
    org_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "update")),
):
    """Run AI scoring on a bid opportunity."""
    await _verify_org_access(org_id, current_user)
    opp = await _get_opportunity(org_id, opportunity_id, db)

    from app.services.agents.bid_decision_agent import score_bid_opportunity as run_agent

    opp_data = {
        "name": opp.name,
        "owner_name": opp.owner_name,
        "project_type": opp.project_type,
        "delivery_method": opp.delivery_method,
        "estimated_value": float(opp.estimated_value) if opp.estimated_value else 0,
        "location": opp.location,
        "latitude": opp.latitude,
        "longitude": opp.longitude,
    }

    result = await run_agent(
        opportunity_id=str(opportunity_id),
        opportunity=opp_data,
        org_id=str(org_id),
    )

    decision = BidDecision(
        opportunity_id=opportunity_id,
        decided_by=current_user.id,
        ai_score=result["composite_score"],
        ai_recommendation=result["recommendation"],
        ai_reasoning=result.get("reasoning", ""),
        factor_scores=result.get("factor_scores", {}),
        win_probability=result.get("win_probability"),
    )
    db.add(decision)
    await db.commit()
    await db.refresh(decision)
    return decision


# ---------------------------------------------------------------------------
# Human Decision & Outcome
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/bid-opportunities/{opportunity_id}/decide",
    response_model=BidOpportunityResponse,
)
async def record_decision(
    org_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    body: RecordDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "update")),
):
    """Record a human bid/no-bid decision."""
    await _verify_org_access(org_id, current_user)
    opp = await _get_opportunity(org_id, opportunity_id, db)

    # Update opportunity status
    opp.status = "pursuing" if body.decision == "pursue" else "declined"

    # Get latest AI decision and update with human decision
    dec_result = await db.execute(
        select(BidDecision)
        .where(BidDecision.opportunity_id == opportunity_id)
        .order_by(BidDecision.created_at.desc())
        .limit(1)
    )
    latest_dec = dec_result.scalar_one_or_none()
    if latest_dec:
        latest_dec.human_decision = body.decision
        latest_dec.human_notes = body.notes
        latest_dec.decided_by = current_user.id
    else:
        # Create a decision record if none exists
        new_dec = BidDecision(
            opportunity_id=opportunity_id,
            decided_by=current_user.id,
            human_decision=body.decision,
            human_notes=body.notes,
        )
        db.add(new_dec)

    await db.commit()
    await db.refresh(opp)
    return opp


@router.post(
    "/{org_id}/bid-opportunities/{opportunity_id}/outcome",
    response_model=BidOpportunityResponse,
)
async def record_outcome(
    org_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    body: RecordOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "update")),
):
    """Record whether the bid was won or lost."""
    await _verify_org_access(org_id, current_user)
    opp = await _get_opportunity(org_id, opportunity_id, db)

    opp.outcome = body.outcome
    opp.status = body.outcome  # "won" or "lost"
    if body.actual_margin is not None:
        opp.actual_margin = Decimal(str(body.actual_margin))

    await db.commit()
    await db.refresh(opp)
    return opp


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/bid-analytics",
    response_model=BidAnalyticsResponse,
)
async def get_bid_analytics(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Get bid analytics: win rates, score distributions, by type/method."""
    await _verify_org_access(org_id, current_user)

    # SQL-based aggregation to avoid loading all rows into memory
    from sqlalchemy import case, literal_column

    # Total counts via SQL
    count_result = await db.execute(
        select(
            func.count().label("total"),
            func.count(case((BidOpportunity.outcome == "won", literal_column("1")))).label("won"),
            func.count(case((BidOpportunity.outcome == "lost", literal_column("1")))).label("lost"),
        ).where(BidOpportunity.org_id == org_id)
    )
    row = count_result.one()
    total, won, lost = int(row.total), int(row.won), int(row.lost)
    decided = won + lost

    # Average AI score
    dec_result = await db.execute(
        select(func.avg(BidDecision.ai_score))
        .join(BidOpportunity, BidDecision.opportunity_id == BidOpportunity.id)
        .where(BidOpportunity.org_id == org_id, BidDecision.ai_score > 0)
    )
    avg_score = dec_result.scalar()

    # Group by type via SQL
    type_result = await db.execute(
        select(
            func.coalesce(BidOpportunity.project_type, "unknown").label("ptype"),
            func.count().label("total"),
            func.count(case((BidOpportunity.outcome == "won", literal_column("1")))).label("won"),
            func.count(case((BidOpportunity.outcome == "lost", literal_column("1")))).label("lost"),
        )
        .where(BidOpportunity.org_id == org_id)
        .group_by("ptype")
    )
    by_type: dict[str, dict] = {}
    for r in type_result.all():
        d = int(r.won) + int(r.lost)
        by_type[r.ptype] = {
            "total": int(r.total),
            "won": int(r.won),
            "lost": int(r.lost),
            "win_rate": int(r.won) / d if d > 0 else 0.0,
        }

    # Group by method via SQL
    method_result = await db.execute(
        select(
            func.coalesce(BidOpportunity.delivery_method, "unknown").label("method"),
            func.count().label("total"),
            func.count(case((BidOpportunity.outcome == "won", literal_column("1")))).label("won"),
            func.count(case((BidOpportunity.outcome == "lost", literal_column("1")))).label("lost"),
        )
        .where(BidOpportunity.org_id == org_id)
        .group_by("method")
    )
    by_method: dict[str, dict] = {}
    for r in method_result.all():
        d = int(r.won) + int(r.lost)
        by_method[r.method] = {
            "total": int(r.total),
            "won": int(r.won),
            "lost": int(r.lost),
            "win_rate": int(r.won) / d if d > 0 else 0.0,
        }

    return BidAnalyticsResponse(
        total_opportunities=total,
        total_won=won,
        total_lost=lost,
        overall_win_rate=won / decided if decided > 0 else 0.0,
        avg_ai_score=round(float(avg_score), 1) if avg_score else None,
        by_project_type=by_type,
        by_delivery_method=by_method,
    )


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/bid-opportunities/import-csv",
    response_model=BidCSVUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_bid_csv(
    org_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("estimates", "create")),
):
    """Import historical bid data from CSV."""
    await _verify_org_access(org_id, current_user)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a CSV",
        )

    if file.size and file.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large (max 10 MB)",
        )

    from app.services.estimating.bid_history_parser import parse_bid_history_csv

    parse_result = await parse_bid_history_csv(content, str(org_id))

    # Cap the number of records that can be imported in a single request
    max_import_rows = 1000
    if len(parse_result.opportunities) > max_import_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV contains {len(parse_result.opportunities)} records, "
            f"exceeding the maximum of {max_import_rows} per import.",
        )

    imported = 0
    for record in parse_result.opportunities:
        opp_data = record["opportunity"]
        dec_data = record["decision"]

        opp = BidOpportunity(
            org_id=org_id,
            name=opp_data["name"],
            owner_name=opp_data.get("owner_name"),
            project_type=opp_data.get("project_type"),
            delivery_method=opp_data.get("delivery_method"),
            estimated_value=opp_data.get("estimated_value"),
            location=opp_data.get("location"),
            status=opp_data.get("status", "evaluating"),
            outcome=opp_data.get("outcome"),
            actual_margin=opp_data.get("actual_margin"),
            metadata_json=opp_data.get("metadata_json", {}),
        )
        if opp_data.get("bid_due_date"):
            from datetime import date as date_type

            with contextlib.suppress(ValueError, TypeError):
                opp.bid_due_date = date_type.fromisoformat(opp_data["bid_due_date"])

        db.add(opp)
        await db.flush()  # Get the ID

        decision = BidDecision(
            opportunity_id=opp.id,
            decided_by=current_user.id,
            ai_score=dec_data.get("ai_score", 0),
            ai_recommendation=dec_data.get("ai_recommendation"),
            ai_reasoning=dec_data.get("ai_reasoning"),
            human_decision=dec_data.get("human_decision"),
            human_notes=dec_data.get("human_notes"),
            factor_scores=dec_data.get("factor_scores", {}),
            win_probability=dec_data.get("win_probability"),
        )
        db.add(decision)
        imported += 1

    await db.commit()

    return BidCSVUploadResponse(
        imported=imported,
        errors=[
            CSVRowError(row=e.row, field=e.field, message=e.message) for e in parse_result.errors
        ],
        warnings=parse_result.warnings,
        total_rows=parse_result.row_count,
    )
