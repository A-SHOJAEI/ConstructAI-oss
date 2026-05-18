"""Earned Value Management calculation engine."""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")


def _compute_earned_schedule(
    ev: Decimal,
    pv_curve: list[Decimal],
) -> Decimal | None:
    """Compute Earned Schedule from a cumulative PV curve.

    ES is the point in time at which the cumulative PV equals the current EV.
    Using linear interpolation between discrete periods:

        ES = AT + (EV - PV_t) / (PV_{t+1} - PV_t)

    where *AT* is the last complete period whose cumulative PV <= EV.

    Returns ``None`` when the PV curve is empty.
    """
    if not pv_curve:
        return None

    n = len(pv_curve)

    # If EV >= the final PV value, ES equals the full schedule length.
    if ev >= pv_curve[-1]:
        return Decimal(n)

    # Find the last period t where cumulative PV <= EV.
    at = 0
    for i in range(n):
        if pv_curve[i] <= ev:
            at = i + 1  # complete periods (1-based count)
        else:
            break

    # Interpolate the fractional period.
    if at >= n:
        return Decimal(n)

    pv_t = pv_curve[at - 1] if at > 0 else ZERO
    pv_t1 = pv_curve[at]
    delta = pv_t1 - pv_t
    if delta == ZERO:
        return Decimal(at)

    fraction = (ev - pv_t) / delta
    return Decimal(at) + fraction


def calculate_evm_metrics(
    bac: Decimal,
    pv: Decimal,
    ev: Decimal,
    ac: Decimal,
    *,
    planned_duration: int | None = None,
    current_period: int | None = None,
    pv_curve: list[Decimal] | None = None,
) -> dict:
    """Calculate full EVM metrics from base values.

    Parameters
    ----------
    bac: Budget at Completion
    pv: Planned Value
    ev: Earned Value
    ac: Actual Cost
    planned_duration:
        Project planned duration in periods (used for ES estimation when
        *pv_curve* is not supplied).
    current_period:
        Current reporting period — the actual time elapsed (AT).  Required
        for Earned Schedule metrics.
    pv_curve:
        Cumulative Planned Value by period (index 0 = end of period 1).
        When provided, Earned Schedule is computed via interpolation on
        this curve.  When absent and *planned_duration* is given, ES is
        estimated as ``AT * SPI`` (where ``AT = planned_duration * EV/BAC``).

    Returns
    -------
    Dict with all derived EVM metrics and an ``is_valid`` flag.
    Metrics that cannot be computed (e.g. SPI when PV is zero) are
    returned as ``None`` instead of a misleading default.

    New Earned-Schedule keys
    ~~~~~~~~~~~~~~~~~~~~~~~~
    es        – Earned Schedule (time units)
    sv_t      – Schedule Variance (time-based): ES - AT
    spi_t     – Schedule Performance Index (time-based): ES / AT
    tcpi_bac  – To-Complete Performance Index against BAC
    tcpi_eac  – To-Complete Performance Index against EAC
    ieac      – Independent Estimate at Completion (composite)

    Raises
    ------
    ValueError
        If BAC is not positive.
    """
    if bac <= ZERO:
        raise ValueError("BAC must be positive")

    if pv < ZERO or ev < ZERO or ac < ZERO:
        raise ValueError("PV, EV, and AC must be non-negative")

    sv = ev - pv  # Schedule Variance
    cv = ev - ac  # Cost Variance

    warnings: list[str] = []

    # M-22: SPI/CPI default to 1.0 when inputs are zero so numeric callers
    # (dashboards, alerts) don't crash. But "1.0" looks like "on track" on
    # a dashboard, which is the opposite of reality when the project
    # hasn't started. Also set `is_provisional` so thoughtful consumers
    # know the ratio is synthetic — legacy callers keep the old behavior.
    is_provisional_spi = pv == ZERO
    is_provisional_cpi = ac == ZERO

    if not is_provisional_spi:
        spi = ev / pv
    else:
        spi = ONE
        warnings.append("SPI is synthetic (PV=0) — treat as unavailable until work is planned")
        logger.warning("SPI defaulted to 1.0 (PV=0) — project may not have started yet")

    if not is_provisional_cpi:
        cpi = ev / ac
    else:
        cpi = ONE
        warnings.append("CPI is synthetic (AC=0) — treat as unavailable until costs post")
        logger.warning("CPI defaulted to 1.0 (AC=0) — no actual cost incurred yet")

    # Determine whether we have sufficient data for downstream metrics.
    # A snapshot is only "valid" for dashboards/alerting when both SPI and
    # CPI were computed from real numbers, not the 1.0 fallback.
    is_valid = not (is_provisional_spi or is_provisional_cpi)

    # EAC using CPI method: BAC / CPI
    if cpi is not None and cpi != ZERO:
        eac = bac / cpi
    else:
        eac = None
        if cpi == ZERO:
            warnings.append("EAC is undefined because CPI is zero")
            logger.warning("EAC cannot be computed: CPI is zero")
    etc = eac - ac if eac is not None else None
    vac = bac - eac if eac is not None else None

    # TCPI: remaining work / remaining budget (original — to BAC)
    remaining_work = bac - ev
    remaining_budget = bac - ac
    tcpi = remaining_work / remaining_budget if remaining_budget != ZERO else None

    percent_complete = (ev / bac * 100) if bac != ZERO else ZERO

    # ------------------------------------------------------------------
    # Earned Schedule metrics
    # ------------------------------------------------------------------
    es: Decimal | None = None
    sv_t: Decimal | None = None
    spi_t: Decimal | None = None

    # Determine AT (actual time / current period)
    at: Decimal | None = None
    if current_period is not None:
        at = Decimal(current_period)

    # --- Earned Schedule (ES) ---
    if pv_curve is not None and pv_curve:
        es = _compute_earned_schedule(ev, pv_curve)
    elif planned_duration is not None and spi is not None:
        # Fallback: estimate ES from SPI.
        # AT = planned_duration * (EV / BAC) gives the "expected" elapsed
        # time; ES = AT * SPI is the time-based progress estimate.
        estimated_at = Decimal(planned_duration) * (ev / bac) if bac != ZERO else None
        if estimated_at is not None:
            es = estimated_at * spi

    # --- SV(t) and SPI(t) ---
    if es is not None and at is not None:
        sv_t = es - at
        spi_t = es / at if at != ZERO else None

    # ------------------------------------------------------------------
    # TCPI variants
    # ------------------------------------------------------------------
    # TCPI_BAC: performance needed to finish within BAC
    tcpi_bac = remaining_work / remaining_budget if remaining_budget != ZERO else None

    # TCPI_EAC: performance needed to finish within EAC
    if eac is not None:
        eac_remaining = eac - ac
        tcpi_eac = remaining_work / eac_remaining if eac_remaining != ZERO else None
    else:
        tcpi_eac = None

    # ------------------------------------------------------------------
    # IEAC: Independent Estimate at Completion (composite SPI*CPI method)
    # IEAC = AC + (BAC - EV) / (SPI * CPI)
    # ------------------------------------------------------------------
    ieac: Decimal | None = None
    if spi is not None and cpi is not None:
        composite = spi * cpi
        if composite != ZERO:
            ieac = ac + remaining_work / composite
        else:
            warnings.append("IEAC is undefined because SPI*CPI is zero")
            logger.warning("IEAC cannot be computed: SPI*CPI composite denominator is zero")

    return {
        "sv": round(sv, 2),
        "cv": round(cv, 2),
        "spi": round(spi, 4) if spi is not None else None,
        "cpi": round(cpi, 4) if cpi is not None else None,
        "eac": round(eac, 2) if eac is not None else None,
        "etc": round(etc, 2) if etc is not None else None,
        "vac": round(vac, 2) if vac is not None else None,
        "tcpi": round(tcpi, 4) if tcpi is not None else None,
        "percent_complete": round(percent_complete, 2),
        "is_valid": is_valid,
        # Earned Schedule metrics
        "es": round(es, 4) if es is not None else None,
        "sv_t": round(sv_t, 4) if sv_t is not None else None,
        "spi_t": round(spi_t, 4) if spi_t is not None else None,
        # TCPI variants
        "tcpi_bac": round(tcpi_bac, 4) if tcpi_bac is not None else None,
        "tcpi_eac": round(tcpi_eac, 4) if tcpi_eac is not None else None,
        # Independent EAC (composite method)
        "ieac": round(ieac, 2) if ieac is not None else None,
        # Warnings for edge cases (e.g. PV=0, AC=0)
        "warnings": warnings if warnings else [],
    }


async def compute_evm_snapshot(
    bac: Decimal,
    pv: Decimal,
    ev: Decimal,
    ac: Decimal,
    *,
    planned_duration: int | None = None,
    current_period: int | None = None,
    pv_curve: list[Decimal] | None = None,
) -> dict:
    """Async wrapper for EVM computation.

    Parameters
    ----------
    bac, pv, ev, ac:
        Standard EVM base values.
    planned_duration:
        Project planned duration in periods (optional, for ES estimation).
    current_period:
        Current reporting period / actual time elapsed (optional, for ES).
    pv_curve:
        Cumulative PV by period for precise Earned Schedule calculation.
    """
    metrics = calculate_evm_metrics(
        bac,
        pv,
        ev,
        ac,
        planned_duration=planned_duration,
        current_period=current_period,
        pv_curve=pv_curve,
    )
    logger.info(
        "EVM snapshot computed: SPI=%s, CPI=%s, EAC=%s, ES=%s, SPI_t=%s, is_valid=%s",
        metrics["spi"],
        metrics["cpi"],
        metrics["eac"],
        metrics["es"],
        metrics["spi_t"],
        metrics["is_valid"],
    )
    return metrics
