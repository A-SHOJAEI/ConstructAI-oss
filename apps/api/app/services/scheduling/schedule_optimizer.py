"""Generative schedule optimization engine.

Takes a baseline schedule, generates perturbation scenarios across four
strategy dimensions (crew sizing, shift work, resequencing, activity
splitting), evaluates each through CPM + cost analysis, and returns
Pareto-ranked results.

The engine operates on pure activity dicts — no database access — making
it testable without mocks for the core optimization logic.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.scheduling.cpm_engine import WorkCalendar, calculate_cpm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

NEAR_CRITICAL_THRESHOLD_DAYS = 5


@dataclass
class OptimizationConfig:
    """Configuration knobs for the optimization run."""

    max_scenarios: int = 50
    max_crew_multiplier: float = 2.0
    allow_overtime: bool = True
    allow_weekend_work: bool = False
    allow_resequencing: bool = True
    allow_splitting: bool = True
    overtime_cost_multiplier: float = 1.5
    shift_differential_pct: float = 15.0
    weights: dict[str, float] = field(
        default_factory=lambda: {"duration": 0.4, "cost": 0.35, "risk": 0.25}
    )


@dataclass
class ProjectContext:
    """Contextual project metadata used during evaluation."""

    project_id: str
    location: str | None = None
    start_date: date = field(default_factory=date.today)
    hourly_rate: Decimal = Decimal("75.00")
    calendar: WorkCalendar | None = None


@dataclass
class ChangeDescription:
    """A single field-level change within a scenario."""

    activity_id: str
    field: str
    original_value: Any
    new_value: Any
    reason: str


@dataclass
class Scenario:
    """A candidate schedule perturbation."""

    id: str
    name: str
    description: str
    perturbation_type: str
    activities: list[dict]
    changes: list[ChangeDescription]


@dataclass
class ScenarioResult:
    """Evaluation output for a single scenario."""

    scenario: Scenario
    duration_days: int
    cost_delta: Decimal
    risk_score: float
    critical_path_count: int
    near_critical_count: int
    weather_delay_days: int
    is_pareto_optimal: bool = False
    rank: int | None = None


@dataclass
class OptimizationResult:
    """Top-level result of an optimization run."""

    baseline_duration: int
    baseline_cost: Decimal
    scenarios: list[ScenarioResult]
    pareto_front: list[ScenarioResult]
    best_duration: ScenarioResult | None
    best_cost: ScenarioResult | None
    best_balanced: ScenarioResult | None
    processing_time_ms: int


# ---------------------------------------------------------------------------
# Helper: identify critical & near-critical activity ids
# ---------------------------------------------------------------------------


def _classify_activities(
    cpm_result: dict,
) -> tuple[set[str], set[str]]:
    """Return (critical_ids, near_critical_ids) from CPM output."""
    critical: set[str] = set()
    near_critical: set[str] = set()
    for act in cpm_result["activities"]:
        aid = str(act["id"])
        tf = act.get("total_float", 0)
        if tf == 0:
            critical.add(aid)
        elif 0 < tf <= NEAR_CRITICAL_THRESHOLD_DAYS:
            near_critical.add(aid)
    return critical, near_critical


def _activity_index(activities: list[dict]) -> dict[str, dict]:
    """Build id -> activity dict mapping."""
    return {str(a["id"]): a for a in activities}


def _get_predecessor_ids(activity: dict) -> list[str]:
    """Extract predecessor ids from both rich relationships and simple format."""
    preds: set[str] = set()
    for rel in activity.get("relationships", []):
        preds.add(str(rel["predecessor_id"]))
    for p in activity.get("predecessors", []):
        preds.add(str(p))
    return list(preds)


def _get_successor_map(activities: list[dict]) -> dict[str, list[str]]:
    """Build activity_id -> list of successor activity_ids."""
    successors: dict[str, list[str]] = defaultdict(list)
    for act in activities:
        aid = str(act["id"])
        for pred_id in _get_predecessor_ids(act):
            successors[pred_id].append(aid)
    return successors


def _has_cycle(activities: list[dict]) -> bool:
    """Return True if the activity graph contains a cycle (Kahn's algorithm)."""
    idx = _activity_index(activities)
    in_degree: dict[str, int] = {aid: 0 for aid in idx}
    successors: dict[str, list[str]] = defaultdict(list)

    for act in activities:
        aid = str(act["id"])
        preds = _get_predecessor_ids(act)
        in_degree[aid] = len(preds)
        for p in preds:
            successors[p].append(aid)

    from collections import deque

    queue: deque[str] = deque(a for a, d in in_degree.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for s in successors.get(node, []):
            in_degree[s] -= 1
            if in_degree[s] == 0:
                queue.append(s)

    return visited != len(idx)


# ---------------------------------------------------------------------------
# Scenario generators
# ---------------------------------------------------------------------------


def _generate_crew_size_scenarios(
    activities: list[dict],
    critical_ids: set[str],
    config: OptimizationConfig,
) -> list[Scenario]:
    """Generate crew-size adjustment scenarios for critical activities.

    Uses the power rule:  new_duration = original * (original_crew / new_crew) ^ 0.85
    to model diminishing returns from adding workers (Brooks's law lite).
    """
    scenarios: list[Scenario] = []
    multipliers = [0.75, 1.25, 1.5]

    for act in activities:
        aid = str(act["id"])
        if aid not in critical_ids:
            continue

        crew_size = act.get("crew_size", 0)
        if crew_size <= 0:
            continue

        original_duration = int(act.get("duration_days", 0))
        if original_duration <= 0:
            continue

        for mult in multipliers:
            new_crew = max(1, round(crew_size * mult))
            if new_crew == crew_size:
                continue
            if new_crew > crew_size * config.max_crew_multiplier:
                continue

            # Diminishing returns exponent
            ratio = crew_size / new_crew
            new_duration = max(1, round(original_duration * (ratio**0.85)))
            if new_duration == original_duration:
                continue

            # Build modified activities list
            modified = copy.deepcopy(activities)
            act_idx = _activity_index(modified)
            target = act_idx.get(aid)
            if target is None:
                continue
            target["duration_days"] = new_duration
            target["crew_size"] = new_crew

            direction = "increase" if new_crew > crew_size else "decrease"
            change = ChangeDescription(
                activity_id=aid,
                field="crew_size",
                original_value=crew_size,
                new_value=new_crew,
                reason=f"Crew {direction} from {crew_size} to {new_crew} "
                f"(duration {original_duration}d -> {new_duration}d)",
            )
            duration_change = ChangeDescription(
                activity_id=aid,
                field="duration_days",
                original_value=original_duration,
                new_value=new_duration,
                reason="Adjusted via crew sizing power rule (exponent 0.85)",
            )

            scenario = Scenario(
                id=str(uuid.uuid4()),
                name=f"Crew {direction}: {act.get('name', aid)} x{mult:.2f}",
                description=(
                    f"Adjust crew size for '{act.get('name', aid)}' from "
                    f"{crew_size} to {new_crew} workers"
                ),
                perturbation_type="crew_size",
                activities=modified,
                changes=[change, duration_change],
            )
            scenarios.append(scenario)

    return scenarios


def _generate_shift_scenarios(
    activities: list[dict],
    critical_ids: set[str],
    config: OptimizationConfig,
) -> list[Scenario]:
    """Generate shift-work scenarios for critical activities.

    - Second shift: reduces duration by 40%, adds shift differential cost.
    - Weekend work (6-day / 7-day): compresses calendar time proportionally.
    """
    scenarios: list[Scenario] = []

    for act in activities:
        aid = str(act["id"])
        if aid not in critical_ids:
            continue

        original_duration = int(act.get("duration_days", 0))

        # Second shift: only for activities > 5 days
        if original_duration > 5 and config.allow_overtime:
            new_duration = max(1, round(original_duration * 0.6))

            modified = copy.deepcopy(activities)
            act_idx = _activity_index(modified)
            target = act_idx.get(aid)
            if target is not None:
                target["duration_days"] = new_duration
                target["second_shift"] = True

                change = ChangeDescription(
                    activity_id=aid,
                    field="duration_days",
                    original_value=original_duration,
                    new_value=new_duration,
                    reason=f"Second shift added: duration reduced 40% "
                    f"({original_duration}d -> {new_duration}d)",
                )

                scenarios.append(
                    Scenario(
                        id=str(uuid.uuid4()),
                        name=f"Second shift: {act.get('name', aid)}",
                        description=(
                            f"Add second shift to '{act.get('name', aid)}' — "
                            f"duration from {original_duration}d to {new_duration}d "
                            f"with {config.shift_differential_pct}% shift premium"
                        ),
                        perturbation_type="shift",
                        activities=modified,
                        changes=[change],
                    )
                )

        # Weekend work: 6-day and 7-day weeks
        if config.allow_weekend_work and original_duration > 3:
            for work_days_per_week, label in [(6, "6-day week"), (7, "7-day week")]:
                ratio = 5.0 / work_days_per_week
                new_duration_wd = max(1, round(original_duration * ratio))
                if new_duration_wd >= original_duration:
                    continue

                modified = copy.deepcopy(activities)
                act_idx = _activity_index(modified)
                target = act_idx.get(aid)
                if target is not None:
                    target["duration_days"] = new_duration_wd
                    target["work_days_per_week"] = work_days_per_week

                    change = ChangeDescription(
                        activity_id=aid,
                        field="duration_days",
                        original_value=original_duration,
                        new_value=new_duration_wd,
                        reason=f"{label}: duration {original_duration}d -> {new_duration_wd}d",
                    )

                    scenarios.append(
                        Scenario(
                            id=str(uuid.uuid4()),
                            name=f"{label}: {act.get('name', aid)}",
                            description=(
                                f"Extend to {label} for '{act.get('name', aid)}' — "
                                f"duration from {original_duration}d to {new_duration_wd}d"
                            ),
                            perturbation_type="shift",
                            activities=modified,
                            changes=[change],
                        )
                    )

    return scenarios


def _generate_resequence_scenarios(
    activities: list[dict],
    critical_ids: set[str],
    near_critical_ids: set[str],
    config: OptimizationConfig,
) -> list[Scenario]:
    """Generate resequencing scenarios by converting FS to SS+lag on critical path.

    Looks for FS relationships between critical activities where a start-to-start
    relationship with a lag of 30% predecessor duration might allow partial
    overlap without creating cycles.
    """
    if not config.allow_resequencing:
        return []

    scenarios: list[Scenario] = []
    idx = _activity_index(activities)

    for act in activities:
        aid = str(act["id"])
        if aid not in critical_ids:
            continue

        rels = act.get("relationships", [])
        for _rel_idx_num, rel in enumerate(rels):
            pred_id = str(rel.get("predecessor_id", ""))
            rel_type = rel.get("type", "FS").upper()

            if rel_type != "FS":
                continue
            if pred_id not in critical_ids and pred_id not in near_critical_ids:
                continue
            if pred_id not in idx:
                continue

            pred = idx[pred_id]
            pred_duration = int(pred.get("duration_days", 0))
            if pred_duration <= 0:
                continue

            # Proposed SS lag = 30% of predecessor duration (at least 1 day)
            lag = max(1, round(pred_duration * 0.3))

            # Build modified activities
            modified = copy.deepcopy(activities)
            mod_idx = _activity_index(modified)
            target = mod_idx.get(aid)
            if target is None:
                continue

            # Find and modify the relationship
            target_rels = target.get("relationships", [])
            rel_modified = False
            for i, r in enumerate(target_rels):
                if (
                    str(r.get("predecessor_id", "")) == pred_id
                    and r.get("type", "FS").upper() == "FS"
                ):
                    target_rels[i] = {
                        "predecessor_id": pred_id,
                        "type": "SS",
                        "lag": lag,
                    }
                    rel_modified = True
                    break

            if not rel_modified:
                continue

            target["relationships"] = target_rels

            # Verify no cycle is created
            if _has_cycle(modified):
                continue

            change = ChangeDescription(
                activity_id=aid,
                field="relationships",
                original_value=f"FS to {pred_id} (lag 0)",
                new_value=f"SS to {pred_id} (lag {lag}d)",
                reason=f"Resequence: overlap with predecessor '{pred.get('name', pred_id)}' "
                f"by starting after {lag}d (30% of {pred_duration}d predecessor)",
            )

            scenarios.append(
                Scenario(
                    id=str(uuid.uuid4()),
                    name=f"Resequence: {act.get('name', aid)} || {pred.get('name', pred_id)}",
                    description=(
                        f"Convert FS to SS+{lag}d between "
                        f"'{pred.get('name', pred_id)}' and '{act.get('name', aid)}' "
                        f"to allow partial overlap"
                    ),
                    perturbation_type="resequence",
                    activities=modified,
                    changes=[change],
                )
            )

    return scenarios


def _generate_split_scenarios(
    activities: list[dict],
    critical_ids: set[str],
    config: OptimizationConfig,
) -> list[Scenario]:
    """Generate activity-splitting scenarios for long critical activities.

    Splits activities with duration > 10 days into two phases (60/40 split)
    and allows the successor to start SS with Phase 2.
    """
    if not config.allow_splitting:
        return []

    scenarios: list[Scenario] = []
    successor_map = _get_successor_map(activities)

    for act in activities:
        aid = str(act["id"])
        if aid not in critical_ids:
            continue

        original_duration = int(act.get("duration_days", 0))
        if original_duration <= 10:
            continue

        phase1_duration = max(1, round(original_duration * 0.6))
        phase2_duration = max(1, original_duration - phase1_duration)

        phase1_id = f"{aid}_p1"
        phase2_id = f"{aid}_p2"

        # Build modified activities list: remove original, add two phases
        modified = [a for a in copy.deepcopy(activities) if str(a["id"]) != aid]

        # Phase 1 inherits all predecessors of the original
        phase1 = {
            **copy.deepcopy(act),
            "id": phase1_id,
            "name": f"{act.get('name', aid)} (Phase 1)",
            "duration_days": phase1_duration,
        }

        # Phase 2 depends on Phase 1 with FS
        phase2_rels = [{"predecessor_id": phase1_id, "type": "FS", "lag": 0}]
        phase2 = {
            "id": phase2_id,
            "name": f"{act.get('name', aid)} (Phase 2)",
            "duration_days": phase2_duration,
            "relationships": phase2_rels,
            "predecessors": [],
            "crew_size": act.get("crew_size", 0),
        }

        modified.append(phase1)
        modified.append(phase2)

        # Repoint successors of the original to Phase 2 (or SS with Phase 2)
        succs = successor_map.get(aid, [])
        for mod_act in modified:
            mod_aid = str(mod_act["id"])
            if mod_aid not in succs:
                continue

            # Update relationships pointing to the original activity
            new_rels = []
            for rel in mod_act.get("relationships", []):
                if str(rel.get("predecessor_id", "")) == aid:
                    # Point to phase2 with SS and a lag of 1 day
                    new_rels.append(
                        {
                            "predecessor_id": phase2_id,
                            "type": "SS",
                            "lag": 1,
                        }
                    )
                else:
                    new_rels.append(rel)
            mod_act["relationships"] = new_rels

            # Also update simple predecessors list
            new_preds = []
            for p in mod_act.get("predecessors", []):
                if str(p) == aid:
                    # Don't add to simple preds — we use relationships
                    pass
                else:
                    new_preds.append(p)
            mod_act["predecessors"] = new_preds

        # Verify no cycle
        if _has_cycle(modified):
            continue

        change = ChangeDescription(
            activity_id=aid,
            field="split",
            original_value=f"{original_duration}d single activity",
            new_value=f"Phase 1 ({phase1_duration}d) + Phase 2 ({phase2_duration}d)",
            reason=("Split long activity into two phases to allow successor overlap with Phase 2"),
        )

        scenarios.append(
            Scenario(
                id=str(uuid.uuid4()),
                name=f"Split: {act.get('name', aid)}",
                description=(
                    f"Split '{act.get('name', aid)}' ({original_duration}d) into "
                    f"Phase 1 ({phase1_duration}d) + Phase 2 ({phase2_duration}d) "
                    f"with SS successor linkage"
                ),
                perturbation_type="split",
                activities=modified,
                changes=[change],
            )
        )

    return scenarios


def _generate_combined_scenarios(
    base_scenarios: list[ScenarioResult],
    activities: list[dict],
    max_combinations: int = 20,
) -> list[Scenario]:
    """Generate combined scenarios by merging non-conflicting pairs.

    Takes the top 5 scenarios by each objective (duration, cost, risk),
    combines pairs that modify different activities, and caps at
    max_combinations total.
    """
    if len(base_scenarios) < 2:
        return []

    # Collect the top 5 by each objective (smallest first)
    by_duration = sorted(base_scenarios, key=lambda r: r.duration_days)[:5]
    by_cost = sorted(base_scenarios, key=lambda r: r.cost_delta)[:5]
    by_risk = sorted(base_scenarios, key=lambda r: r.risk_score)[:5]

    candidates = {r.scenario.id: r for r in by_duration + by_cost + by_risk}
    candidate_list = list(candidates.values())

    def _changed_activity_ids(result: ScenarioResult) -> set[str]:
        return {c.activity_id for c in result.scenario.changes}

    combined: list[Scenario] = []
    seen_pairs: set[frozenset[str]] = set()

    for a, b in itertools.combinations(candidate_list, 2):
        if len(combined) >= max_combinations:
            break

        pair_key = frozenset([a.scenario.id, b.scenario.id])
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        ids_a = _changed_activity_ids(a)
        ids_b = _changed_activity_ids(b)

        # Skip if they modify the same activities
        if ids_a & ids_b:
            continue

        # Merge: start from a's activities, apply b's changes
        merged_activities = copy.deepcopy(a.scenario.activities)
        merged_idx = _activity_index(merged_activities)

        # Apply b's changes onto the merged set
        b_idx = _activity_index(b.scenario.activities)
        for change in b.scenario.changes:
            target = merged_idx.get(change.activity_id)
            b_act = b_idx.get(change.activity_id)
            if target is not None and b_act is not None:
                # Copy the entire modified activity from b's scenario
                for key, val in b_act.items():
                    target[key] = copy.deepcopy(val)

        merged_changes = list(a.scenario.changes) + list(b.scenario.changes)

        combined.append(
            Scenario(
                id=str(uuid.uuid4()),
                name=f"Combined: {a.scenario.name} + {b.scenario.name}",
                description=(
                    f"Combining {a.scenario.perturbation_type} on "
                    f"{', '.join(ids_a)} with {b.scenario.perturbation_type} on "
                    f"{', '.join(ids_b)}"
                ),
                perturbation_type="combined",
                activities=merged_activities,
                changes=merged_changes,
            )
        )

    return combined


# ---------------------------------------------------------------------------
# Scenario evaluation
# ---------------------------------------------------------------------------


def _calculate_cost_delta(
    changes: list[ChangeDescription],
    activities: list[dict],
    context: ProjectContext,
) -> Decimal:
    """Calculate the cost delta from a set of changes.

    Cost factors:
    - Crew size changes: (new_crew - old_crew) * new_duration * hours_per_day * hourly_rate
    - Second shift: original_duration * crew_size * hours_per_day * hourly_rate * shift_differential
    - Weekend work: extra_days * crew_size * hours_per_day * hourly_rate * overtime_multiplier
    """
    HOURS_PER_DAY = Decimal("8")
    cost_delta = Decimal("0")
    idx = _activity_index(activities)

    crew_changes: dict[str, dict[str, Any]] = {}
    for change in changes:
        if change.field == "crew_size":
            crew_changes.setdefault(change.activity_id, {})["crew"] = (
                change.original_value,
                change.new_value,
            )
        elif change.field == "duration_days":
            crew_changes.setdefault(change.activity_id, {})["duration"] = (
                change.original_value,
                change.new_value,
            )

    for aid, info in crew_changes.items():
        act = idx.get(aid, {})
        if "crew" in info:
            old_crew, new_crew = info["crew"]
            # Use new duration if available, otherwise original
            if "duration" in info:
                _, new_dur = info["duration"]
            else:
                new_dur = int(act.get("duration_days", 0))

            crew_diff = new_crew - old_crew
            cost_delta += (
                Decimal(str(crew_diff))
                * Decimal(str(new_dur))
                * HOURS_PER_DAY
                * context.hourly_rate
            )

    # Shift-related costs
    for change in changes:
        act = idx.get(change.activity_id, {})
        crew_size = act.get("crew_size", 1)
        if crew_size <= 0:
            crew_size = 1

        if "Second shift" in change.reason:
            original_duration = change.original_value
            differential = Decimal("0.15")  # default shift differential
            shift_cost = (
                Decimal(str(original_duration))
                * Decimal(str(crew_size))
                * HOURS_PER_DAY
                * context.hourly_rate
                * differential
            )
            cost_delta += shift_cost

        elif "day week" in change.reason:
            original_duration = change.original_value
            new_duration = change.new_value
            saved_days = original_duration - new_duration
            # Weekend premium
            overtime_mult = Decimal("1.5")
            weekend_cost = (
                Decimal(str(saved_days))
                * Decimal(str(crew_size))
                * HOURS_PER_DAY
                * context.hourly_rate
                * (overtime_mult - Decimal("1"))
            )
            cost_delta += weekend_cost

    return cost_delta.quantize(Decimal("0.01"))


def _calculate_risk_score(
    cpm_result: dict,
    baseline_duration: int,
) -> float:
    """Compute a composite risk score from schedule characteristics.

    Risk components (all normalized to [0, 1]):
    - near_critical_ratio: fraction of non-critical activities with float <= 5
    - resource_density: inverse of average float (tighter schedule = higher risk)
    - compression_ratio: how much shorter vs baseline (more compression = more risk)
    """
    enriched = cpm_result["activities"]
    total = len(enriched)

    if total == 0:
        return 0.0

    critical_count = sum(1 for a in enriched if a.get("is_critical", False))
    non_critical = total - critical_count

    # Near-critical ratio
    near_critical = sum(
        1
        for a in enriched
        if not a.get("is_critical", False)
        and 0 < a.get("total_float", 0) <= NEAR_CRITICAL_THRESHOLD_DAYS
    )
    near_critical_ratio = near_critical / max(non_critical, 1)

    # Average float for non-critical activities (lower = riskier)
    float_values = [a.get("total_float", 0) for a in enriched if not a.get("is_critical", False)]
    avg_float = sum(float_values) / max(len(float_values), 1) if float_values else 0.0
    # Normalize: 0 float -> risk 1.0, 20+ float -> risk ~0
    resource_density = max(0.0, min(1.0, 1.0 - (avg_float / 20.0)))

    # Compression ratio
    new_duration = cpm_result["project_duration"]
    if baseline_duration > 0:
        compression = max(0.0, (baseline_duration - new_duration) / baseline_duration)
    else:
        compression = 0.0
    # Compression > 30% gets exponentially riskier
    compression_risk = min(1.0, compression * 2.0) if compression <= 0.5 else 1.0

    risk = 0.35 * near_critical_ratio + 0.35 * resource_density + 0.30 * compression_risk

    return round(min(1.0, max(0.0, risk)), 4)


# ---------------------------------------------------------------------------
# IG-08: Weather-aware activity classification
# ---------------------------------------------------------------------------

# Keywords in activity names that suggest outdoor / weather-sensitive work
_OUTDOOR_ACTIVITY_KEYWORDS: dict[str, str] = {
    "concrete": "concrete_pour",
    "pour": "concrete_pour",
    "slab": "concrete_pour",
    "foundation": "concrete_pour",
    "footing": "concrete_pour",
    "crane": "crane_operation",
    "steel": "crane_operation",
    "erect": "crane_operation",
    "iron": "crane_operation",
    "excavat": "excavation",
    "grade": "excavation",
    "backfill": "excavation",
    "trench": "excavation",
    "earthwork": "excavation",
    "sitework": "excavation",
    "roof": "roofing",
    "shingle": "roofing",
    "membrane": "roofing",
    "paint": "painting_exterior",
    "exterior": "painting_exterior",
    "paving": "excavation",
    "landscape": "excavation",
}


def _classify_weather_sensitivity(activity: dict) -> str | None:
    """Determine the weather sensitivity type for an activity based on its name.

    Returns a weather impact function key (e.g., 'concrete_pour', 'crane_operation')
    or None if the activity does not appear to be weather-sensitive.
    """
    name = (activity.get("name") or "").lower()
    wbs = (activity.get("wbs_code") or "").lower()
    search_text = f"{name} {wbs}"

    for keyword, impact_type in _OUTDOOR_ACTIVITY_KEYWORDS.items():
        if keyword in search_text:
            return impact_type
    return None


async def _estimate_weather_delays(
    activities: list[dict],
    critical_ids: set[str],
    context: ProjectContext,
) -> int:
    """Estimate weather delay days for critical outdoor activities.

    For each critical activity that appears to be weather-sensitive,
    queries the weather service for the activity's date range and checks
    whether conditions allow the work type.  Sums up the estimated
    delay days across all affected critical activities (avoiding double-
    counting dates).

    Returns 0 if location is not available or weather data cannot be fetched.
    """
    if not context.location:
        return 0

    try:
        from app.services.scheduling.weather_service import (
            IMPACT_FUNCTIONS,
            get_weather_forecast,
            weather_impact_score,
        )
    except ImportError:
        return 0

    # Collect critical activities with weather sensitivity
    idx = _activity_index(activities)
    sensitive_activities: list[tuple[dict, str]] = []
    for aid in critical_ids:
        act = idx.get(aid)
        if act is None:
            continue
        sensitivity = _classify_weather_sensitivity(act)
        if sensitivity is not None:
            sensitive_activities.append((act, sensitivity))

    if not sensitive_activities:
        return 0

    # Determine the overall date range we need weather for
    all_start_dates: list[str] = []
    all_end_dates: list[str] = []
    for act, _ in sensitive_activities:
        start = act.get("start_date", "")
        end = act.get("end_date", "") or act.get("finish_date", "")
        if start:
            all_start_dates.append(str(start))
        if end:
            all_end_dates.append(str(end))

    if not all_start_dates or not all_end_dates:
        # Use context start_date and estimate range from activities
        start_str = context.start_date.isoformat()
        # Estimate end from maximum duration
        max_dur = max((int(a.get("duration_days", 0)) for a, _ in sensitive_activities), default=30)
        from datetime import timedelta as _td

        end_date = context.start_date + _td(days=max_dur)
        end_str = end_date.isoformat()
    else:
        start_str = min(all_start_dates)
        end_str = max(all_end_dates)

    # Geocode location — we need lat/lon for the weather API
    # Use a simple approach: try to parse "lat,lon" from location, or skip
    latitude, longitude = None, None
    try:
        # get_weather_impact geocodes internally, but we need the raw forecast
        # Try a basic comma-separated lat,lon format first
        parts = context.location.split(",")
        if len(parts) == 2:
            latitude = float(parts[0].strip())
            longitude = float(parts[1].strip())
    except (ValueError, TypeError):
        pass

    if latitude is None or longitude is None:
        # Cannot geocode — return 0 delay days
        logger.debug(
            "Cannot determine lat/lon from location '%s' for weather delays",
            context.location,
        )
        return 0

    # Fetch weather forecast
    try:
        weather_data = await get_weather_forecast(latitude, longitude, start_str, end_str)
    except Exception as exc:
        logger.debug("Weather forecast unavailable for schedule optimization: %s", exc)
        return 0

    if not weather_data:
        return 0

    # Build date -> weather lookup
    weather_by_date = {w.get("date", ""): w for w in weather_data}

    # Count impacted days (unique dates across all critical activities)
    impacted_dates: set[str] = set()
    for act, sensitivity in sensitive_activities:
        impact_fn = IMPACT_FUNCTIONS.get(sensitivity, weather_impact_score)
        start = act.get("start_date", "")
        end = act.get("end_date", "") or act.get("finish_date", "")
        if not start or not end:
            continue

        try:
            from datetime import datetime as _dt
            from datetime import timedelta as _td

            current = _dt.strptime(str(start), "%Y-%m-%d")
            end_dt = _dt.strptime(str(end), "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        while current <= end_dt:
            date_str = current.strftime("%Y-%m-%d")
            weather = weather_by_date.get(date_str)
            if weather is not None:
                try:
                    impact = impact_fn(weather)
                    if not impact.allowed:
                        impacted_dates.add(date_str)
                except Exception as e:
                    logger.warning(
                        "Weather impact check failed for date, treating as impacted (fail-safe): %s",
                        e,
                    )
                    impacted_dates.add(date_str)
            current += _td(days=1)

    return len(impacted_dates)


async def _evaluate_scenario(
    scenario: Scenario,
    context: ProjectContext,
    baseline_duration: int,
) -> ScenarioResult:
    """Evaluate a single scenario by running CPM and computing metrics.

    IG-08: When ``context.location`` is provided, estimates weather delay
    days for critical outdoor activities and includes them in the result.
    """
    try:
        cpm_result = await calculate_cpm(scenario.activities)
    except ValueError:
        # Cycle or invalid data — return a heavily penalized result
        return ScenarioResult(
            scenario=scenario,
            duration_days=baseline_duration * 2,
            cost_delta=Decimal("999999.99"),
            risk_score=1.0,
            critical_path_count=0,
            near_critical_count=0,
            weather_delay_days=0,
        )

    duration = cpm_result["project_duration"]
    critical_ids, near_critical_ids = _classify_activities(cpm_result)

    cost_delta = _calculate_cost_delta(scenario.changes, scenario.activities, context)
    risk_score = _calculate_risk_score(cpm_result, baseline_duration)

    # SV-06: Check resource conflicts using resource_leveler and
    # incorporate conflict count into the risk score.
    resource_conflict_count = 0
    try:
        from app.services.scheduling.resource_leveler import check_resource_conflicts

        # Build max_resources from the scenario activities
        resource_caps: dict[str, int] = {}
        for act in scenario.activities:
            resources = act.get("resources", {})
            for res_type, qty in resources.items():
                resource_caps[res_type] = max(resource_caps.get(res_type, 0), int(qty))

        if resource_caps:
            conflicts = check_resource_conflicts(scenario.activities, resource_caps)
            resource_conflict_count = len(conflicts)
            # Add resource conflict penalty: each conflict adds 0.02 to risk (capped)
            resource_penalty = min(0.20, resource_conflict_count * 0.02)
            risk_score = min(1.0, risk_score + resource_penalty)
    except Exception as exc:
        logger.debug("Resource conflict detection failed: %s", exc)

    # IG-08: Estimate weather delay days for critical outdoor activities
    weather_delay_days = 0
    if context.location:
        try:
            weather_delay_days = await _estimate_weather_delays(
                scenario.activities, critical_ids, context
            )
        except Exception as exc:
            logger.debug("Weather delay estimation failed: %s", exc)

    return ScenarioResult(
        scenario=scenario,
        duration_days=duration,
        cost_delta=cost_delta,
        risk_score=risk_score,
        critical_path_count=len(critical_ids),
        near_critical_count=len(near_critical_ids),
        weather_delay_days=weather_delay_days,
    )


# ---------------------------------------------------------------------------
# Pareto front & ranking
# ---------------------------------------------------------------------------


def _compute_pareto_front(results: list[ScenarioResult]) -> list[ScenarioResult]:
    """Compute the Pareto-optimal front across three objectives.

    A result A dominates B if A is <= B on all objectives and strictly < on
    at least one.  Objectives: duration_days (min), cost_delta (min),
    risk_score (min).
    """
    if len(results) <= 1:
        for r in results:
            r.is_pareto_optimal = True
        return list(results)

    def _dominates(a: ScenarioResult, b: ScenarioResult) -> bool:
        """Return True if a dominates b."""
        dur_a, cost_a, risk_a = a.duration_days, float(a.cost_delta), a.risk_score
        dur_b, cost_b, risk_b = b.duration_days, float(b.cost_delta), b.risk_score

        all_leq = dur_a <= dur_b and cost_a <= cost_b and risk_a <= risk_b
        any_lt = dur_a < dur_b or cost_a < cost_b or risk_a < risk_b

        return all_leq and any_lt

    pareto: list[ScenarioResult] = []

    for candidate in results:
        is_dominated = False
        for other in results:
            if other is candidate:
                continue
            if _dominates(other, candidate):
                is_dominated = True
                break

        if not is_dominated:
            candidate.is_pareto_optimal = True
            pareto.append(candidate)

    return pareto


def _rank_scenarios(
    results: list[ScenarioResult],
    weights: dict[str, float],
) -> list[ScenarioResult]:
    """Rank scenarios by weighted normalized score (lower = better).

    Normalizes each objective to [0, 1] across the candidate set, then
    computes a weighted sum.
    """
    if not results:
        return []

    if len(results) == 1:
        results[0].rank = 1
        return results

    durations = [r.duration_days for r in results]
    costs = [float(r.cost_delta) for r in results]
    risks = [r.risk_score for r in results]

    dur_min, dur_max = min(durations), max(durations)
    cost_min, cost_max = min(costs), max(costs)
    risk_min, risk_max = min(risks), max(risks)

    dur_range = dur_max - dur_min if dur_max != dur_min else 1.0
    cost_range = cost_max - cost_min if cost_max != cost_min else 1.0
    risk_range = risk_max - risk_min if risk_max != risk_min else 1.0

    w_dur = weights.get("duration", 0.4)
    w_cost = weights.get("cost", 0.35)
    w_risk = weights.get("risk", 0.25)

    scored: list[tuple[float, ScenarioResult]] = []
    for r in results:
        norm_dur = (r.duration_days - dur_min) / dur_range
        norm_cost = (float(r.cost_delta) - cost_min) / cost_range
        norm_risk = (r.risk_score - risk_min) / risk_range

        score = w_dur * norm_dur + w_cost * norm_cost + w_risk * norm_risk
        scored.append((score, r))

    scored.sort(key=lambda x: x[0])

    ranked: list[ScenarioResult] = []
    for i, (_, r) in enumerate(scored):
        r.rank = i + 1
        ranked.append(r)

    return ranked


# ---------------------------------------------------------------------------
# SV-08: Historical learning from cross-project analytics
# ---------------------------------------------------------------------------


async def _adjust_from_historical(
    scenarios: list[ScenarioResult],
    db: Any | None = None,
    org_id: Any | None = None,
) -> list[ScenarioResult]:
    """Adjust scenario rankings based on historical schedule accuracy data.

    Queries cross_project_analytics for past optimization outcomes.
    If historical data shows certain perturbation types work better for
    specific activity types (e.g., crew increases are more effective than
    shift changes for concrete work), those scenarios get a ranking boost
    (lower risk_score).

    This is optional — if no historical data is available or db/org_id
    are not provided, scenarios are returned unchanged.
    """
    if db is None or org_id is None:
        return scenarios

    try:
        from sqlalchemy import select

        from app.models.cross_project import CrossProjectInsight

        # Look for cached optimization history insights
        stmt = (
            select(CrossProjectInsight)
            .where(
                CrossProjectInsight.org_id == org_id,
                CrossProjectInsight.insight_type == "schedule_optimization_history",
            )
            .order_by(CrossProjectInsight.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        insight = result.scalar_one_or_none()

        if insight is None or not insight.result:
            return scenarios

        # Expected format: {perturbation_type: {effectiveness_multiplier: float}}
        # e.g., {"crew_size": {"effectiveness": 1.2}, "shift": {"effectiveness": 0.8}}
        history = insight.result
        perturbation_effectiveness: dict[str, float] = {}
        for ptype, data in history.items():
            if isinstance(data, dict) and "effectiveness" in data:
                perturbation_effectiveness[ptype] = float(data["effectiveness"])

        if not perturbation_effectiveness:
            return scenarios

        # Adjust risk scores based on historical effectiveness
        for sr in scenarios:
            ptype = sr.scenario.perturbation_type
            effectiveness = perturbation_effectiveness.get(ptype)
            if effectiveness is not None and effectiveness > 0:
                # Higher effectiveness = lower risk. Scale risk_score inversely.
                # effectiveness > 1.0 reduces risk, < 1.0 increases it.
                adjustment = 1.0 / effectiveness
                sr.risk_score = round(min(1.0, max(0.0, sr.risk_score * adjustment)), 4)

        logger.info(
            "Applied historical learning to %d scenarios from org %s (perturbation types: %s)",
            len(scenarios),
            org_id,
            list(perturbation_effectiveness.keys()),
        )
    except Exception as exc:
        logger.debug("Historical learning adjustment failed (non-fatal): %s", exc)

    return scenarios


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def optimize_schedule(
    activities: list[dict],
    config: OptimizationConfig | None = None,
    context: ProjectContext | None = None,
    db: Any | None = None,
    org_id: Any | None = None,
) -> OptimizationResult:
    """Run generative schedule optimization.

    1. Compute baseline CPM to identify critical and near-critical activities.
    2. Generate perturbation scenarios from four strategy dimensions.
    3. Generate promising combinations of non-conflicting scenarios.
    4. Evaluate each scenario through CPM + cost + risk scoring.
    5. Compute the Pareto front and rank by weighted objectives.
    6. (SV-08) Optionally adjust rankings from historical cross-project data.

    Parameters
    ----------
    activities:
        List of activity dicts compatible with ``calculate_cpm()``.
    config:
        Optimization knobs. Defaults are used if omitted.
    context:
        Project metadata for cost calculations. Defaults are used if omitted.

    Returns
    -------
    OptimizationResult with baseline metrics, evaluated scenarios,
    Pareto-optimal front, and best-of picks for duration/cost/balanced.
    """
    t_start = time.monotonic()

    if config is None:
        config = OptimizationConfig()
    if context is None:
        context = ProjectContext(project_id="default")

    # --- Step 1: Baseline CPM --------------------------------------------------
    baseline_result = await calculate_cpm(activities)
    baseline_duration = baseline_result["project_duration"]

    # Baseline cost: sum of crew_size * duration * hours * hourly_rate for all
    HOURS_PER_DAY = Decimal("8")
    baseline_cost = Decimal("0")
    for act in activities:
        crew = act.get("crew_size", 0)
        dur = int(act.get("duration_days", 0))
        if crew > 0 and dur > 0:
            baseline_cost += (
                Decimal(str(crew)) * Decimal(str(dur)) * HOURS_PER_DAY * context.hourly_rate
            )

    critical_ids, near_critical_ids = _classify_activities(baseline_result)

    logger.info(
        "Optimization baseline: duration=%d days, critical=%d, near_critical=%d, total=%d",
        baseline_duration,
        len(critical_ids),
        len(near_critical_ids),
        len(activities),
    )

    # --- Step 2: Generate scenarios -------------------------------------------
    all_scenarios: list[Scenario] = []

    crew_scenarios = _generate_crew_size_scenarios(activities, critical_ids, config)
    all_scenarios.extend(crew_scenarios)

    shift_scenarios = _generate_shift_scenarios(activities, critical_ids, config)
    all_scenarios.extend(shift_scenarios)

    reseq_scenarios = _generate_resequence_scenarios(
        activities, critical_ids, near_critical_ids, config
    )
    all_scenarios.extend(reseq_scenarios)

    split_scenarios = _generate_split_scenarios(activities, critical_ids, config)
    all_scenarios.extend(split_scenarios)

    logger.info(
        "Generated %d scenarios: %d crew, %d shift, %d resequence, %d split",
        len(all_scenarios),
        len(crew_scenarios),
        len(shift_scenarios),
        len(reseq_scenarios),
        len(split_scenarios),
    )

    # --- Step 3: Evaluate base scenarios (cap to max_scenarios) ----------------
    # Truncate to max_scenarios before evaluation to bound compute
    if len(all_scenarios) > config.max_scenarios:
        all_scenarios = all_scenarios[: config.max_scenarios]

    # SV-07: Parallel evaluation with asyncio.gather(), capped at 10 concurrent
    _eval_semaphore = asyncio.Semaphore(10)

    async def _eval_with_semaphore(scenario: Scenario) -> ScenarioResult:
        async with _eval_semaphore:
            return await _evaluate_scenario(scenario, context, baseline_duration)

    base_results: list[ScenarioResult] = list(
        await asyncio.gather(*[_eval_with_semaphore(s) for s in all_scenarios])
    )

    # --- Step 4: Generate and evaluate combined scenarios ---------------------
    combined_scenarios = _generate_combined_scenarios(base_results, activities)

    combined_budget = config.max_scenarios - len(base_results)
    if combined_budget > 0:
        combined_scenarios = combined_scenarios[:combined_budget]

    combined_results: list[ScenarioResult] = list(
        await asyncio.gather(*[_eval_with_semaphore(s) for s in combined_scenarios])
    )

    all_results = base_results + combined_results

    # Filter out scenarios that are worse than baseline on all dimensions
    valid_results = [
        r
        for r in all_results
        if r.duration_days <= baseline_duration or r.cost_delta < Decimal("0") or r.risk_score < 0.5
    ]

    # If filtering removed everything, keep all results
    if not valid_results:
        valid_results = all_results

    # --- Step 5: Historical learning (SV-08) ---------------------------------
    valid_results = await _adjust_from_historical(valid_results, db, org_id)

    # --- Step 6: Pareto front & ranking --------------------------------------
    pareto_front = _compute_pareto_front(valid_results)
    ranked = _rank_scenarios(valid_results, config.weights)

    # Identify best-of picks
    best_duration = min(ranked, key=lambda r: r.duration_days) if ranked else None
    best_cost = min(ranked, key=lambda r: r.cost_delta) if ranked else None
    best_balanced = ranked[0] if ranked else None

    t_end = time.monotonic()
    processing_ms = int((t_end - t_start) * 1000)

    logger.info(
        "Optimization complete: %d scenarios evaluated, %d Pareto-optimal, "
        "best duration=%dd (delta=%d), processing=%dms",
        len(valid_results),
        len(pareto_front),
        best_duration.duration_days if best_duration else baseline_duration,
        (best_duration.duration_days - baseline_duration) if best_duration else 0,
        processing_ms,
    )

    return OptimizationResult(
        baseline_duration=baseline_duration,
        baseline_cost=baseline_cost.quantize(Decimal("0.01")),
        scenarios=ranked,
        pareto_front=pareto_front,
        best_duration=best_duration,
        best_cost=best_cost,
        best_balanced=best_balanced,
        processing_time_ms=processing_ms,
    )


# ---------------------------------------------------------------------------
# Stubs for planned features
# ---------------------------------------------------------------------------


async def what_if_analysis(
    activities: list[dict],
    changes: list[dict],
    **kwargs: Any,
) -> dict[str, Any]:
    """Stub: what-if analysis not yet implemented."""
    raise NotImplementedError("What-if analysis not yet implemented")


async def level_resources(
    activities: list[dict],
    resource_limits: dict[str, int],
    **kwargs: Any,
) -> dict[str, Any]:
    """Stub: resource leveling not yet implemented."""
    raise NotImplementedError("Resource leveling not yet implemented")
