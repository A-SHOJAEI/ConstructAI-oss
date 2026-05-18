"""Server-side math validation for AIA G702/G703 pay applications.

All functions are pure (no DB access) for easy testing.
Every computed field on the G702 and G703 forms is calculated here.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def _round2(value: Decimal) -> Decimal:
    """Round to 2 decimal places (money)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _round4(value: Decimal) -> Decimal:
    """Round to 4 decimal places (percentages)."""
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def compute_pco_total(
    labor: Decimal,
    material: Decimal,
    equipment: Decimal,
    subcontractor: Decimal,
    overhead: Decimal,
    profit_markup_pct: Decimal,
) -> Decimal:
    """Compute PCO total cost from breakdown.

    total = (labor + material + equipment + subcontractor + overhead) * (1 + markup_pct/100)
    """
    subtotal = labor + material + equipment + subcontractor + overhead
    markup_factor = Decimal("1") + profit_markup_pct / HUNDRED
    return _round2(subtotal * markup_factor)


def compute_g703_line(
    scheduled_value: Decimal,
    work_completed_previous: Decimal,
    work_completed_this_period: Decimal,
    materials_presently_stored: Decimal,
) -> dict[str, Decimal]:
    """Compute G703 derived columns for a single line item.

    Returns:
        total_completed_and_stored: D + E + F  (Column G)
        percent_complete: G / C * 100          (Column H, zero-division safe)
        balance_to_finish: C - G               (Column I)
    """
    if scheduled_value < ZERO:
        raise ValueError("scheduled_value must be non-negative")
    if work_completed_previous < ZERO:
        raise ValueError("work_completed_previous must be non-negative")
    if work_completed_this_period < ZERO:
        raise ValueError("work_completed_this_period must be non-negative")
    if materials_presently_stored < ZERO:
        raise ValueError("materials_presently_stored must be non-negative")

    # Column G = D + E + F
    total = work_completed_previous + work_completed_this_period + materials_presently_stored
    total = _round2(total)

    # Column H = G / C * 100 (zero-division safe)
    pct = total / scheduled_value * HUNDRED if scheduled_value != ZERO else ZERO
    pct = _round4(pct)

    # Column I = C - G
    balance = _round2(scheduled_value - total)

    return {
        "total_completed_and_stored": total,
        "percent_complete": pct,
        "balance_to_finish": balance,
    }


def compute_g702_totals(
    line_items: list[dict],
    retainage_pct: Decimal,
    less_previous_certificates: Decimal,
    original_contract_sum: Decimal,
    net_change_by_cos: Decimal,
) -> dict[str, Decimal]:
    """Compute all G702 summary fields from G703 line items.

    Each line_item dict must have:
        scheduled_value, work_completed_previous, work_completed_this_period,
        materials_presently_stored, retainage_pct (optional per-line override)

    Returns dict with all G702 computed fields.
    """
    if retainage_pct < ZERO or retainage_pct > HUNDRED:
        raise ValueError("retainage_pct must be between 0 and 100")

    # Line 3: Contract Sum to Date = Original + Net Change
    contract_sum_to_date = _round2(original_contract_sum + net_change_by_cos)

    total_completed_and_stored = ZERO
    retainage_work = ZERO
    retainage_stored = ZERO

    for li in line_items:
        computed = compute_g703_line(
            li["scheduled_value"],
            li["work_completed_previous"],
            li["work_completed_this_period"],
            li["materials_presently_stored"],
        )
        total_completed_and_stored += computed["total_completed_and_stored"]

        # Per-line retainage rate, falling back to header-level
        line_ret_raw = li.get("retainage_pct")
        if line_ret_raw is not None:
            # Validate per-line retainage override
            try:
                line_ret_raw = Decimal(str(line_ret_raw))
            except Exception:
                raise ValueError(
                    f"retainage_pct for line item {li.get('item_number', '?')} "
                    f"must be numeric, got: {li.get('retainage_pct')!r}"
                )
            if line_ret_raw < ZERO or line_ret_raw > HUNDRED:
                raise ValueError(
                    f"retainage_pct for line item {li.get('item_number', '?')} "
                    f"must be between 0 and 100, got: {line_ret_raw}"
                )
        line_ret_pct = (line_ret_raw if line_ret_raw is not None else retainage_pct) / HUNDRED
        line_work = li["work_completed_previous"] + li["work_completed_this_period"]
        retainage_work += line_work * line_ret_pct
        retainage_stored += li["materials_presently_stored"] * line_ret_pct

    total_completed_and_stored = _round2(total_completed_and_stored)
    retainage_work = _round2(retainage_work)
    retainage_stored = _round2(retainage_stored)

    # Line 5: Total Retainage
    total_retainage = _round2(retainage_work + retainage_stored)

    # Line 6: Total Earned Less Retainage = Line 4 - Line 5
    total_earned_less_retainage = _round2(total_completed_and_stored - total_retainage)

    # Line 8: Current Payment Due = Line 6 - Line 7
    current_payment_due = _round2(total_earned_less_retainage - less_previous_certificates)

    # Line 9: Balance to Finish, Including Retainage = Line 3 - Line 4 + Line 5
    balance_to_finish_including_retainage = _round2(
        contract_sum_to_date - total_completed_and_stored + total_retainage
    )

    return {
        "contract_sum_to_date": contract_sum_to_date,
        "total_completed_and_stored": total_completed_and_stored,
        "retainage_work_completed": retainage_work,
        "retainage_stored_materials": retainage_stored,
        "total_retainage": total_retainage,
        "total_earned_less_retainage": total_earned_less_retainage,
        "current_payment_due": current_payment_due,
        "balance_to_finish_including_retainage": balance_to_finish_including_retainage,
    }


def validate_no_overbilling(
    line_items: list[dict],
) -> list[dict]:
    """Check that no line item's total_completed_and_stored exceeds scheduled_value.

    Returns a list of overbilling warnings (empty = valid).
    Each warning: {item_number, scheduled, billed, excess}
    """
    warnings = []
    for li in line_items:
        computed = compute_g703_line(
            li["scheduled_value"],
            li["work_completed_previous"],
            li["work_completed_this_period"],
            li["materials_presently_stored"],
        )
        if computed["total_completed_and_stored"] > li["scheduled_value"]:
            excess = _round2(computed["total_completed_and_stored"] - li["scheduled_value"])
            warnings.append(
                {
                    "item_number": li["item_number"],
                    "scheduled": li["scheduled_value"],
                    "billed": computed["total_completed_and_stored"],
                    "excess": excess,
                }
            )
    return warnings
