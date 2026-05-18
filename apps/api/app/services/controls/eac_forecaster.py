"""Estimate at Completion forecasting with multiple methods."""

from __future__ import annotations

import logging
import math
from decimal import Decimal

logger = logging.getLogger(__name__)


def _compute_std_dev(values: list[Decimal]) -> Decimal:
    """Compute standard deviation of a list of Decimal values."""
    if len(values) < 2:
        return Decimal("0")
    n = len(values)
    mean: Decimal = sum(values, Decimal(0)) / n
    variance: Decimal = sum(((v - mean) ** 2 for v in values), Decimal(0)) / Decimal(str(n - 1))
    # Use float sqrt then convert back to Decimal
    std = Decimal(str(math.sqrt(float(variance))))
    return std


def _compute_confidence_interval(
    eac: Decimal,
    bac: Decimal,
    ev: Decimal,
    historical_cpi_values: list[Decimal] | None = None,
    project_type: str | None = None,
) -> tuple[Decimal, Decimal]:
    """Compute data-aware confidence interval for an EAC estimate.

    Uses a tiered strategy based on data availability:

    1. **>= 20 snapshots**: uses only project-specific data (statistical CI).
    2. **5-19 snapshots**: uses project-specific data (statistical CI).
    3. **< 5 snapshots**: uses industry benchmarks for the project type
       (if available), falling back to phase-aware defaults.

    Statistical CI uses error propagation::

        std_eac = (BAC / cpi_mean) * (cpi_std / cpi_mean) / sqrt(n)
        margin  = 1.96 * std_eac

    Phase-aware defaults::

        early  (EV/BAC < 0.2) = +/- 25%
        mid    (0.2 - 0.7)    = +/- 15%
        late   (> 0.7)        = +/- 8%
    """
    n = len(historical_cpi_values) if historical_cpi_values else 0

    # Tier 1 & 2: >= 5 historical data points — use project-specific stats
    if n >= 5:
        cpi_mean = sum(historical_cpi_values) / Decimal(str(n))  # type: ignore[arg-type]
        cpi_std = _compute_std_dev(historical_cpi_values)  # type: ignore[arg-type]
        z = Decimal("1.96")
        cpi_mean_safe: Decimal = cpi_mean if cpi_mean != Decimal("0") else Decimal("1")
        sqrt_n = Decimal(str(math.sqrt(n)))
        std_eac = (bac / cpi_mean_safe) * (cpi_std / cpi_mean_safe) / sqrt_n
        margin = z * std_eac

    # Tier 3 (< 5 snapshots): try industry benchmarks
    elif project_type:
        from app.services.controls.industry_benchmarks import CPI_BENCHMARKS

        benchmark = CPI_BENCHMARKS.get(project_type.lower())
        if benchmark:
            # Use benchmark CPI distribution
            cpi_mean_bm = Decimal(str(benchmark["mean"]))
            cpi_std_bm = Decimal(str(benchmark["std"]))
            cpi_mean_safe_bm: Decimal = cpi_mean_bm if cpi_mean_bm != Decimal("0") else Decimal("1")
            z = Decimal("1.96")
            # Treat as n=30 equivalent (population benchmark)
            sqrt_n = Decimal(str(math.sqrt(30)))
            std_eac = (bac / cpi_mean_safe_bm) * (cpi_std_bm / cpi_mean_safe_bm) / sqrt_n
            margin = z * std_eac
        else:
            margin = _phase_aware_margin(eac, bac, ev)

    # Fallback: 2-4 CPI values — try stats, otherwise phase-aware
    elif n >= 2:
        cpi_mean = sum(historical_cpi_values) / Decimal(str(n))  # type: ignore[arg-type]
        cpi_std = _compute_std_dev(historical_cpi_values)  # type: ignore[arg-type]
        z = Decimal("1.96")
        cpi_mean_safe = cpi_mean if cpi_mean != Decimal("0") else Decimal("1")
        sqrt_n = Decimal(str(math.sqrt(n)))
        std_eac = (bac / cpi_mean_safe) * (cpi_std / cpi_mean_safe) / sqrt_n
        margin = z * std_eac
    else:
        margin = _phase_aware_margin(eac, bac, ev)

    confidence_low = eac - margin
    confidence_high = eac + margin
    return confidence_low, confidence_high


def _phase_aware_margin(eac: Decimal, bac: Decimal, ev: Decimal) -> Decimal:
    """Return a phase-aware default margin for EAC confidence intervals."""
    completion_pct = ev / bac if bac > 0 else Decimal("0")

    if completion_pct < Decimal("0.2"):
        margin_pct = Decimal("0.25")
    elif completion_pct <= Decimal("0.7"):
        margin_pct = Decimal("0.15")
    else:
        margin_pct = Decimal("0.08")

    return margin_pct * eac


def _compute_trend_weighted_eac(
    bac: Decimal,
    historical_cpi_values: list[Decimal] | None = None,
    cpi: Decimal | None = None,
) -> Decimal:
    """Compute trend-weighted EAC using recent CPI values.

    Uses last 3 CPI values with weights [0.5, 0.3, 0.2] (most recent first).
    Falls back to simple BAC/CPI if insufficient history.
    """
    zero = Decimal("0")

    if historical_cpi_values and len(historical_cpi_values) >= 3:
        recent = historical_cpi_values[-3:]  # last 3 values
        weights = [Decimal("0.2"), Decimal("0.3"), Decimal("0.5")]
        weighted_cpi = sum(w * v for w, v in zip(weights, recent, strict=False))
        if weighted_cpi != zero:
            return bac / weighted_cpi
    elif historical_cpi_values and len(historical_cpi_values) >= 1:
        # Use available values with adjusted weights
        n = len(historical_cpi_values)
        weights = [Decimal("0.4"), Decimal("0.6")] if n == 2 else [Decimal("1")]
        weighted_cpi = sum(w * v for w, v in zip(weights, historical_cpi_values[-n:], strict=False))
        if weighted_cpi != zero:
            return bac / weighted_cpi

    # Fallback to simple CPI
    if cpi and cpi != zero:
        return bac / cpi
    return bac


async def forecast_eac(
    bac: Decimal,
    ev: Decimal,
    ac: Decimal,
    spi: Decimal,
    cpi: Decimal,
    method: str = "cpi",
    historical_cpi_values: list[Decimal] | None = None,
    management_reserve_pct: Decimal | None = None,
    project_type: str | None = None,
) -> dict:
    """Forecast EAC using the specified method.

    Supported methods:
    - cpi: BAC / CPI
    - spi_cpi: AC + (BAC - EV) / (CPI * SPI)
    - remaining_work: AC + (BAC - EV)
    - mgmt_estimate: base_eac * (1 + management_reserve_pct)
    - trend_weighted: BAC / weighted_CPI (recent CPI weighted)
    - all: returns all 5 methods side-by-side

    Parameters
    ----------
    bac: Budget at Completion
    ev: Earned Value
    ac: Actual Cost
    spi: Schedule Performance Index
    cpi: Cost Performance Index
    method: Forecasting method to use
    historical_cpi_values: Optional list of historical CPI values for
        data-aware confidence intervals and trend-weighted EAC.
    management_reserve_pct: Optional management reserve percentage
        (default 0.10 = 10%). Used by mgmt_estimate method.
    project_type: Optional project type (e.g. "commercial", "healthcare")
        for industry benchmark fallback when historical data is sparse.

    Returns dict with eac_value, confidence_low, confidence_high.
    """
    zero = Decimal("0")
    one = Decimal("1")

    if management_reserve_pct is None:
        management_reserve_pct = Decimal("0.10")

    # Return all methods side-by-side
    if method == "all":
        all_results = {}
        for m in ["cpi", "spi_cpi", "remaining_work", "mgmt_estimate", "trend_weighted"]:
            result = await forecast_eac(
                bac=bac,
                ev=ev,
                ac=ac,
                spi=spi,
                cpi=cpi,
                method=m,
                historical_cpi_values=historical_cpi_values,
                management_reserve_pct=management_reserve_pct,
                project_type=project_type,
            )
            all_results[m] = result

        return {
            "method": "all",
            "all_methods": all_results,
            "model_params": {
                "bac": str(bac),
                "cpi": str(cpi),
                "spi": str(spi),
            },
        }

    if method == "cpi":
        eac = bac / cpi if cpi != zero else bac
    elif method == "spi_cpi":
        composite = cpi * spi if (cpi * spi) != zero else one
        eac = ac + (bac - ev) / composite
    elif method == "remaining_work":
        eac = ac + (bac - ev)
    elif method == "mgmt_estimate":
        base_eac = bac / cpi if cpi != zero else bac
        eac = base_eac * (one + management_reserve_pct)
    elif method == "trend_weighted":
        eac = _compute_trend_weighted_eac(bac, historical_cpi_values, cpi)
    else:
        eac = bac / cpi if cpi != zero else bac

    # Data-aware confidence interval
    confidence_low, confidence_high = _compute_confidence_interval(
        eac=eac,
        bac=bac,
        ev=ev,
        historical_cpi_values=historical_cpi_values,
        project_type=project_type,
    )

    result = {
        "method": method,
        "eac_value": round(eac, 2),
        "confidence_low": round(confidence_low, 2),
        "confidence_high": round(confidence_high, 2),
        "model_params": {
            "bac": str(bac),
            "cpi": str(cpi),
            "spi": str(spi),
        },
    }

    logger.info(
        "EAC forecast (%s): %.2f [%.2f - %.2f]",
        method,
        eac,
        confidence_low,
        confidence_high,
    )
    return result
