"""DEAP NSGA-II multi-objective construction site layout optimization.

Optimizes facility placement on a construction site to minimize travel
distance, safety risk, and crane inefficiency using the Non-dominated
Sorting Genetic Algorithm II.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

try:
    from deap import algorithms, base, creator, tools
except ImportError:
    algorithms = None  # type: ignore[assignment]
    base = None  # type: ignore[assignment]
    creator = None  # type: ignore[assignment]
    tools = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fitness evaluation (sync helper)
# ---------------------------------------------------------------------------


def evaluate_layout(
    positions: list[tuple[float, float]],
    facilities: list[dict],
    site_boundary: dict,
    constraints: dict,
) -> tuple[float, float, float]:
    """Evaluate a site layout solution for the three objectives.

    Objectives (all minimized):
        1. Total travel distance between related facilities
        2. Safety risk score (proximity of hazardous facilities)
        3. Crane utilization inefficiency (material storage distance from crane)

    Parameters
    ----------
    positions:
        (x, y) coordinates for each non-fixed facility.
    facilities:
        Full facility list including fixed and non-fixed.
    site_boundary:
        Site dimensions and exclusion zones.
    constraints:
        Min distances, road access, crane radius, etc.

    Returns
    -------
    Tuple of (travel_distance, safety_risk, crane_inefficiency).
    """
    site_w = float(site_boundary.get("width", 100))
    site_l = float(site_boundary.get("length", 100))

    # Build coordinate map: fixed facilities keep their positions,
    # non-fixed get positions from the individual
    coord_map: dict[str, tuple[float, float]] = {}
    pos_idx = 0
    for fac in facilities:
        fid = str(fac["id"])
        if fac.get("fixed"):
            coord_map[fid] = (
                float(fac.get("x", 0)),
                float(fac.get("y", 0)),
            )
        else:
            if pos_idx < len(positions):
                x, y = positions[pos_idx]
                # Clamp within site boundary accounting for facility dimensions
                fw = float(fac.get("width", 5)) / 2
                fl = float(fac.get("length", 5)) / 2
                x = max(fw, min(site_w - fw, x))
                y = max(fl, min(site_l - fl, y))
                coord_map[fid] = (x, y)
                pos_idx += 1

    # --- Objective 1: Travel distance ---
    travel_distance = 0.0
    # Sum pairwise distances weighted by relationship frequency
    fac_ids = [str(f["id"]) for f in facilities]
    for i, fid_a in enumerate(fac_ids):
        for fid_b in fac_ids[i + 1 :]:
            if fid_a in coord_map and fid_b in coord_map:
                dx = coord_map[fid_a][0] - coord_map[fid_b][0]
                dy = coord_map[fid_a][1] - coord_map[fid_b][1]
                dist = math.sqrt(dx * dx + dy * dy)
                travel_distance += dist

    # --- Objective 2: Safety risk ---
    safety_risk = 0.0
    min_distances = constraints.get("min_distance_between", {})
    exclusion_zones = site_boundary.get("exclusion_zones", [])

    # Penalty for violating minimum distance constraints
    for pair_key, min_dist in min_distances.items():
        parts = pair_key.split("-") if isinstance(pair_key, str) else []
        if len(parts) == 2:
            fid_a, fid_b = parts[0], parts[1]
            if fid_a in coord_map and fid_b in coord_map:
                dx = coord_map[fid_a][0] - coord_map[fid_b][0]
                dy = coord_map[fid_a][1] - coord_map[fid_b][1]
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < float(min_dist):
                    safety_risk += (float(min_dist) - dist) * 10.0

    # Penalty for facilities placed in exclusion zones
    for zone in exclusion_zones:
        zx = float(zone.get("x", 0))
        zy = float(zone.get("y", 0))
        zw = float(zone.get("width", 0))
        zl = float(zone.get("length", 0))
        for _fid, (fx, fy) in coord_map.items():
            if zx <= fx <= zx + zw and zy <= fy <= zy + zl:
                safety_risk += 100.0

    # Penalty for hazardous facilities near worker areas
    hazardous_types = {"fuel_storage", "chemical_storage", "generator"}
    worker_types = {"office", "break_area", "first_aid"}
    for fac_a in facilities:
        if fac_a.get("type") in hazardous_types:
            for fac_b in facilities:
                if fac_b.get("type") in worker_types:
                    fid_a = str(fac_a["id"])
                    fid_b = str(fac_b["id"])
                    if fid_a in coord_map and fid_b in coord_map:
                        dx = coord_map[fid_a][0] - coord_map[fid_b][0]
                        dy = coord_map[fid_a][1] - coord_map[fid_b][1]
                        dist = math.sqrt(dx * dx + dy * dy)
                        if dist < 30.0:
                            safety_risk += (30.0 - dist) * 5.0

    # --- Objective 3: Crane inefficiency ---
    crane_inefficiency = 0.0
    crane_radius = float(constraints.get("crane_radius", 50.0))

    # Find crane facilities
    crane_positions: list[tuple[float, float]] = []
    for fac in facilities:
        if fac.get("type") in ("crane", "tower_crane"):
            fid = str(fac["id"])
            if fid in coord_map:
                crane_positions.append(coord_map[fid])

    # Material storage should be near crane(s)
    storage_types = {"material_storage", "laydown_area", "staging"}
    for fac in facilities:
        if fac.get("type") in storage_types:
            fid = str(fac["id"])
            if fid in coord_map and crane_positions:
                min_crane_dist = min(
                    math.sqrt((coord_map[fid][0] - cx) ** 2 + (coord_map[fid][1] - cy) ** 2)
                    for cx, cy in crane_positions
                )
                # Penalty increases the farther from crane
                if min_crane_dist > crane_radius:
                    crane_inefficiency += (min_crane_dist - crane_radius) * 2.0
                else:
                    crane_inefficiency += min_crane_dist * 0.1

    return (travel_distance, safety_risk, crane_inefficiency)


# ---------------------------------------------------------------------------
# NSGA-II Optimizer
# ---------------------------------------------------------------------------


def _setup_deap(
    num_variables: int,
    site_boundary: dict,
) -> tuple[Any, Any]:
    """Configure DEAP toolbox for NSGA-II.

    Returns (toolbox, None).  Creator types are set up as module-level
    side-effects (DEAP requirement).
    """
    if base is None or creator is None or tools is None:
        raise ImportError(
            "DEAP is required for site layout optimization. Install with: pip install deap"
        )

    site_w = float(site_boundary.get("width", 100))
    site_l = float(site_boundary.get("length", 100))

    # DEAP requires module-level creator definitions; guard against re-creation
    if not hasattr(creator, "SiteLayoutFitness"):
        creator.create("SiteLayoutFitness", base.Fitness, weights=(-1.0, -1.0, -1.0))
    if not hasattr(creator, "SiteLayoutIndividual"):
        creator.create("SiteLayoutIndividual", list, fitness=creator.SiteLayoutFitness)

    toolbox = base.Toolbox()

    # Each gene is a coordinate value; individuals have num_variables * 2 genes
    # (x, y for each non-fixed facility)
    toolbox.register("attr_x", random.uniform, 0, site_w)
    toolbox.register("attr_y", random.uniform, 0, site_l)

    def _init_individual():
        genes = []
        for _ in range(num_variables):
            genes.extend([toolbox.attr_x(), toolbox.attr_y()])
        return creator.SiteLayoutIndividual(genes)

    toolbox.register("individual", _init_individual)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)

    # Genetic operators
    toolbox.register(
        "mate", tools.cxSimulatedBinaryBounded, low=0, up=max(site_w, site_l), eta=20.0
    )
    toolbox.register(
        "mutate",
        tools.mutPolynomialBounded,
        low=0,
        up=max(site_w, site_l),
        eta=20.0,
        indpb=1.0 / max(num_variables * 2, 1),
    )
    toolbox.register("select", tools.selNSGA2)

    return toolbox, None


async def optimize_site_layout(
    facilities: list[dict],
    site_boundary: dict,
    constraints: dict,
    population_size: int = 50,
    generations: int = 100,
) -> dict:
    """Optimize construction site layout using NSGA-II.

    Parameters
    ----------
    facilities:
        List of facility dicts with ``id``, ``name``, ``type``, ``width``,
        ``length``, ``fixed`` (bool), ``preferred_zone`` (optional).
    site_boundary:
        ``{width, length, exclusion_zones: [...]}``.
    constraints:
        ``{min_distance_between: {...}, road_access_required: [...], crane_radius: float}``.
    population_size:
        NSGA-II population size.
    generations:
        Number of evolutionary generations.

    Returns
    -------
    dict with layouts (top 5 Pareto-optimal solutions), pareto_front,
    generations, population_size.
    """
    non_fixed = [f for f in facilities if not f.get("fixed")]
    num_variables = len(non_fixed)

    if num_variables == 0:
        logger.info("No non-fixed facilities to optimize.")
        return {
            "layouts": [],
            "pareto_front": [],
            "generations": 0,
            "population_size": 0,
        }

    toolbox, _ = _setup_deap(num_variables, site_boundary)

    # Fitness wrapper: decode flat gene list into (x, y) pairs
    def _eval_wrapper(individual: list[float]) -> tuple[float, float, float]:
        positions = [(individual[i * 2], individual[i * 2 + 1]) for i in range(num_variables)]
        return evaluate_layout(positions, facilities, site_boundary, constraints)

    toolbox.register("evaluate", _eval_wrapper)

    # Seed population
    pop = toolbox.population(n=population_size)

    # Evaluate initial population
    fitnesses = list(map(toolbox.evaluate, pop))
    for ind, fit in zip(pop, fitnesses, strict=False):
        ind.fitness.values = fit

    # Evolution loop
    for _gen in range(generations):
        offspring = algorithms.varAnd(pop, toolbox, cxpb=0.7, mutpb=0.2)

        # Evaluate offspring that need evaluation
        invalids = [ind for ind in offspring if not ind.fitness.valid]
        fitnesses = list(map(toolbox.evaluate, invalids))
        for ind, fit in zip(invalids, fitnesses, strict=False):
            ind.fitness.values = fit

        # Select next generation
        pop = toolbox.select(pop + offspring, k=population_size)

    # Extract Pareto front
    pareto_front_inds = tools.sortNondominated(pop, len(pop), first_front_only=True)[0]

    # Sort by first objective and take top 5
    pareto_front_inds.sort(key=lambda ind: ind.fitness.values[0])
    top_solutions = pareto_front_inds[:5]

    layouts: list[dict] = []
    pareto_front: list[dict] = []

    for sol in top_solutions:
        positions = {}
        for i, fac in enumerate(non_fixed):
            positions[str(fac["id"])] = {
                "x": round(sol[i * 2], 2),
                "y": round(sol[i * 2 + 1], 2),
            }
        # Include fixed facility positions
        for fac in facilities:
            if fac.get("fixed"):
                positions[str(fac["id"])] = {
                    "x": float(fac.get("x", 0)),
                    "y": float(fac.get("y", 0)),
                }

        travel_dist, safety, crane_ineff = sol.fitness.values
        layouts.append(
            {
                "facility_positions": positions,
                "travel_distance": round(travel_dist, 2),
                "safety_score": round(safety, 2),
                "crane_efficiency_score": round(crane_ineff, 2),
            }
        )
        pareto_front.append(
            {
                "travel_distance": round(travel_dist, 2),
                "safety_score": round(safety, 2),
                "efficiency_score": round(crane_ineff, 2),
            }
        )

    logger.info(
        "Site layout optimization complete: %d generations, %d Pareto solutions",
        generations,
        len(pareto_front),
    )

    return {
        "layouts": layouts,
        "pareto_front": pareto_front,
        "generations": generations,
        "population_size": population_size,
    }
