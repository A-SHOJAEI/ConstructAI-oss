"""Monte Carlo simulation for risk-adjusted construction cost estimates."""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)


def _to_money(value) -> str:
    """Convert a numeric value to a 2-decimal string for monetary precision."""
    return str(Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False
    logger.warning("numpy not installed; Monte Carlo simulation unavailable")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_MAX_SEED = 2**32 - 1  # numpy default_rng accepts 0..2^32-1 for reproducibility


def _validate_seed(seed: int | None) -> int | None:
    """Validate random seed is within a safe range for numpy."""
    if seed is None:
        return None
    if not isinstance(seed, int) or seed < 0 or seed > _MAX_SEED:
        raise ValueError(f"seed must be an integer between 0 and {_MAX_SEED}")
    return seed


async def run_monte_carlo(
    line_items: list[dict],
    num_simulations: int = 10000,
    contingency_pct: float = 10.0,
    seed: int | None = None,
    *,
    org_id: str | None = None,
) -> dict:
    """Run Monte Carlo simulation on estimate line items.

    Each line item uses a PERT Beta distribution (via scipy) or triangular
    distribution based on per-item bounds. Default bounds:
    - min: unit_cost * 0.85 (or custom ``cost_min``)
    - mode: unit_cost
    - max: unit_cost * 1.30 (or custom ``cost_max``)

    Contingency is reported as a separate line item alongside the
    risk-adjusted total (not multiplied on top) to avoid double-counting.

    Returns dict with: p10, p50, p80, p90, mean, std_dev,
    histogram_data (50 bins), num_simulations, contingency_pct,
    contingency_amount, base_estimate.

    Args:
        org_id: H-9 — organization ID that owns ``line_items``. Required
            for production callers so audit logs can attribute the run;
            optional in tests. Emits a WARNING when omitted so missing
            scoping is visible without breaking historical callers.
    """
    # Clamp iterations: at least 1, at most 100k to prevent resource exhaustion
    num_simulations = max(1, min(num_simulations, 100_000))
    seed = _validate_seed(seed)

    # H-9: Tenant scoping is enforced at the route layer (the caller must
    # verify line_items belong to org_id's projects). Log the claimed org
    # so audit trails show which tenant initiated the simulation.
    if org_id:
        logger.info(
            "Monte Carlo run for org=%s items=%d sims=%d",
            org_id,
            len(line_items),
            num_simulations,
        )
    else:
        logger.warning(
            "Monte Carlo run missing org_id — production callers MUST pass "
            "org_id for audit scoping. items=%d sims=%d",
            len(line_items),
            num_simulations,
        )

    if not _HAS_NUMPY:
        raise RuntimeError(
            "numpy is required for Monte Carlo simulation. Install with: pip install numpy"
        )

    if not line_items:
        return {
            "p10": 0.0,
            "p50": 0.0,
            "p80": 0.0,
            "p90": 0.0,
            "mean": 0.0,
            "std_dev": 0.0,
            "histogram_data": [],
            "num_simulations": num_simulations,
            "contingency_pct": contingency_pct,
            "contingency_amount": 0.0,
            "base_estimate": 0.0,
        }

    rng = np.random.default_rng(seed=seed)
    n_items = len(line_items)

    # Build per-item parameter arrays
    quantities = np.array([float(item.get("quantity", 1)) for item in line_items])
    unit_costs = np.array([float(item.get("unit_cost", 0)) for item in line_items])

    # Per-item distribution parameters: use material-specific uncertainty
    # ranges from cost_database when cost_min/cost_max are not explicit.
    try:
        from app.services.estimating.cost_database import get_uncertainty_range

        _has_uncertainty = True
    except ImportError:
        _has_uncertainty = False

    tri_min_list: list[float] = []
    tri_max_list: list[float] = []
    for i, item in enumerate(line_items):
        uc = unit_costs[i]
        if "cost_min" in item:
            tri_min_list.append(float(item["cost_min"]))
        elif _has_uncertainty:
            category = item.get("category", item.get("description", "default"))
            low_pct, _ = get_uncertainty_range(category)
            tri_min_list.append(uc * (1.0 - low_pct))
        else:
            tri_min_list.append(uc * 0.85)

        if "cost_max" in item:
            tri_max_list.append(float(item["cost_max"]))
        elif _has_uncertainty:
            category = item.get("category", item.get("description", "default"))
            _, high_pct = get_uncertainty_range(category)
            tri_max_list.append(uc * (1.0 + high_pct))
        else:
            tri_max_list.append(uc * 1.30)

    tri_min = np.array(tri_min_list)
    tri_mode = unit_costs
    tri_max = np.array(tri_max_list)

    # Try PERT Beta distribution, fall back to triangular
    try:
        from scipy.stats import beta as scipy_beta

        _use_pert = True
    except ImportError:
        _use_pert = False

    # Vectorized simulation: shape (num_simulations, n_items)
    sampled_costs = np.empty((num_simulations, n_items))
    for i in range(n_items):
        if tri_min[i] >= tri_max[i]:
            # Degenerate case: fixed cost
            sampled_costs[:, i] = tri_mode[i]
        elif (tri_max[i] - tri_min[i]) < 0.01 * abs(tri_mode[i] or 1.0):
            # Near-degenerate: range too narrow for stable PERT, use fixed
            sampled_costs[:, i] = tri_mode[i]
        elif _use_pert:
            range_val = tri_max[i] - tri_min[i]
            lam = 4.0
            alpha = 1.0 + lam * (tri_mode[i] - tri_min[i]) / range_val
            beta_param = 1.0 + lam * (tri_max[i] - tri_mode[i]) / range_val
            sampled_costs[:, i] = tri_min[i] + range_val * scipy_beta.rvs(  # pyright: ignore[reportPossiblyUnbound]
                alpha, beta_param, size=num_simulations, random_state=rng
            )
        else:
            sampled_costs[:, i] = rng.triangular(
                tri_min[i], tri_mode[i], tri_max[i], size=num_simulations
            )

    # Total cost per simulation = sum of (sampled_unit_cost * quantity) across items
    total_costs = np.sum(sampled_costs * quantities, axis=1)

    # Base (deterministic) estimate for reference
    base_estimate = float(np.sum(unit_costs * quantities))

    # Contingency is a separate additive amount, NOT a multiplier on Monte Carlo results
    # This avoids double-counting risk that is already captured by the simulation
    contingency_amount = round(base_estimate * contingency_pct / 100.0, 2)

    # Compute statistics (from risk-adjusted simulation, without contingency)
    p10 = float(np.percentile(total_costs, 10))
    p50 = float(np.percentile(total_costs, 50))
    p80 = float(np.percentile(total_costs, 80))
    p90 = float(np.percentile(total_costs, 90))
    mean = float(np.mean(total_costs))
    std_dev = float(np.std(total_costs))

    # Histogram with 50 bins
    counts, _ = np.histogram(total_costs, bins=50)
    histogram_data = [int(c) for c in counts]

    logger.info(
        "Monte Carlo complete: %d sims, mean=$%.2f, p50=$%.2f, p90=$%.2f",
        num_simulations,
        mean,
        p50,
        p90,
    )

    return {
        "p10": _to_money(p10),
        "p50": _to_money(p50),
        "p80": _to_money(p80),
        "p90": _to_money(p90),
        "mean": _to_money(mean),
        "std_dev": _to_money(std_dev),
        "histogram_data": histogram_data,
        "num_simulations": num_simulations,
        "contingency_pct": contingency_pct,
        "contingency_amount": _to_money(contingency_amount),
        "base_estimate": _to_money(base_estimate),
        "total_with_contingency": _to_money(base_estimate + contingency_amount),
    }


async def sensitivity_analysis(line_items: list[dict], num_simulations: int = 5000) -> list[dict]:
    """Identify which line items contribute most to cost variance.

    Returns list sorted by impact, each with: description, csi_code,
    correlation_coefficient, contribution_pct, base_cost.
    """
    # Clamp iterations: at least 1, at most 100k to prevent resource exhaustion
    num_simulations = max(1, min(num_simulations, 100_000))

    if not _HAS_NUMPY:
        raise RuntimeError(
            "numpy is required for sensitivity analysis. Install with: pip install numpy"
        )

    if not line_items:
        return []

    # Fixed seed=42 is intentional for reproducibility: sensitivity analysis
    # must produce deterministic results so that repeated calls for the same
    # estimate return consistent tornado-diagram rankings.  The seed is NOT
    # used for the main Monte Carlo simulation (``run_monte_carlo``), which
    # accepts an optional user-supplied seed.
    rng = np.random.default_rng(seed=42)
    n_items = len(line_items)

    quantities = np.array([float(item.get("quantity", 1)) for item in line_items])
    unit_costs = np.array([float(item.get("unit_cost", 0)) for item in line_items])

    # Use material-specific uncertainty ranges when available
    try:
        from app.services.estimating.cost_database import get_uncertainty_range

        _has_unc = True
    except ImportError:
        _has_unc = False

    tri_min_list: list[float] = []
    tri_max_list: list[float] = []
    for i, item in enumerate(line_items):
        uc = unit_costs[i]
        if _has_unc:
            category = item.get("category", item.get("description", "default"))
            low_pct, high_pct = get_uncertainty_range(category)
            tri_min_list.append(uc * (1.0 - low_pct))
            tri_max_list.append(uc * (1.0 + high_pct))
        else:
            tri_min_list.append(uc * 0.85)
            tri_max_list.append(uc * 1.30)

    tri_min = np.array(tri_min_list)
    tri_mode = unit_costs
    tri_max = np.array(tri_max_list)

    # Sample each item independently: shape (num_simulations, n_items)
    sampled_costs = np.empty((num_simulations, n_items))
    for i in range(n_items):
        if tri_min[i] >= tri_max[i]:
            sampled_costs[:, i] = tri_mode[i]
        else:
            sampled_costs[:, i] = rng.triangular(
                tri_min[i], tri_mode[i], tri_max[i], size=num_simulations
            )

    # Item-level costs (unit_cost_sample * quantity)
    item_totals = sampled_costs * quantities  # (num_simulations, n_items)
    project_totals = np.sum(item_totals, axis=1)  # (num_simulations,)

    # Compute correlation of each item's variation with total project cost
    results: list[dict] = []
    project_std = float(np.std(project_totals))

    for i, item in enumerate(line_items):
        if project_std == 0 or np.std(item_totals[:, i]) == 0:
            corr = 0.0
        else:
            corr_matrix = np.corrcoef(item_totals[:, i], project_totals)
            corr = float(corr_matrix[0, 1])

        base_cost = float(unit_costs[i] * quantities[i])

        results.append(
            {
                "description": item.get("description", ""),
                "csi_code": item.get("csi_code", ""),
                "correlation_coefficient": round(corr, 4),
                "contribution_pct": 0.0,  # placeholder, computed below
                "base_cost": round(base_cost, 2),
            }
        )

    # Compute contribution percentages from squared correlations
    squared_corrs = [r["correlation_coefficient"] ** 2 for r in results]
    total_sq = sum(squared_corrs)

    if total_sq > 0:
        for i, r in enumerate(results):
            r["contribution_pct"] = round((squared_corrs[i] / total_sq) * 100.0, 2)

    # Sort by absolute correlation descending
    results.sort(key=lambda r: abs(r["correlation_coefficient"]), reverse=True)

    logger.info(
        "Sensitivity analysis complete: %d items, top contributor=%s (%.1f%%)",
        n_items,
        results[0]["description"] if results else "none",
        results[0]["contribution_pct"] if results else 0.0,
    )

    return results
