"""Resource conflict detection and heuristic leveling for construction schedules.

Provides two core functions:
1. ``check_resource_conflicts`` — identifies periods where resource demand
   exceeds capacity across overlapping activities.
2. ``level_resources`` — delays non-critical activities (those with float > 0)
   to reduce peak demand below capacity limits.

Both functions operate on pure activity dicts with CPM-computed ``early_start``,
``early_finish``, and ``total_float`` fields.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Safety bound to prevent infinite iteration in the leveling heuristic
_MAX_LEVELING_ITERATIONS = 5000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ResourceConflict:
    """Describes a period where resource demand exceeds capacity."""

    period_start: int
    period_end: int
    resource_type: str
    demand: int
    capacity: int
    conflicting_activity_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_daily_demand(
    activities: list[dict],
) -> dict[str, dict[int, list[tuple[str, int]]]]:
    """Build resource_type -> {day -> [(activity_id, demand)]} mapping.

    Each activity is assumed to occupy [early_start, early_start + duration_days).
    Resource demand is extracted from the ``resources`` dict on each activity:
    ``{"crane": 2, "electrician": 4}``.
    """
    demand: dict[str, dict[int, list[tuple[str, int]]]] = defaultdict(lambda: defaultdict(list))

    for act in activities:
        aid = str(act["id"])
        start = int(act.get("early_start", 0))
        duration = int(act.get("duration_days", 0))
        resources = act.get("resources", {})

        for res_type, units in resources.items():
            units_int = int(units)
            if units_int <= 0:
                continue
            for day in range(start, start + duration):
                demand[res_type][day].append((aid, units_int))

    return demand


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_resource_conflicts(
    activities: list[dict],
    max_resources: dict[str, int] | None = None,
) -> list[ResourceConflict]:
    """Identify time periods where resource demand exceeds capacity.

    Parameters
    ----------
    activities:
        Activity dicts with ``early_start``, ``duration_days``, and
        ``resources`` (dict[str, int]).
    max_resources:
        Maximum available units per resource type.  If ``None``, no
        capacity constraints are applied (returns empty list).

    Returns
    -------
    List of ``ResourceConflict`` objects sorted by (resource_type, period_start).
    """
    if not activities or not max_resources:
        return []

    demand_map = _build_daily_demand(activities)
    conflicts: list[ResourceConflict] = []

    for res_type, capacity in max_resources.items():
        day_demand = demand_map.get(res_type, {})
        if not day_demand:
            continue

        # Walk through days in order, coalescing consecutive over-capacity days
        sorted_days = sorted(day_demand.keys())
        conflict_start: int | None = None
        conflict_ids: set[str] = set()
        peak_demand = 0

        for day in sorted_days:
            entries = day_demand[day]
            total = sum(d for _, d in entries)

            if total > capacity:
                if conflict_start is None:
                    conflict_start = day
                    conflict_ids = set()
                    peak_demand = 0
                for aid, _ in entries:
                    conflict_ids.add(aid)
                peak_demand = max(peak_demand, total)
            else:
                if conflict_start is not None:
                    conflicts.append(
                        ResourceConflict(
                            period_start=conflict_start,
                            period_end=day - 1,
                            resource_type=res_type,
                            demand=peak_demand,
                            capacity=capacity,
                            conflicting_activity_ids=sorted(conflict_ids),
                        )
                    )
                    conflict_start = None

        # Close any trailing conflict
        if conflict_start is not None:
            conflicts.append(
                ResourceConflict(
                    period_start=conflict_start,
                    period_end=sorted_days[-1],
                    resource_type=res_type,
                    demand=peak_demand,
                    capacity=capacity,
                    conflicting_activity_ids=sorted(conflict_ids),
                )
            )

    conflicts.sort(key=lambda c: (c.resource_type, c.period_start))

    logger.info(
        "Resource conflict check: %d conflicts found across %d resource types",
        len(conflicts),
        len(max_resources),
    )

    return conflicts


def level_resources(
    activities: list[dict],
    max_resources: dict[str, int],
) -> list[dict]:
    """Level resources by delaying non-critical activities.

    Uses a simple heuristic: iteratively finds the first day with a conflict,
    identifies the non-critical activity with the most float, and delays it
    by 1 day.  Repeats until no conflicts remain or the iteration limit is
    reached.

    Parameters
    ----------
    activities:
        Activity dicts with ``early_start``, ``duration_days``,
        ``total_float``, and ``resources``.
    max_resources:
        Maximum available units per resource type.

    Returns
    -------
    Modified list of activity dicts with adjusted ``early_start`` and
    ``early_finish`` values.
    """
    if not activities or not max_resources:
        return list(activities)

    leveled = copy.deepcopy(activities)
    iterations = 0

    while iterations < _MAX_LEVELING_ITERATIONS:
        conflicts = check_resource_conflicts(leveled, max_resources)
        if not conflicts:
            break

        # Take the earliest conflict
        conflict = conflicts[0]

        # Among conflicting activities, find those with float > 0
        idx = {str(a["id"]): a for a in leveled}
        delayable = []
        for aid in conflict.conflicting_activity_ids:
            act = idx.get(aid)
            if act is None:
                continue
            tf = int(act.get("total_float", 0))
            if tf > 0:
                delayable.append((tf, aid))

        if not delayable:
            # No delayable activities — cannot resolve this conflict
            # Move on to the next conflict by breaking to prevent infinite loop
            break

        # Delay the activity with the highest float (most room to shift)
        delayable.sort(key=lambda x: -x[0])
        _, delay_aid = delayable[0]

        target = idx[delay_aid]
        target["early_start"] = int(target.get("early_start", 0)) + 1
        target["early_finish"] = target["early_start"] + int(target.get("duration_days", 0))
        target["total_float"] = max(0, int(target.get("total_float", 0)) - 1)

        iterations += 1

    if iterations >= _MAX_LEVELING_ITERATIONS:
        logger.warning(
            "Resource leveling hit iteration limit (%d); some conflicts may remain",
            _MAX_LEVELING_ITERATIONS,
        )
    else:
        logger.info("Resource leveling complete in %d iterations", iterations)

    return leveled
