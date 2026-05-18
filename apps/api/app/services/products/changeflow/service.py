"""ChangeFlow T&M service — field-captured T&M entries, pricing, and negotiation.

Wraps the existing change order lifecycle with:
- T&M entry capture (labor, material, equipment, subcontractor)
- Pricing engine with markup cascade (burden, tax, overhead, profit, bond)
- COR generation from aggregated T&M entries
- Negotiation tracking per COR
- Dashboard analytics
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tm_entry import CorNegotiation, TmEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pricing engine
# ---------------------------------------------------------------------------


def calculate_pricing_summary(
    entries: list,
    overhead_pct: float = 0.10,
    profit_pct: float = 0.10,
    bond_pct: float = 0.01,
    labor_burden_pct: float = 0.40,
    material_tax_rate: float = 0.0,
) -> dict:
    """Calculate pricing from T&M entries with markup cascade.

    Cascade order:
      1. Direct costs (labor + burden, material + tax, equipment, sub)
      2. Overhead applied to direct cost subtotal
      3. Profit applied to (direct + overhead)
      4. Bond applied to (direct + overhead + profit)
    """
    labor_subtotal = sum(
        float(e.straight_hours or 0) * float(e.labor_rate or 0)
        + float(e.overtime_hours or 0) * float(e.ot_rate or 0)
        for e in entries
        if e.entry_type == "labor"
    )
    labor_burden = labor_subtotal * labor_burden_pct
    labor_total = labor_subtotal + labor_burden

    material_subtotal = sum(
        float(e.quantity or 0) * float(e.unit_cost or 0)
        for e in entries
        if e.entry_type == "material"
    )
    material_tax = material_subtotal * material_tax_rate
    material_total = material_subtotal + material_tax

    equipment_total = sum(
        float(e.equipment_hours or 0) * float(e.equipment_rate or 0)
        for e in entries
        if e.entry_type == "equipment"
    )

    sub_total = sum(float(e.sub_amount or 0) for e in entries if e.entry_type == "subcontractor")

    direct_cost_subtotal = labor_total + material_total + equipment_total + sub_total
    overhead_amount = direct_cost_subtotal * overhead_pct
    profit_amount = (direct_cost_subtotal + overhead_amount) * profit_pct
    bond_amount = (direct_cost_subtotal + overhead_amount + profit_amount) * bond_pct
    grand_total = direct_cost_subtotal + overhead_amount + profit_amount + bond_amount

    return {
        "labor_subtotal": round(labor_subtotal, 2),
        "labor_burden": round(labor_burden, 2),
        "labor_total": round(labor_total, 2),
        "material_subtotal": round(material_subtotal, 2),
        "material_tax": round(material_tax, 2),
        "material_total": round(material_total, 2),
        "equipment_total": round(equipment_total, 2),
        "sub_total": round(sub_total, 2),
        "direct_cost_subtotal": round(direct_cost_subtotal, 2),
        "overhead_amount": round(overhead_amount, 2),
        "profit_amount": round(profit_amount, 2),
        "bond_amount": round(bond_amount, 2),
        "grand_total": round(grand_total, 2),
    }


# ---------------------------------------------------------------------------
# T&M entry CRUD
# ---------------------------------------------------------------------------


async def add_tm_entry(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    change_event_id: uuid.UUID | None,
    data: dict,
    user_id: uuid.UUID | None = None,
) -> TmEntry:
    """Create a T&M entry and persist it."""
    entry = TmEntry(
        project_id=project_id,
        organization_id=org_id,
        change_event_id=change_event_id,
        entry_date=data.get("entry_date") or date.today(),
        entry_type=data["entry_type"],
        # Labor
        worker_name=data.get("worker_name"),
        classification=data.get("classification"),
        straight_hours=data.get("straight_hours"),
        overtime_hours=data.get("overtime_hours"),
        labor_rate=data.get("labor_rate"),
        ot_rate=data.get("ot_rate"),
        # Material
        material_description=data.get("material_description"),
        quantity=data.get("quantity"),
        unit=data.get("unit"),
        unit_cost=data.get("unit_cost"),
        vendor=data.get("vendor"),
        # Equipment
        equipment_type=data.get("equipment_type"),
        equipment_hours=data.get("equipment_hours"),
        equipment_rate=data.get("equipment_rate"),
        # Subcontractor
        sub_name=data.get("sub_name"),
        sub_scope=data.get("sub_scope"),
        sub_amount=data.get("sub_amount"),
        # Location / media
        gps_lat=data.get("gps_lat"),
        gps_lng=data.get("gps_lng"),
        photos=data.get("photos", []),
        voice_note_s3_key=data.get("voice_note_s3_key"),
        notes=data.get("notes"),
        created_by=user_id,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    logger.info(
        "T&M entry created: id=%s type=%s project=%s event=%s",
        entry.id,
        entry.entry_type,
        project_id,
        change_event_id,
    )
    return entry


async def list_tm_entries(
    db: AsyncSession,
    change_event_id: uuid.UUID,
) -> list[TmEntry]:
    """Return all T&M entries for a change event, ordered by date."""
    result = await db.execute(
        select(TmEntry)
        .where(TmEntry.change_event_id == change_event_id)
        .order_by(TmEntry.entry_date.asc(), TmEntry.created_at.asc())
    )
    return list(result.scalars().all())


async def get_tm_summary(
    db: AsyncSession,
    change_event_id: uuid.UUID,
) -> dict:
    """Aggregate T&M entries for a change event into subtotals."""
    entries = await list_tm_entries(db, change_event_id)

    labor_subtotal = sum(
        float(e.straight_hours or 0) * float(e.labor_rate or 0)
        + float(e.overtime_hours or 0) * float(e.ot_rate or 0)
        for e in entries
        if e.entry_type == "labor"
    )
    material_subtotal = sum(
        float(e.quantity or 0) * float(e.unit_cost or 0)
        for e in entries
        if e.entry_type == "material"
    )
    equipment_subtotal = sum(
        float(e.equipment_hours or 0) * float(e.equipment_rate or 0)
        for e in entries
        if e.entry_type == "equipment"
    )
    sub_subtotal = sum(float(e.sub_amount or 0) for e in entries if e.entry_type == "subcontractor")

    return {
        "labor_subtotal": round(labor_subtotal, 2),
        "material_subtotal": round(material_subtotal, 2),
        "equipment_subtotal": round(equipment_subtotal, 2),
        "sub_subtotal": round(sub_subtotal, 2),
        "entry_count": len(entries),
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# COR generation from T&M entries
# ---------------------------------------------------------------------------


async def generate_cor(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    change_event_id: uuid.UUID,
    subject: str | None = None,
    user_id: uuid.UUID | None = None,
) -> dict:
    """Generate a COR data dict from aggregated T&M entries.

    Computes pricing via ``calculate_pricing_summary``, attempts LLM-based
    scope narrative generation with a template fallback, and returns
    structured COR data ready for downstream use.
    """
    entries = await list_tm_entries(db, change_event_id)
    if not entries:
        raise ValueError("No T&M entries found for this change event")

    pricing = calculate_pricing_summary(entries)

    # Build scope narrative
    scope_narrative = _build_scope_narrative(entries, subject)

    # Attempt LLM-enhanced scope narrative (non-blocking)
    llm_narrative = None
    try:
        llm_narrative = await _generate_llm_narrative(entries, subject)
    except Exception:
        logger.debug("LLM narrative generation skipped or failed; using template")

    cor_data = {
        "project_id": str(project_id),
        "organization_id": str(org_id),
        "change_event_id": str(change_event_id),
        "subject": subject or f"T&M Change Order — {len(entries)} entries",
        "scope_narrative": llm_narrative or scope_narrative,
        "basis_of_claim": (
            f"Time and materials tracked in the field over "
            f"{len(set(e.entry_date for e in entries))} working day(s). "
            f"Total of {len(entries)} line item(s)."
        ),
        "pricing": pricing,
        "entry_count": len(entries),
        "generated_by": str(user_id) if user_id else None,
    }
    logger.info(
        "COR generated for event=%s grand_total=%.2f",
        change_event_id,
        pricing["grand_total"],
    )
    return cor_data


def _build_scope_narrative(entries: list[TmEntry], subject: str | None) -> str:
    """Build a template-based scope narrative from T&M entries."""
    sections: list[str] = []
    if subject:
        sections.append(f"Subject: {subject}")

    labor_entries = [e for e in entries if e.entry_type == "labor"]
    material_entries = [e for e in entries if e.entry_type == "material"]
    equipment_entries = [e for e in entries if e.entry_type == "equipment"]
    sub_entries = [e for e in entries if e.entry_type == "subcontractor"]

    if labor_entries:
        total_straight = sum(float(e.straight_hours or 0) for e in labor_entries)
        total_ot = sum(float(e.overtime_hours or 0) for e in labor_entries)
        workers = {e.worker_name for e in labor_entries if e.worker_name}
        sections.append(
            f"Labor: {len(labor_entries)} entries, "
            f"{total_straight:.1f} straight hours + {total_ot:.1f} OT hours, "
            f"{len(workers)} worker(s)."
        )

    if material_entries:
        descs = [e.material_description for e in material_entries if e.material_description]
        sections.append(
            f"Materials: {len(material_entries)} items"
            + (f" including {', '.join(descs[:3])}" if descs else "")
            + "."
        )

    if equipment_entries:
        types = {e.equipment_type for e in equipment_entries if e.equipment_type}
        sections.append(f"Equipment: {', '.join(types) if types else 'various'}.")

    if sub_entries:
        names = {e.sub_name for e in sub_entries if e.sub_name}
        sections.append(
            f"Subcontractors: {', '.join(names) if names else f'{len(sub_entries)} entry(ies)'}."
        )

    return "\n".join(sections) if sections else "T&M change order — see attached entries."


async def _generate_llm_narrative(
    entries: list[TmEntry],
    subject: str | None,
) -> str | None:
    """Try to generate a scope narrative via LLM. Returns None on failure."""
    try:
        from app.services.reliability.llm_gateway import get_llm_gateway
    except ImportError:
        return None

    summary_lines = []
    for e in entries[:20]:  # cap to avoid token overflow
        if e.entry_type == "labor":
            summary_lines.append(
                f"- Labor: {e.worker_name or 'worker'} "
                f"({e.straight_hours or 0}h + {e.overtime_hours or 0}h OT)"
            )
        elif e.entry_type == "material":
            summary_lines.append(
                f"- Material: {e.material_description or 'item'} "
                f"qty {e.quantity or 0} @ ${e.unit_cost or 0}/{e.unit or 'ea'}"
            )
        elif e.entry_type == "equipment":
            summary_lines.append(
                f"- Equipment: {e.equipment_type or 'unit'} "
                f"{e.equipment_hours or 0}h @ ${e.equipment_rate or 0}/hr"
            )
        elif e.entry_type == "subcontractor":
            summary_lines.append(
                f"- Sub: {e.sub_name or 'subcontractor'} — {e.sub_scope or 'scope TBD'} "
                f"${e.sub_amount or 0}"
            )

    prompt = (
        "You are a construction change order specialist. Write a concise scope narrative "
        "and basis of claim for a T&M change order based on these field entries.\n\n"
        f"Subject: {subject or 'T&M Work'}\n\n"
        "Entries:\n" + "\n".join(summary_lines) + "\n\n"
        "Write 2-3 professional paragraphs covering scope, justification, and work performed."
    )

    try:
        gateway = await get_llm_gateway()
        response = await gateway.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name="changeflow_narrative",
            temperature=0.3,
        )
        result = response.get("content", "")
        return result if isinstance(result, str) else str(result)
    except Exception:
        logger.debug("LLM call failed for scope narrative", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Negotiation tracking
# ---------------------------------------------------------------------------


async def record_negotiation(
    db: AsyncSession,
    cor_id: uuid.UUID,
    action: str,
    amount: float | None = None,
    notes: str | None = None,
    user_id: uuid.UUID | None = None,
) -> CorNegotiation:
    """Record a negotiation action on a COR."""
    negotiation = CorNegotiation(
        cor_id=cor_id,
        action=action,
        amount=Decimal(str(amount)) if amount is not None else None,
        notes=notes,
        acted_by=user_id,
    )
    db.add(negotiation)
    await db.flush()
    await db.refresh(negotiation)
    logger.info("Negotiation recorded: cor=%s action=%s amount=%s", cor_id, action, amount)
    return negotiation


async def list_negotiations(
    db: AsyncSession,
    cor_id: uuid.UUID,
) -> list[CorNegotiation]:
    """Return negotiation history for a COR, ordered chronologically."""
    result = await db.execute(
        select(CorNegotiation)
        .where(CorNegotiation.cor_id == cor_id)
        .order_by(CorNegotiation.acted_at.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Dashboard analytics
# ---------------------------------------------------------------------------


async def get_dashboard(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Aggregate ChangeFlow dashboard metrics for a project.

    - pending_value: total T&M cost for entries not yet linked to an approved COR
    - approved_to_date: sum of approved negotiation amounts
    - rejected_value: sum of rejected negotiation amounts
    - total_events: count of distinct change events with T&M entries
    - total_cors: count of distinct CORs with negotiations
    - avg_processing_days: average days between first and latest negotiation per COR
    """
    # Pending value — sum all T&M entries for this project
    # (entries linked to change events that don't have an approved negotiation)
    pending_result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(TmEntry.straight_hours * TmEntry.labor_rate, 0)
                    + func.coalesce(TmEntry.overtime_hours * TmEntry.ot_rate, 0)
                    + func.coalesce(TmEntry.quantity * TmEntry.unit_cost, 0)
                    + func.coalesce(TmEntry.equipment_hours * TmEntry.equipment_rate, 0)
                    + func.coalesce(TmEntry.sub_amount, 0)
                ),
                0,
            )
        ).where(TmEntry.project_id == project_id)
    )
    pending_value = float(pending_result.scalar_one() or 0)

    # Total distinct change events
    events_result = await db.execute(
        select(func.count(distinct(TmEntry.change_event_id))).where(
            TmEntry.project_id == project_id,
            TmEntry.change_event_id.is_not(None),
        )
    )
    total_events = events_result.scalar_one()

    # COR-level metrics via negotiations table
    # We join cor_negotiations through the project's change_event_ids
    cors_result = await db.execute(select(func.count(distinct(CorNegotiation.cor_id))))
    total_cors = cors_result.scalar_one()

    # Approved value
    approved_result = await db.execute(
        select(func.coalesce(func.sum(CorNegotiation.amount), 0)).where(
            CorNegotiation.action == "approved"
        )
    )
    approved_to_date = float(approved_result.scalar_one() or 0)

    # Rejected value
    rejected_result = await db.execute(
        select(func.coalesce(func.sum(CorNegotiation.amount), 0)).where(
            CorNegotiation.action == "rejected"
        )
    )
    rejected_value = float(rejected_result.scalar_one() or 0)

    # Average processing days (first negotiation to last per COR)
    # Subquery: min and max acted_at per cor_id
    processing_sub = (
        select(
            CorNegotiation.cor_id,
            func.min(CorNegotiation.acted_at).label("first_action"),
            func.max(CorNegotiation.acted_at).label("last_action"),
        )
        .group_by(CorNegotiation.cor_id)
        .having(func.count(CorNegotiation.id) > 1)
        .subquery()
    )
    avg_result = await db.execute(
        select(
            func.avg(
                func.extract("epoch", processing_sub.c.last_action)
                - func.extract("epoch", processing_sub.c.first_action)
            )
        )
    )
    avg_seconds = avg_result.scalar_one()
    avg_processing_days = round(float(avg_seconds) / 86400, 1) if avg_seconds else None

    return {
        "pending_value": round(pending_value, 2),
        "approved_to_date": round(approved_to_date, 2),
        "rejected_value": round(rejected_value, 2),
        "total_events": total_events,
        "total_cors": total_cors,
        "avg_processing_days": avg_processing_days,
    }
