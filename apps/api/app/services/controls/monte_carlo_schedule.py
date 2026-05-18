"""Monte Carlo schedule risk simulation.

Supports both independent and correlated activity duration sampling.
Correlated sampling uses Cholesky decomposition to generate jointly
distributed PERT-beta variates, which better captures cascading risk.
"""

from __future__ import annotations

import logging
from collections import deque
from decimal import Decimal

import numpy as np

# Guard scipy import; fall back to triangular if unavailable
try:
    from scipy.stats import beta as _scipy_beta
    from scipy.stats import norm as _scipy_norm

    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _scipy_beta = None  # type: ignore[assignment]
    _scipy_norm = None  # type: ignore[assignment]
    _HAS_SCIPY = False

logger = logging.getLogger(__name__)

_MAX_SEED = 2**32 - 1  # numpy default_rng accepts 0..2^32-1 for reproducibility


def _validate_seed(seed: int | None) -> int | None:
    """Validate random seed is within a safe range for numpy."""
    if seed is None:
        return None
    if not isinstance(seed, int) or seed < 0 or seed > _MAX_SEED:
        raise ValueError(f"seed must be an integer between 0 and {_MAX_SEED}")
    return seed


# ---------------------------------------------------------------------------
# Default correlation coefficients (activity-pair rules)
# ---------------------------------------------------------------------------

CORR_SAME_WBS_PARENT = 0.7
CORR_DIRECT_PREDECESSOR = 0.6
CORR_SAME_RESOURCE = 0.5
CORR_DIFFERENT_WBS_BRANCH = 0.15
CORR_DEFAULT = 0.0


# ---------------------------------------------------------------------------
# PERT Beta distribution sampling
# ---------------------------------------------------------------------------


def _pert_sample(
    optimistic: float,
    most_likely: float,
    pessimistic: float,
    lam: float = 4.0,
    rng: np.random.Generator | None = None,
) -> float:
    """Sample from a PERT Beta distribution.

    Parameters
    ----------
    optimistic: Minimum / best-case value
    most_likely: Most likely value (mode)
    pessimistic: Maximum / worst-case value
    lam: Shape parameter (default 4 for standard PERT)
    rng: numpy random generator (preferred over global state)

    Falls back to numpy triangular distribution if scipy is not installed.

    Raises
    ------
    ValueError
        If pessimistic < optimistic (inverted bounds).
    """
    _EPSILON = 1e-9  # Float precision tolerance

    if pessimistic < optimistic - _EPSILON:
        raise ValueError(f"pessimistic ({pessimistic}) must be >= optimistic ({optimistic})")

    # Degenerate case: estimates are equal (within float precision) — no uncertainty
    if abs(pessimistic - optimistic) < _EPSILON:
        return most_likely

    range_val = pessimistic - optimistic

    if rng is None:
        rng = np.random.default_rng()

    if _HAS_SCIPY:
        alpha = 1.0 + lam * (most_likely - optimistic) / range_val
        beta_param = 1.0 + lam * (pessimistic - most_likely) / range_val
        sample = optimistic + range_val * _scipy_beta.rvs(alpha, beta_param, random_state=rng)  # pyright: ignore[reportOptionalMemberAccess]
        return float(sample)
    else:
        return float(rng.triangular(optimistic, most_likely, pessimistic))


# ---------------------------------------------------------------------------
# Correlation matrix construction
# ---------------------------------------------------------------------------


def _build_correlation_matrix(act_params: list[dict]) -> np.ndarray:
    """Build an N×N correlation matrix based on schedule structure.

    Rules (applied in priority order — highest correlation wins):
    - Same WBS parent: 0.7
    - Direct predecessor-successor: 0.6
    - Same resource/trade: 0.5
    - Different WBS branch (but same project): 0.15
    - Default (no relationship): 0.0

    Diagonal is always 1.0.
    """
    n = len(act_params)
    corr = np.eye(n)

    id_to_idx = {ap["id"]: i for i, ap in enumerate(act_params)}

    # Build predecessor/successor sets
    pred_pairs: set[tuple[int, int]] = set()
    for ap in act_params:
        j = id_to_idx[ap["id"]]
        for pred_id in ap.get("predecessors", []):
            if pred_id in id_to_idx:
                i = id_to_idx[pred_id]
                pred_pairs.add((i, j))
                pred_pairs.add((j, i))

    # Extract WBS parents (common prefix before last segment)
    wbs_parents: dict[int, str] = {}
    for idx, ap in enumerate(act_params):
        wbs = ap.get("wbs_code") or ap.get("wbs_path") or ""
        # Parent = everything before the last "/" or the WBS code prefix
        if "/" in wbs:
            wbs_parents[idx] = wbs.rsplit("/", 1)[0]
        elif wbs:
            wbs_parents[idx] = wbs
        else:
            wbs_parents[idx] = ""

    # Extract resource/trade sets
    resources: dict[int, set[str]] = {}
    for idx, ap in enumerate(act_params):
        res_set: set[str] = set()
        for ra in ap.get("resource_assignments", []):
            if isinstance(ra, dict):
                rname = ra.get("resource_name", "")
                if rname:
                    res_set.add(rname.lower())
            elif isinstance(ra, str):
                res_set.add(ra.lower())
        resources[idx] = res_set

    for i in range(n):
        for j in range(i + 1, n):
            rho = CORR_DEFAULT

            # Rule 1: direct predecessor-successor
            if (i, j) in pred_pairs:
                rho = max(rho, CORR_DIRECT_PREDECESSOR)

            # Rule 2: same WBS parent
            wi, wj = wbs_parents.get(i, ""), wbs_parents.get(j, "")
            if wi and wj and wi == wj:
                rho = max(rho, CORR_SAME_WBS_PARENT)
            elif wi and wj and wi != wj:
                rho = max(rho, CORR_DIFFERENT_WBS_BRANCH)

            # Rule 3: shared resources
            ri, rj = resources.get(i, set()), resources.get(j, set())
            if ri and rj and ri & rj:
                rho = max(rho, CORR_SAME_RESOURCE)

            corr[i, j] = rho
            corr[j, i] = rho

    # Ensure positive semi-definite (numerical safety)
    corr = _nearest_positive_semidefinite(corr)
    return corr


def _nearest_positive_semidefinite(matrix: np.ndarray) -> np.ndarray:
    """Project a symmetric matrix to the nearest positive semi-definite matrix.

    Uses eigenvalue clipping: negative eigenvalues are set to a small epsilon.
    """
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    epsilon = 1e-8
    eigenvalues = np.maximum(eigenvalues, epsilon)
    result = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
    # Restore unit diagonal
    d = np.sqrt(np.diag(result))
    d[d == 0] = 1.0
    result = result / np.outer(d, d)
    np.fill_diagonal(result, 1.0)
    return result


def _pert_params(optimistic: float, most_likely: float, pessimistic: float, lam: float = 4.0):
    """Return (alpha, beta) shape parameters for a PERT Beta distribution."""
    range_val = pessimistic - optimistic
    if range_val <= 0:
        return None, None
    alpha = 1.0 + lam * (most_likely - optimistic) / range_val
    beta_param = 1.0 + lam * (pessimistic - most_likely) / range_val
    return alpha, beta_param


def _sample_correlated_pert(
    act_params: list[dict],
    corr_matrix: np.ndarray,
    rng: np.random.Generator,
) -> dict[str, float]:
    """Generate correlated PERT-beta samples using Cholesky decomposition.

    Steps:
    1. Generate N independent standard normals
    2. Multiply by Cholesky factor L to get correlated normals
    3. Transform to uniform [0,1] via norm.cdf()
    4. Transform to PERT-beta variates via beta.ppf()
    """
    n = len(act_params)

    # Cholesky decomposition (L @ L^T = corr_matrix)
    try:
        L = np.linalg.cholesky(corr_matrix)
    except np.linalg.LinAlgError:
        # Fallback: use nearest PSD
        psd = _nearest_positive_semidefinite(corr_matrix)
        L = np.linalg.cholesky(psd)

    # Step 1: independent standard normals
    z = rng.standard_normal(n)

    # Step 2: correlated normals
    corr_z = L @ z

    # Step 3 & 4: transform to PERT-beta via uniform
    sampled: dict[str, float] = {}
    for i, ap in enumerate(act_params):
        opt = ap["optimistic"]
        ml = ap["most_likely"]
        pess = ap["pessimistic"]
        range_val = pess - opt

        if range_val <= 0:
            sampled[ap["id"]] = ml
            continue

        alpha, beta_param = _pert_params(opt, ml, pess)
        if alpha is None:
            sampled[ap["id"]] = ml
            continue

        if _HAS_SCIPY:
            # Correlated normal → uniform via CDF
            u = float(_scipy_norm.cdf(corr_z[i]))  # pyright: ignore[reportOptionalMemberAccess]
            u = max(1e-10, min(1 - 1e-10, u))  # clamp to avoid boundary issues
            # Uniform → PERT-beta via inverse CDF
            sample = opt + range_val * float(_scipy_beta.ppf(u, alpha, beta_param))  # pyright: ignore[reportOptionalMemberAccess]
        else:
            # Fallback: triangular with some correlation effect
            u = float(np.clip((corr_z[i] + 3) / 6, 0.01, 0.99))
            sample = opt + range_val * u

        sampled[ap["id"]] = max(1.0, sample)

    return sampled


def _topological_sort(act_params: list[dict]) -> list[dict]:
    """Topological sort of activities by predecessors (Kahn's algorithm).

    Raises ValueError if a cycle is detected.
    """
    id_to_act = {ap["id"]: ap for ap in act_params}
    in_degree: dict[str, int] = {ap["id"]: 0 for ap in act_params}
    successors: dict[str, list[str]] = {ap["id"]: [] for ap in act_params}

    for ap in act_params:
        for pred_id in ap["predecessors"]:
            if pred_id in id_to_act:
                in_degree[ap["id"]] += 1
                successors[pred_id].append(ap["id"])

    queue = deque(aid for aid, deg in in_degree.items() if deg == 0)
    order: list[dict] = []

    while queue:
        node = queue.popleft()
        order.append(id_to_act[node])
        for succ in successors[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(order) != len(act_params):
        raise ValueError("Activity dependency cycle detected in schedule")

    return order


# ---------------------------------------------------------------------------
# Schedule Risk Simulation
# ---------------------------------------------------------------------------


async def run_schedule_risk_simulation(
    activities: list[dict],
    num_iterations: int = 10000,
    seed: int | None = None,
    use_correlations: bool = False,
) -> dict:
    """Run Monte Carlo simulation on schedule activities.

    Each activity should have:
    - id: str
    - name: str
    - duration_days: int (most likely)
    - optimistic_days: int (optional, default duration_days * optimistic_factor)
    - pessimistic_days: int (optional, default duration_days * pessimistic_factor)
    - optimistic_factor: float (optional, default 0.8)
    - pessimistic_factor: float (optional, default 1.5)
    - predecessors: list[str]
    - wbs_code: str (optional, used for correlation)
    - wbs_path: str (optional, used for correlation)
    - resource_assignments: list (optional, used for correlation)

    Parameters
    ----------
    use_correlations:
        When True, uses Cholesky decomposition to generate correlated
        activity duration samples based on WBS, predecessor, and resource
        relationships.  This produces wider P10-P90 ranges that better
        capture cascading risk.

    Returns percentile durations, critical risk drivers, sensitivity
    analysis (tornado diagram data), and optional correlation impact summary.
    """
    MAX_ITERATIONS = 100_000
    if num_iterations > MAX_ITERATIONS:
        raise ValueError(f"num_iterations must be {MAX_ITERATIONS} or fewer")
    seed = _validate_seed(seed)

    rng = np.random.default_rng(seed)

    if not activities:
        return _empty_result()

    # Prepare activity parameters with per-activity configurable bounds
    act_params = []
    for act in activities:
        ml = act.get("duration_days", 10)
        opt_factor = act.get("optimistic_factor", 0.8)
        pess_factor = act.get("pessimistic_factor", 1.5)
        opt = act.get("optimistic_days", max(1, int(ml * opt_factor)))
        pess = act.get("pessimistic_days", int(ml * pess_factor))
        act_params.append(
            {
                "id": act["id"],
                "name": act.get("name", act["id"]),
                "optimistic": opt,
                "most_likely": ml,
                "pessimistic": pess,
                "predecessors": act.get("predecessors", []),
                "wbs_code": act.get("wbs_code"),
                "wbs_path": act.get("wbs_path"),
                "resource_assignments": act.get("resource_assignments", []),
            }
        )

    # Topological sort to ensure correct forward pass order
    topo_order = _topological_sort(act_params)

    # Build correlation matrix if requested
    corr_matrix = None
    if use_correlations and len(act_params) > 1:
        corr_matrix = _build_correlation_matrix(act_params)

    # Run simulations
    durations = []
    activity_criticality = {a["id"]: 0 for a in act_params}
    # Track per-activity sampled durations for sensitivity
    all_sampled_durations: dict[str, list[float]] = {a["id"]: [] for a in act_params}

    for _ in range(num_iterations):
        # Sample durations
        if corr_matrix is not None:
            sampled = _sample_correlated_pert(act_params, corr_matrix, rng)
            sampled = {k: max(1, round(v)) for k, v in sampled.items()}
        else:
            sampled = {}
            for ap in act_params:
                sample = _pert_sample(
                    ap["optimistic"],
                    ap["most_likely"],
                    ap["pessimistic"],
                    rng=rng,
                )
                sampled[ap["id"]] = max(1, round(sample))

        for aid, dur in sampled.items():
            all_sampled_durations[aid].append(float(dur))

        # Forward pass in topological order
        finish_times: dict[str, int] = {}
        start_times: dict[str, int] = {}
        for ap in topo_order:
            preds = ap["predecessors"]
            start = 0
            if preds:
                start = max(finish_times.get(p, 0) for p in preds)
            start_times[ap["id"]] = start
            finish_times[ap["id"]] = start + int(sampled[ap["id"]])

        project_duration = max(finish_times.values())
        durations.append(project_duration)

        # Track criticality using backward pass (true critical path)
        # An activity is critical if total float == 0
        late_finish: dict[str, int] = {}
        successors_map: dict[str, list[str]] = {ap["id"]: [] for ap in act_params}
        for ap in act_params:
            for p in ap["predecessors"]:
                successors_map.setdefault(p, []).append(ap["id"])

        for ap in reversed(topo_order):
            succs = successors_map.get(ap["id"], [])
            if succs:
                lf = min((late_finish.get(s, project_duration) - sampled.get(s, 0)) for s in succs)
                # late_finish = min(late_start of successors) + own duration
                # Actually: LF = min(LS of successors), LS = LF - duration
                lf = min(late_finish.get(s, project_duration) for s in succs)
            else:
                lf = project_duration
            late_finish[ap["id"]] = lf
            total_float = lf - sampled[ap["id"]] - start_times[ap["id"]]
            if total_float == 0:
                activity_criticality[ap["id"]] += 1

    durations_arr = np.array(durations)

    # Compute percentiles
    p10 = int(np.percentile(durations_arr, 10))
    p50 = int(np.percentile(durations_arr, 50))
    p80 = int(np.percentile(durations_arr, 80))
    p90 = int(np.percentile(durations_arr, 90))
    mean_dur = float(np.mean(durations_arr))
    std_dev = float(np.std(durations_arr))

    # Critical risk drivers (top by criticality)
    drivers = []
    for ap in act_params:
        crit_pct = activity_criticality[ap["id"]] / num_iterations * 100
        if crit_pct > 5:
            drivers.append(
                {
                    "activity_id": ap["id"],
                    "activity_name": ap["name"],
                    "criticality_pct": round(crit_pct, 1),
                    "duration_range": (f"{ap['optimistic']}-{ap['pessimistic']} days"),
                }
            )

    drivers.sort(
        key=lambda d: d["criticality_pct"],
        reverse=True,
    )

    # Histogram bins
    hist_counts, _ = np.histogram(durations_arr, bins=20)
    histogram_data = [float(c) for c in hist_counts]

    # Sensitivity analysis (tornado diagram data)
    sensitivity = _compute_sensitivity_analysis(act_params)

    # Per-activity criticality index (fraction of iterations on critical path)
    criticality_index = {
        ap["id"]: round(activity_criticality[ap["id"]] / num_iterations, 4) for ap in act_params
    }

    # Per-activity variance contribution (how much each activity
    # contributes to overall project duration variance)
    variance_contributions = _compute_variance_contributions(
        act_params, all_sampled_durations, durations_arr
    )

    result = {
        "num_iterations": num_iterations,
        "p10_duration": p10,
        "p50_duration": p50,
        "p80_duration": p80,
        "p90_duration": p90,
        "mean_duration": round(Decimal(str(mean_dur)), 2),
        "std_dev": round(Decimal(str(std_dev)), 2),
        "critical_risk_drivers": drivers,
        "histogram_data": histogram_data,
        "sensitivity_analysis": sensitivity,
        "criticality_index": criticality_index,
        "variance_contributions": variance_contributions,
        "use_correlations": use_correlations,
    }

    # If correlated, also run uncorrelated for comparison
    if use_correlations and len(act_params) > 1:
        uncorr_result = await run_schedule_risk_simulation(
            activities=activities,
            num_iterations=num_iterations,
            seed=seed,
            use_correlations=False,
        )
        uncorr_range = uncorr_result["p90_duration"] - uncorr_result["p10_duration"]
        corr_range = p90 - p10
        if uncorr_range > 0:
            range_increase_pct = round((corr_range - uncorr_range) / uncorr_range * 100, 1)
        else:
            range_increase_pct = 0.0

        result["correlation_impact"] = {
            "uncorrelated_p10": uncorr_result["p10_duration"],
            "uncorrelated_p50": uncorr_result["p50_duration"],
            "uncorrelated_p90": uncorr_result["p90_duration"],
            "uncorrelated_range": uncorr_range,
            "correlated_p10": p10,
            "correlated_p50": p50,
            "correlated_p90": p90,
            "correlated_range": corr_range,
            "range_increase_pct": range_increase_pct,
        }

    logger.info(
        "Monte Carlo simulation complete: %d iterations, P50=%d days, P80=%d days, correlated=%s",
        num_iterations,
        p50,
        p80,
        use_correlations,
    )
    return result


# ---------------------------------------------------------------------------
# Sensitivity / Tornado Analysis
# ---------------------------------------------------------------------------


def _compute_variance_contributions(
    act_params: list[dict],
    all_sampled_durations: dict[str, list[float]],
    project_durations: np.ndarray,
) -> list[dict]:
    """Compute each activity's contribution to project duration variance.

    Uses Pearson correlation between each activity's sampled duration and
    the project duration.  Activities with higher correlation contribute
    more to schedule uncertainty.
    """
    contributions: list[dict] = []
    proj_std = float(np.std(project_durations))
    if proj_std == 0:
        return contributions

    for ap in act_params:
        samples = np.array(all_sampled_durations[ap["id"]])
        if len(samples) < 2 or float(np.std(samples)) == 0:
            contributions.append(
                {
                    "activity_id": ap["id"],
                    "activity_name": ap["name"],
                    "correlation_with_project": 0.0,
                    "variance_contribution_pct": 0.0,
                }
            )
            continue

        corr = float(np.corrcoef(samples, project_durations)[0, 1])
        # Variance contribution proportional to correlation squared
        contributions.append(
            {
                "activity_id": ap["id"],
                "activity_name": ap["name"],
                "correlation_with_project": round(corr, 4),
                "variance_contribution_pct": round(corr**2 * 100, 1),
            }
        )

    contributions.sort(key=lambda c: c["variance_contribution_pct"], reverse=True)
    return contributions


def _compute_sensitivity_analysis(act_params: list[dict]) -> list[dict]:
    """Compute tornado-style sensitivity data.

    For each activity, compute the project duration range when only that
    activity varies (all others held at most_likely). Results are sorted
    by impact (descending).
    """
    # Baseline: all activities at most-likely duration
    baseline_finish = _forward_pass_deterministic(
        act_params, {ap["id"]: ap["most_likely"] for ap in act_params}
    )

    sensitivity = []
    for ap in act_params:
        # All at most-likely except this one at optimistic
        dur_opt = {a["id"]: a["most_likely"] for a in act_params}
        dur_opt[ap["id"]] = ap["optimistic"]
        finish_opt = _forward_pass_deterministic(act_params, dur_opt)

        # All at most-likely except this one at pessimistic
        dur_pess = {a["id"]: a["most_likely"] for a in act_params}
        dur_pess[ap["id"]] = ap["pessimistic"]
        finish_pess = _forward_pass_deterministic(act_params, dur_pess)

        impact = finish_pess - finish_opt
        sensitivity.append(
            {
                "activity_id": ap["id"],
                "activity_name": ap["name"],
                "low_duration": finish_opt,
                "high_duration": finish_pess,
                "baseline_duration": baseline_finish,
                "impact_range": impact,
            }
        )

    sensitivity.sort(key=lambda s: s["impact_range"], reverse=True)
    return sensitivity


def _forward_pass_deterministic(
    act_params: list[dict],
    durations: dict[str, int],
) -> int:
    """Run a single deterministic forward pass and return project duration."""
    finish_times: dict[str, int] = {}
    for ap in act_params:
        preds = ap["predecessors"]
        start = 0
        if preds:
            start = max(finish_times.get(p, 0) for p in preds)
        finish_times[ap["id"]] = start + durations[ap["id"]]
    return max(finish_times.values()) if finish_times else 0


# ---------------------------------------------------------------------------
# Cost Risk Simulation
# ---------------------------------------------------------------------------


async def run_cost_risk_simulation(
    activities: list[dict],
    num_iterations: int = 10000,
    seed: int | None = None,
    use_industry_uncertainty: bool = True,
) -> dict:
    """Run Monte Carlo simulation on activity costs.

    Each activity should have:
    - id: str
    - name: str
    - estimated_cost: float (most likely cost)
    - cost_optimistic: float (optional)
    - cost_pessimistic: float (optional)
    - wbs_code: str (optional, for uncertainty classification)

    When *use_industry_uncertainty* is True (default) and explicit
    optimistic/pessimistic values are not provided, the activity name
    and WBS code are used to classify the activity and apply industry-
    standard duration uncertainty percentages from DURATION_UNCERTAINTY.

    Returns cost percentiles (P10, P50, P80, P90) and cost risk drivers.
    """
    MAX_ITERATIONS = 100_000
    if num_iterations > MAX_ITERATIONS:
        raise ValueError(f"num_iterations must be {MAX_ITERATIONS} or fewer")
    seed = _validate_seed(seed)

    cost_rng = np.random.default_rng(seed)

    if not activities:
        return _empty_cost_result()

    # Prepare activity cost parameters
    cost_params = []
    for act in activities:
        est = float(act.get("estimated_cost", 0))

        # Use explicit bounds if provided
        if "cost_optimistic" in act or "cost_pessimistic" in act:
            opt = float(act.get("cost_optimistic", est * 0.85))
            pess = float(act.get("cost_pessimistic", est * 1.3))
        elif use_industry_uncertainty:
            # Classify activity and use industry uncertainty
            from app.services.controls.industry_benchmarks import (
                classify_activity,
                get_duration_bounds,
            )

            category = classify_activity(
                name=act.get("name", ""),
                wbs_code=act.get("wbs_code"),
            )
            opt_dur, pess_dur = get_duration_bounds(est, category=category)
            opt = opt_dur
            pess = pess_dur
        else:
            opt = est * 0.85
            pess = est * 1.3

        cost_params.append(
            {
                "id": act["id"],
                "name": act.get("name", act["id"]),
                "optimistic": opt,
                "most_likely": est,
                "pessimistic": pess,
            }
        )

    # Run simulations
    total_costs = []
    activity_cost_samples: dict[str, list[float]] = {cp["id"]: [] for cp in cost_params}

    for _ in range(num_iterations):
        iteration_total = 0.0
        for cp in cost_params:
            sample = _pert_sample(
                cp["optimistic"],
                cp["most_likely"],
                cp["pessimistic"],
                rng=cost_rng,
            )
            sample = max(0.0, sample)
            activity_cost_samples[cp["id"]].append(sample)
            iteration_total += sample
        total_costs.append(iteration_total)

    costs_arr = np.array(total_costs)

    # Compute percentiles
    p10 = float(np.percentile(costs_arr, 10))
    p50 = float(np.percentile(costs_arr, 50))
    p80 = float(np.percentile(costs_arr, 80))
    p90 = float(np.percentile(costs_arr, 90))
    mean_cost = float(np.mean(costs_arr))
    std_dev = float(np.std(costs_arr))

    # Cost risk drivers: activities with highest cost variance
    cost_drivers = []
    for cp in cost_params:
        samples = np.array(activity_cost_samples[cp["id"]])
        act_std = float(np.std(samples))
        act_mean = float(np.mean(samples))
        cost_drivers.append(
            {
                "activity_id": cp["id"],
                "activity_name": cp["name"],
                "mean_cost": round(act_mean, 2),
                "std_dev": round(act_std, 2),
                "cost_range": f"{cp['optimistic']:.0f}-{cp['pessimistic']:.0f}",
            }
        )

    cost_drivers.sort(key=lambda d: d["std_dev"], reverse=True)

    # Histogram bins
    hist_counts, _ = np.histogram(costs_arr, bins=20)
    histogram_data = [float(c) for c in hist_counts]

    result = {
        "num_iterations": num_iterations,
        "p10_cost": round(Decimal(str(p10)), 2),
        "p50_cost": round(Decimal(str(p50)), 2),
        "p80_cost": round(Decimal(str(p80)), 2),
        "p90_cost": round(Decimal(str(p90)), 2),
        "mean_cost": round(Decimal(str(mean_cost)), 2),
        "std_dev": round(Decimal(str(std_dev)), 2),
        "cost_risk_drivers": cost_drivers,
        "histogram_data": histogram_data,
    }

    logger.info(
        "Cost risk simulation complete: %d iterations, P50=$%.2f, P80=$%.2f",
        num_iterations,
        p50,
        p80,
    )
    return result


# ---------------------------------------------------------------------------
# Empty result helpers
# ---------------------------------------------------------------------------


def _empty_result() -> dict:
    """Return empty simulation result."""
    return {
        "num_iterations": 0,
        "p10_duration": 0,
        "p50_duration": 0,
        "p80_duration": 0,
        "p90_duration": 0,
        "mean_duration": Decimal("0"),
        "std_dev": Decimal("0"),
        "critical_risk_drivers": [],
        "histogram_data": [],
        "sensitivity_analysis": [],
    }


def _empty_cost_result() -> dict:
    """Return empty cost simulation result."""
    return {
        "num_iterations": 0,
        "p10_cost": Decimal("0"),
        "p50_cost": Decimal("0"),
        "p80_cost": Decimal("0"),
        "p90_cost": Decimal("0"),
        "mean_cost": Decimal("0"),
        "std_dev": Decimal("0"),
        "cost_risk_drivers": [],
        "histogram_data": [],
    }
