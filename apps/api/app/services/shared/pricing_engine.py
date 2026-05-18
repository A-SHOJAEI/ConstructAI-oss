"""Pricing engine for change order and T&M markup calculations.

All functions are pure (no DB access) — the pricing configuration is
passed in as a ``ProjectPricingConfig`` instance.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from app.models.pricing_config import ProjectPricingConfig

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _r2(value: Decimal) -> Decimal:
    """Round to 2 decimal places (money)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class PricingSummary(BaseModel):
    """Fully cascaded pricing breakdown."""

    labor_subtotal: Decimal
    labor_burden: Decimal
    labor_total: Decimal
    material_subtotal: Decimal
    material_tax: Decimal
    material_total: Decimal
    equipment_total: Decimal
    sub_total: Decimal
    direct_cost_subtotal: Decimal
    overhead_amount: Decimal
    profit_amount: Decimal
    bond_amount: Decimal
    grand_total: Decimal


def calculate_pricing(
    entries: list[dict],
    config: ProjectPricingConfig,
) -> PricingSummary:
    """Aggregate *entries* and apply the markup cascade from *config*.

    Each entry dict must contain a ``"type"`` key (one of ``"labor"``,
    ``"material"``, ``"equipment"``, ``"subcontractor"``).

    Labor entries: ``straight_hours``, ``rate``, and optionally
    ``overtime_hours``, ``ot_rate``.

    Material entries: ``quantity``, ``unit_cost``.

    Equipment entries: ``hours``, ``rate``.

    Subcontractor entries: ``amount``.

    The cascade order is:

    1. Compute category subtotals.
    2. Labor burden on labor subtotal.
    3. Material tax on material subtotal.
    4. Direct cost = labor_total + material_total + equipment + sub.
    5. Overhead on direct cost.
    6. Profit on (direct + overhead).
    7. Bond on (direct + overhead + profit).
    """
    labor_subtotal = ZERO
    material_subtotal = ZERO
    equipment_total = ZERO
    sub_total = ZERO

    for entry in entries:
        entry_type = entry.get("type", "")
        if entry_type == "labor":
            straight = Decimal(str(entry.get("straight_hours", 0)))
            rate = Decimal(str(entry.get("rate", 0)))
            ot_hours = Decimal(str(entry.get("overtime_hours", 0)))
            ot_rate = Decimal(str(entry.get("ot_rate", 0)))
            labor_subtotal += straight * rate + ot_hours * ot_rate
        elif entry_type == "material":
            qty = Decimal(str(entry.get("quantity", 0)))
            unit_cost = Decimal(str(entry.get("unit_cost", 0)))
            material_subtotal += qty * unit_cost
        elif entry_type == "equipment":
            hours = Decimal(str(entry.get("hours", 0)))
            rate = Decimal(str(entry.get("rate", 0)))
            equipment_total += hours * rate
        elif entry_type == "subcontractor":
            amount = Decimal(str(entry.get("amount", 0)))
            sub_total += amount

    labor_subtotal = _r2(labor_subtotal)
    material_subtotal = _r2(material_subtotal)
    equipment_total = _r2(equipment_total)
    sub_total = _r2(sub_total)

    # Burden & tax
    labor_burden = _r2(labor_subtotal * config.labor_burden_pct)
    labor_total = _r2(labor_subtotal + labor_burden)

    material_tax = _r2(material_subtotal * config.material_tax_rate)
    material_total = _r2(material_subtotal + material_tax)

    # Direct cost
    direct_cost_subtotal = _r2(labor_total + material_total + equipment_total + sub_total)

    # Overhead, profit, bond cascade
    overhead_amount = _r2(direct_cost_subtotal * config.overhead_pct)
    profit_amount = _r2((direct_cost_subtotal + overhead_amount) * config.profit_pct)
    bond_amount = _r2((direct_cost_subtotal + overhead_amount + profit_amount) * config.bond_pct)

    grand_total = _r2(direct_cost_subtotal + overhead_amount + profit_amount + bond_amount)

    return PricingSummary(
        labor_subtotal=labor_subtotal,
        labor_burden=labor_burden,
        labor_total=labor_total,
        material_subtotal=material_subtotal,
        material_tax=material_tax,
        material_total=material_total,
        equipment_total=equipment_total,
        sub_total=sub_total,
        direct_cost_subtotal=direct_cost_subtotal,
        overhead_amount=overhead_amount,
        profit_amount=profit_amount,
        bond_amount=bond_amount,
        grand_total=grand_total,
    )
