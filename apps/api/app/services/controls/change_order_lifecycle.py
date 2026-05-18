"""Change order lifecycle service: PCO -> COR -> CO.

Manages the full lifecycle from Potential Change Order through
Change Order Request to approved Change Order, including cost
aggregation, status transitions, and SOV integration.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_order_lifecycle import (
    ChangeOrderRequest,
    CORPCOLink,
    PotentialChangeOrder,
)
from app.models.evm import ChangeOrder
from app.models.project import Project
from app.services.controls.change_order_analyzer import analyze_change_order
from app.services.controls.pay_application_math import compute_pco_total

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status transition matrices
# ---------------------------------------------------------------------------

PCO_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_review", "void"},
    "pending_review": {"approved", "rejected", "void"},
    "approved": {"void"},
    "rejected": {"draft", "void"},
    "void": set(),
}

COR_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted", "void"},
    "submitted": {"under_review", "void"},
    "under_review": {"approved", "rejected", "void"},
    "approved": set(),  # terminal — triggers CO creation
    "rejected": {"draft", "void"},
    "void": set(),
}


def _validate_transition(current: str, new: str, matrix: dict[str, set[str]]) -> None:
    """Raise ValueError if transition is invalid."""
    allowed = matrix.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"Cannot transition from '{current}' to '{new}'. "
            f"Allowed transitions: {sorted(allowed) if allowed else 'none (terminal state)'}"
        )


# ---------------------------------------------------------------------------
# PCO operations
# ---------------------------------------------------------------------------


async def get_next_pco_number(db: AsyncSession, project_id: uuid.UUID) -> int:
    """Get the next sequential PCO number for a project."""
    result = await db.execute(
        select(func.coalesce(func.max(PotentialChangeOrder.pco_number), 0)).where(
            PotentialChangeOrder.project_id == project_id
        )
    )
    return result.scalar_one() + 1


async def create_pco(
    db: AsyncSession,
    project_id: uuid.UUID,
    title: str,
    description: str,
    change_type: str,
    originated_by: uuid.UUID,
    *,
    labor_cost: Decimal = Decimal("0"),
    material_cost: Decimal = Decimal("0"),
    equipment_cost: Decimal = Decimal("0"),
    subcontractor_cost: Decimal = Decimal("0"),
    overhead_cost: Decimal = Decimal("0"),
    profit_markup_pct: Decimal = Decimal("0"),
    schedule_impact_days: int = 0,
    spec_section: str | None = None,
    drawing_reference: str | None = None,
    attachments: list[dict] | None = None,
) -> PotentialChangeOrder:
    """Create a new PCO with auto-incrementing number and AI analysis."""
    pco_number = await get_next_pco_number(db, project_id)
    total = compute_pco_total(
        labor_cost,
        material_cost,
        equipment_cost,
        subcontractor_cost,
        overhead_cost,
        profit_markup_pct,
    )

    # Run AI analysis
    ai_result = await analyze_change_order(
        title=title,
        description=description,
        change_type=change_type,
        cost_impact=total,
        schedule_impact_days=schedule_impact_days,
    )

    pco = PotentialChangeOrder(
        project_id=project_id,
        pco_number=pco_number,
        title=title,
        description=description,
        change_type=change_type,
        originated_by=originated_by,
        labor_cost=labor_cost,
        material_cost=material_cost,
        equipment_cost=equipment_cost,
        subcontractor_cost=subcontractor_cost,
        overhead_cost=overhead_cost,
        profit_markup_pct=profit_markup_pct,
        total_cost=total,
        schedule_impact_days=schedule_impact_days,
        spec_section=spec_section,
        drawing_reference=drawing_reference,
        attachments=attachments or [],
        risk_score=ai_result.get("risk_score"),
        ai_analysis=ai_result,
    )
    db.add(pco)
    await db.flush()
    await db.refresh(pco)
    return pco


async def update_pco(
    db: AsyncSession,
    pco: PotentialChangeOrder,
    **kwargs,
) -> PotentialChangeOrder:
    """Update PCO fields. Recomputes total_cost if cost breakdown changes."""
    cost_breakdown = kwargs.pop("cost_breakdown", None)
    status = kwargs.pop("status", None)

    if status is not None:
        _validate_transition(pco.status, status, PCO_TRANSITIONS)
        # L-27: Log a structured trail of every status transition (especially
        # `approved -> void` which can mask a reversed approval). Full audit
        # persistence happens at the route layer; this gives us a fallback
        # signal in the service log even when a caller forgets to audit.
        logger.info(
            "pco_status_transition",
            extra={
                "pco_id": str(pco.id) if pco.id else None,
                "from": pco.status,
                "to": status,
            },
        )
        pco.status = status

    for field, value in kwargs.items():
        if value is not None and hasattr(pco, field):
            setattr(pco, field, value)

    if cost_breakdown is not None:
        pco.labor_cost = cost_breakdown.get("labor_cost", pco.labor_cost)
        pco.material_cost = cost_breakdown.get("material_cost", pco.material_cost)
        pco.equipment_cost = cost_breakdown.get("equipment_cost", pco.equipment_cost)
        pco.subcontractor_cost = cost_breakdown.get("subcontractor_cost", pco.subcontractor_cost)
        pco.overhead_cost = cost_breakdown.get("overhead_cost", pco.overhead_cost)
        pco.profit_markup_pct = cost_breakdown.get("profit_markup_pct", pco.profit_markup_pct)
        pco.total_cost = compute_pco_total(
            pco.labor_cost,
            pco.material_cost,
            pco.equipment_cost,
            pco.subcontractor_cost,
            pco.overhead_cost,
            pco.profit_markup_pct,
        )

    pco.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(pco)
    return pco


# ---------------------------------------------------------------------------
# COR operations
# ---------------------------------------------------------------------------


async def get_next_cor_number(db: AsyncSession, project_id: uuid.UUID) -> int:
    """Get the next sequential COR number for a project."""
    result = await db.execute(
        select(func.coalesce(func.max(ChangeOrderRequest.cor_number), 0)).where(
            ChangeOrderRequest.project_id == project_id
        )
    )
    return result.scalar_one() + 1


async def create_cor(
    db: AsyncSession,
    project_id: uuid.UUID,
    title: str,
    pco_ids: list[uuid.UUID],
    *,
    description: str | None = None,
    markup_pct: Decimal = Decimal("0"),
    overhead_pct: Decimal = Decimal("0"),
    cor_adjustment: Decimal = Decimal("0"),
) -> ChangeOrderRequest:
    """Create a COR from approved PCOs.

    Validates:
    - All PCO IDs exist and belong to the project
    - All PCOs have status 'approved'
    - No PCO is already linked to another COR
    """
    # Fetch and validate PCOs
    result = await db.execute(
        select(PotentialChangeOrder).where(
            PotentialChangeOrder.id.in_(pco_ids),
            PotentialChangeOrder.project_id == project_id,
        )
    )
    pcos = list(result.scalars().all())

    if len(pcos) != len(pco_ids):
        found_ids = {p.id for p in pcos}
        missing = [str(pid) for pid in pco_ids if pid not in found_ids]
        raise ValueError(f"PCOs not found in project: {missing}")

    for pco in pcos:
        if pco.status != "approved":
            raise ValueError(f"PCO #{pco.pco_number} has status '{pco.status}', must be 'approved'")

    # Check none are already linked
    existing_links = await db.execute(
        select(CORPCOLink.pco_id).where(CORPCOLink.pco_id.in_(pco_ids))
    )
    already_linked = set(existing_links.scalars().all())
    if already_linked:
        raise ValueError(f"PCOs already linked to a COR: {[str(pid) for pid in already_linked]}")

    # Aggregate costs and schedule impact
    pco_subtotal = sum((p.total_cost for p in pcos), Decimal("0"))
    schedule_days = sum(p.schedule_impact_days for p in pcos)

    # Apply COR-level markups
    # NOTE: Markup and overhead are applied multiplicatively (compounded),
    # meaning a 10% markup + 10% overhead results in a 21% total increase
    # (1.10 * 1.10 = 1.21), NOT a simple 20% additive increase. This is
    # intentional per industry practice where overhead applies to the
    # marked-up subtotal. If additive behavior is desired, change to:
    #   markup_factor = Decimal("1") + (markup_pct + overhead_pct) / Decimal("100")
    markup_factor = (Decimal("1") + markup_pct / Decimal("100")) * (
        Decimal("1") + overhead_pct / Decimal("100")
    )
    total_cost = (pco_subtotal * markup_factor + cor_adjustment).quantize(Decimal("0.01"))

    cor_number = await get_next_cor_number(db, project_id)

    cor = ChangeOrderRequest(
        project_id=project_id,
        cor_number=cor_number,
        title=title,
        description=description,
        markup_pct=markup_pct,
        overhead_pct=overhead_pct,
        cor_adjustment=cor_adjustment,
        total_cost=total_cost,
        schedule_impact_days=schedule_days,
    )
    db.add(cor)
    await db.flush()
    await db.refresh(cor)

    # Create COR-PCO links
    for pco_id in pco_ids:
        link = CORPCOLink(cor_id=cor.id, pco_id=pco_id)
        db.add(link)

    await db.flush()
    await db.refresh(cor)
    return cor


async def update_cor(
    db: AsyncSession,
    cor: ChangeOrderRequest,
    **kwargs,
) -> ChangeOrderRequest:
    """Update COR fields."""
    status = kwargs.pop("status", None)

    if status is not None:
        _validate_transition(cor.status, status, COR_TRANSITIONS)
        cor.status = status
        if status == "submitted":
            cor.submitted_at = datetime.now(UTC)

    for field, value in kwargs.items():
        if value is not None and hasattr(cor, field):
            setattr(cor, field, value)

    cor.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(cor)
    return cor


# ---------------------------------------------------------------------------
# COR -> CO approval (critical integration point)
# ---------------------------------------------------------------------------


async def approve_cor_to_co(
    db: AsyncSession,
    cor_id: uuid.UUID,
    approved_by: uuid.UUID,
) -> ChangeOrder:
    """Approve a COR and generate a Change Order.

    1. Validates COR is in 'under_review' status
    2. Creates ChangeOrder from COR data
    3. Computes contract adjustment fields
    4. Updates SOV with new CO line item

    .. warning:: Role-Based Approval Authority Gap

       This function currently validates that ``approved_by`` is a non-None
       valid UUID but does NOT enforce role-based approval authority (e.g.
       verifying the user has PM or executive role, or that the CO amount
       is within their approval limit). Full role-based authority checks
       require a user-roles schema and approval-limit configuration, which
       should be added in a future migration. Until then, authorization
       must be enforced at the API route layer.
    """
    # SECURITY: Basic validation that approved_by is a valid, non-None UUID.
    if approved_by is None:
        raise ValueError("approved_by user_id is required for COR approval")
    try:
        uuid.UUID(str(approved_by))
    except (ValueError, AttributeError):
        raise ValueError(f"approved_by must be a valid UUID, got: {approved_by}")

    cor = await db.get(ChangeOrderRequest, cor_id)
    if cor is None:
        raise ValueError("COR not found")

    _validate_transition(cor.status, "approved", COR_TRANSITIONS)

    # Approve the COR
    now = datetime.now(UTC)
    cor.status = "approved"
    cor.approved_by = approved_by
    cor.approved_at = now
    cor.updated_at = now

    # Get project for contract value
    project = await db.get(Project, cor.project_id)
    if project is None:
        raise ValueError("Project not found")

    original_contract = project.contract_value or Decimal("0")

    # Sum all previously approved COs for this project
    prev_cos_result = await db.execute(
        select(func.coalesce(func.sum(ChangeOrder.cost_impact), Decimal("0"))).where(
            ChangeOrder.project_id == cor.project_id,
            ChangeOrder.status == "approved",
        )
    )
    previous_cos_sum = prev_cos_result.scalar_one()

    # Get next CO number — extract numeric suffix with regex to avoid
    # string-comparison bugs (e.g. MAX("9") > MAX("10") in string sort).
    co_nums_result = await db.execute(
        select(ChangeOrder.co_number).where(ChangeOrder.project_id == cor.project_id)
    )
    existing_co_numbers = list(co_nums_result.scalars().all())
    max_co_int = 0
    for raw_num in existing_co_numbers:
        match = re.search(r"(\d+)", str(raw_num))
        if match:
            max_co_int = max(max_co_int, int(match.group(1)))
    next_co_num = str(max_co_int + 1)

    # Aggregate cost breakdown from PCOs
    pco_ids_result = await db.execute(select(CORPCOLink.pco_id).where(CORPCOLink.cor_id == cor_id))
    pco_ids = list(pco_ids_result.scalars().all())

    labor = material = equipment = sub = overhead = Decimal("0")
    if pco_ids:
        pcos_result = await db.execute(
            select(PotentialChangeOrder).where(PotentialChangeOrder.id.in_(pco_ids))
        )
        for pco in pcos_result.scalars().all():
            labor += pco.labor_cost
            material += pco.material_cost
            equipment += pco.equipment_cost
            sub += pco.subcontractor_cost
            overhead += pco.overhead_cost

    co = ChangeOrder(
        project_id=cor.project_id,
        co_number=next_co_num,
        title=cor.title,
        description=cor.description or cor.title,
        change_type="owner_directed",  # default; COR doesn't carry type
        status="approved",
        requested_by=approved_by,
        cost_impact=cor.total_cost,
        schedule_impact_days=cor.schedule_impact_days,
        cor_id=cor.id,
        approved_date=now,
        labor_cost=labor,
        material_cost=material,
        equipment_cost=equipment,
        subcontractor_cost=sub,
        overhead_cost=overhead,
        markup_pct=cor.markup_pct,
        overhead_pct=cor.overhead_pct,
        original_contract_sum=original_contract,
        previous_cos_sum=previous_cos_sum,
        this_co_amount=cor.total_cost,
        new_contract_sum=original_contract + previous_cos_sum + cor.total_cost,
    )

    # Run AI analysis
    ai_result = await analyze_change_order(
        title=co.title,
        description=co.description,
        change_type=co.change_type,
        cost_impact=co.cost_impact,
        schedule_impact_days=co.schedule_impact_days,
    )
    co.risk_score = ai_result.get("risk_score")
    co.ai_analysis = ai_result

    db.add(co)
    await db.flush()
    await db.refresh(co)

    # Add CO to Schedule of Values
    from app.services.controls.pay_application_service import add_co_to_sov

    try:
        await add_co_to_sov(db, cor.project_id, co.id)
    except Exception:
        logger.error("Failed to add CO %s to SOV", co.co_number, exc_info=True)

    # IG-09: Flag EVM snapshots for BAC adjustment when a CO is approved
    try:
        from app.models.evm import EVMSnapshot

        evm_result = await db.execute(
            select(EVMSnapshot)
            .where(EVMSnapshot.project_id == cor.project_id)
            .order_by(EVMSnapshot.snapshot_date.desc())
            .limit(1)
        )
        latest_evm = evm_result.scalars().first()
        if latest_evm:
            meta = dict(co.ai_analysis or {})
            meta["evm_bac_adjustment_needed"] = True
            meta["evm_bac_adjustment_amount"] = str(co.cost_impact)
            meta["evm_latest_snapshot_id"] = str(latest_evm.id)
            meta["evm_latest_snapshot_bac"] = str(latest_evm.bac)
            co.ai_analysis = meta
            logger.info(
                "CO #%s flagged EVM BAC adjustment needed: +%s (snapshot %s)",
                co.co_number,
                co.cost_impact,
                latest_evm.id,
            )
        else:
            logger.debug(
                "No EVM snapshots found for project %s; skipping BAC flag",
                cor.project_id,
            )
    except Exception:
        logger.warning(
            "Failed to check EVM snapshots for CO #%s BAC adjustment",
            co.co_number,
            exc_info=True,
        )

    return co


# ---------------------------------------------------------------------------
# Cumulative impact
# ---------------------------------------------------------------------------


async def get_cumulative_co_impact(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Get cumulative approved CO impact for a project."""
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    original_contract = project.contract_value or Decimal("0")

    result = await db.execute(
        select(
            func.count(ChangeOrder.id),
            func.coalesce(func.sum(ChangeOrder.cost_impact), Decimal("0")),
            func.coalesce(func.sum(ChangeOrder.schedule_impact_days), 0),
        ).where(
            ChangeOrder.project_id == project_id,
            ChangeOrder.status == "approved",
        )
    )
    row = result.one()
    total_cost = row[1]
    pct_change = (
        (total_cost / original_contract * Decimal("100")).quantize(Decimal("0.01"))
        if original_contract > 0
        else Decimal("0")
    )

    return {
        "total_approved_cos": row[0],
        "total_cost_impact": total_cost,
        "total_schedule_impact_days": row[2],
        "original_contract_value": original_contract,
        "current_contract_value": original_contract + total_cost,
        "percent_change": pct_change,
    }
