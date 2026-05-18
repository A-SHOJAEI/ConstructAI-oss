"""Critical Path Method (CPM) engine for construction schedule analysis."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Work calendar support
# ---------------------------------------------------------------------------


class WorkCalendar:
    """A work calendar defining which days are workable and which are holidays."""

    def __init__(self, work_days: list[int] | set[int], holidays: set[str] | None = None):
        """
        Parameters
        ----------
        work_days:
            Weekday indices (0=Monday .. 6=Sunday) that are work days.
        holidays:
            Set of ISO date strings ("YYYY-MM-DD") that are non-work days
            regardless of weekday.
        """
        self.work_days: set[int] = set(work_days)
        self.holidays: set[str] = holidays or set()

    def is_work_day(self, d: date) -> bool:
        return d.weekday() in self.work_days and d.isoformat() not in self.holidays

    _MAX_ITERATIONS = 10_000

    def add_work_days(self, start: date, num_work_days: int) -> date:
        """Return the date that is *num_work_days* work days after *start*.

        *start* itself is not counted.  If *num_work_days* is 0 the start
        date is returned unchanged.
        """
        if num_work_days <= 0:
            return start
        if not self.work_days:
            raise ValueError("Calendar has no work days configured")
        current = start
        remaining = num_work_days
        iterations = 0
        while remaining > 0:
            if iterations >= self._MAX_ITERATIONS:
                raise ValueError(
                    f"add_work_days exceeded {self._MAX_ITERATIONS} iterations; "
                    f"check calendar configuration (holidays may block all work days)"
                )
            current += timedelta(days=1)
            if self.is_work_day(current):
                remaining -= 1
            iterations += 1
        return current

    def work_days_between(self, start: date, end: date) -> int:
        """Count work days between *start* (exclusive) and *end* (inclusive)."""
        if not self.work_days and start < end:
            raise ValueError("Calendar has no work days configured")
        count = 0
        current = start
        iterations = 0
        while current < end:
            if iterations >= self._MAX_ITERATIONS:
                raise ValueError(
                    f"work_days_between exceeded {self._MAX_ITERATIONS} iterations; "
                    f"date range too large or calendar misconfigured"
                )
            current += timedelta(days=1)
            if self.is_work_day(current):
                count += 1
            iterations += 1
        return count


DEFAULT_CALENDAR = WorkCalendar(work_days=[0, 1, 2, 3, 4])  # Mon-Fri, no holidays

# Supported relationship types
_VALID_REL_TYPES = {"FS", "SS", "FF", "SF"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_graph(
    activities: list[dict],
) -> tuple[
    dict[str, dict],
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, list[dict]],
]:
    """Build adjacency structures from activity list.

    Each activity may specify dependencies in two ways (both may co-exist):

    * ``predecessors`` – simple list of predecessor ids (treated as **FS**
      with ``lag=0``).
    * ``relationships`` – rich format::

          [{"predecessor_id": "A", "type": "FS", "lag": 5}, ...]

      Supported types: FS, SS, FF, SF.  Defaults: type=FS, lag=0.

    Returns
    -------
    activity_map :
        id -> activity dict
    successors :
        id -> list of successor ids
    predecessors :
        id -> list of predecessor ids
    rel_data :
        id -> list of relationship dicts
        ``[{"pred_id": str, "type": str, "lag": int}, ...]``
    """
    activity_map: dict[str, dict] = {}
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    rel_data: dict[str, list[dict]] = defaultdict(list)

    for act in activities:
        aid = str(act["id"])
        activity_map[aid] = {**act, "id": aid}

        seen_preds: set[str] = set()

        # ---- rich relationships -----------------------------------------
        for rel in act.get("relationships", []):
            pred_id = str(rel["predecessor_id"])
            rel_type = rel.get("type", "FS").upper()
            if rel_type not in _VALID_REL_TYPES:
                raise ValueError(
                    f"Activity {aid}: unsupported relationship type "
                    f"'{rel_type}'. Must be one of {sorted(_VALID_REL_TYPES)}."
                )
            lag = int(rel.get("lag", 0))
            rel_data[aid].append({"pred_id": pred_id, "type": rel_type, "lag": lag})
            seen_preds.add(pred_id)

        # ---- simple predecessors (backward-compatible) ------------------
        for p in act.get("predecessors", []):
            pred_id = str(p)
            if pred_id not in seen_preds:
                rel_data[aid].append({"pred_id": pred_id, "type": "FS", "lag": 0})
                seen_preds.add(pred_id)

        # Build flat adjacency lists (used for topo-sort, free-float, etc.)
        predecessors[aid] = list(seen_preds)

    for aid, preds in predecessors.items():
        for pred_id in preds:
            successors[pred_id].append(aid)

    return activity_map, successors, predecessors, rel_data


def _topological_sort(
    activity_map: dict[str, dict],
    predecessors: dict[str, list[str]],
) -> list[str]:
    """Kahn's algorithm for topological ordering."""
    in_degree: dict[str, int] = {aid: 0 for aid in activity_map}
    for aid, preds in predecessors.items():
        in_degree[aid] = len(preds)

    queue: deque[str] = deque(aid for aid, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    # Build forward adjacency for decrementing in-degree
    successors: dict[str, list[str]] = defaultdict(list)
    for aid, preds in predecessors.items():
        for pred_id in preds:
            successors[pred_id].append(aid)

    while queue:
        node = queue.popleft()
        order.append(node)
        for succ in successors[node]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(order) != len(activity_map):
        missing = set(activity_map) - set(order)
        raise ValueError(
            f"Schedule contains a dependency cycle involving activities: "
            f"{', '.join(sorted(missing))}. "
            f"Resolve circular dependencies before running CPM."
        )

    return order


def _get_duration(activity_map: dict[str, dict], aid: str) -> int:
    """Return the integer duration for an activity, defaulting to 0."""
    raw = activity_map[aid].get("duration_days")
    return int(raw) if raw is not None else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def calculate_cpm(
    activities: list[dict],
    calendars: dict[str, WorkCalendar] | None = None,
    project_start: date | None = None,
) -> dict:
    """Calculate Critical Path Method for a set of activities.

    Parameters
    ----------
    activities:
        List of dicts with keys: ``id``, ``name``, ``duration_days``,
        and one or both of:

        * ``predecessors`` (list of ids) -- simple form, treated as FS/lag=0.
        * ``relationships`` (list of dicts) -- rich form with
          ``predecessor_id``, ``type`` (FS|SS|FF|SF), ``lag``.
        * ``calendar_id`` (optional) -- key into *calendars* dict.
    calendars:
        Optional mapping of calendar id → WorkCalendar.  Used only when
        *project_start* is also provided, to convert day offsets to
        actual calendar dates.
    project_start:
        When provided (together with *calendars*), CPM results include
        ``start_date`` and ``finish_date`` fields as ISO date strings.

    Returns
    -------
    dict with:
        - activities: list with added early_start, early_finish, late_start,
          late_finish, total_float, is_critical
          (and optionally start_date, finish_date when project_start given)
        - critical_path: list of activity ids on the critical path
        - project_duration: total duration in days
        - critical_path_length: number of activities on critical path
    """
    if not activities:
        return {
            "activities": [],
            "critical_path": [],
            "project_duration": 0,
            "critical_path_length": 0,
        }

    activity_map, _successors, predecessors, rel_data = _build_graph(activities)
    topo_order = _topological_sort(activity_map, predecessors)

    # ----- Forward pass (ES, EF) -----------------------------------------
    es: dict[str, int] = {}
    ef: dict[str, int] = {}

    for aid in topo_order:
        duration = _get_duration(activity_map, aid)
        rels = rel_data.get(aid, [])

        if rels:
            # Start with the earliest possible (0) and let each relationship
            # push ES/EF later.
            candidate_es: int = 0
            candidate_ef: int = duration  # = candidate_es + duration

            for rel in rels:
                pred_id = rel["pred_id"]
                rel_type = rel["type"]
                lag = rel["lag"]

                pred_es = es.get(pred_id, 0)
                pred_ef = ef.get(pred_id, 0)

                if rel_type == "FS":
                    # ES[j] >= EF[i] + lag
                    new_es = pred_ef + lag
                    if new_es > candidate_es:
                        candidate_es = new_es
                        candidate_ef = candidate_es + duration

                elif rel_type == "SS":
                    # ES[j] >= ES[i] + lag
                    new_es = pred_es + lag
                    if new_es > candidate_es:
                        candidate_es = new_es
                        candidate_ef = candidate_es + duration

                elif rel_type == "FF":
                    # EF[j] >= EF[i] + lag  =>  ES[j] = EF[j] - dur
                    new_ef = pred_ef + lag
                    # EF must be at least new_ef; ES must accommodate
                    required_es = new_ef - duration
                    if required_es > candidate_es:
                        candidate_es = required_es
                        candidate_ef = candidate_es + duration

                elif rel_type == "SF":
                    # EF[j] >= ES[i] + lag  =>  ES[j] = EF[j] - dur
                    new_ef = pred_es + lag
                    required_es = new_ef - duration
                    if required_es > candidate_es:
                        candidate_es = required_es
                        candidate_ef = candidate_es + duration

            es[aid] = candidate_es
            ef[aid] = candidate_ef
        else:
            es[aid] = 0
            ef[aid] = duration

    project_duration = max(ef.values()) if ef else 0

    # ----- Backward pass (LF, LS) ----------------------------------------
    lf: dict[str, int] = {}
    ls: dict[str, int] = {}

    # Pre-compute successor relationships: for each activity, gather the
    # relationships from its successors that reference it.
    succ_rels: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for aid, rels in rel_data.items():
        for rel in rels:
            # aid is the successor, rel["pred_id"] is the predecessor
            succ_rels[rel["pred_id"]].append((aid, rel["type"], rel["lag"]))

    for aid in reversed(topo_order):
        duration = _get_duration(activity_map, aid)
        srels = succ_rels.get(aid, [])

        if srels:
            candidate_lf: int = project_duration
            candidate_ls: int = project_duration - duration

            for succ_id, rel_type, lag in srels:
                succ_ls = ls.get(succ_id, project_duration)
                succ_lf = lf.get(succ_id, project_duration)

                if rel_type == "FS":
                    # LF[i] <= LS[j] - lag
                    new_lf = succ_ls - lag
                    if new_lf < candidate_lf:
                        candidate_lf = new_lf
                        candidate_ls = candidate_lf - duration

                elif rel_type == "SS":
                    # LS[i] <= LS[j] - lag
                    new_ls = succ_ls - lag
                    # LS must be at most new_ls; LF = LS + dur
                    required_lf = new_ls + duration
                    if required_lf < candidate_lf:
                        candidate_lf = required_lf
                        candidate_ls = candidate_lf - duration

                elif rel_type == "FF":
                    # LF[i] <= LF[j] - lag
                    new_lf = succ_lf - lag
                    if new_lf < candidate_lf:
                        candidate_lf = new_lf
                        candidate_ls = candidate_lf - duration

                elif rel_type == "SF":
                    # LS[i] <= LF[j] - lag
                    new_ls = succ_lf - lag
                    required_lf = new_ls + duration
                    if required_lf < candidate_lf:
                        candidate_lf = required_lf
                        candidate_ls = candidate_lf - duration

            lf[aid] = candidate_lf
            ls[aid] = candidate_ls
        else:
            lf[aid] = project_duration
            ls[aid] = project_duration - duration

    # ----- Float and critical path ----------------------------------------
    critical_path: list[str] = []
    result_activities: list[dict] = []

    for aid in topo_order:
        total_float = ls[aid] - es[aid]
        is_critical = total_float == 0

        enriched = {
            **activity_map[aid],
            "early_start": es[aid],
            "early_finish": ef[aid],
            "late_start": ls[aid],
            "late_finish": lf[aid],
            "total_float": total_float,
            "is_critical": is_critical,
        }
        result_activities.append(enriched)

        if is_critical:
            critical_path.append(aid)

    # ----- Calendar date conversion (optional) ------------------------------
    if project_start is not None:
        cal_map = calendars or {}
        for act in result_activities:
            cal_id = act.get("calendar_id")
            cal = cal_map.get(cal_id, DEFAULT_CALENDAR) if cal_id else DEFAULT_CALENDAR
            act_es = act["early_start"]
            duration = _get_duration(activity_map, act["id"])
            start_d = cal.add_work_days(project_start, act_es)
            finish_d = cal.add_work_days(start_d, duration) if duration > 0 else start_d
            act["start_date"] = start_d.isoformat()
            act["finish_date"] = finish_d.isoformat()

    logger.info(
        "CPM calculated: project_duration=%d, critical_activities=%d/%d",
        project_duration,
        len(critical_path),
        len(activities),
    )

    return {
        "activities": result_activities,
        "critical_path": critical_path,
        "project_duration": project_duration,
        "critical_path_length": len(critical_path),
    }


async def calculate_free_float(activities: list[dict]) -> list[dict]:
    """Calculate free float for each activity.

    Free Float = min(ES of successors) - EF of current activity.
    Activities with no successors get free float of total float.

    Returns updated activities list with ``free_float`` added.
    """
    # First run CPM to get ES/EF/LS/LF values
    cpm_result = await calculate_cpm(activities)
    enriched = cpm_result["activities"]

    # Build successor map from enriched activities
    successors: dict[str, list[str]] = defaultdict(list)
    es_map: dict[str, int] = {}
    ef_map: dict[str, int] = {}

    for act in enriched:
        aid = str(act["id"])
        es_map[aid] = act["early_start"]
        ef_map[aid] = act["early_finish"]
        for pred_id in act.get("predecessors", []):
            successors[str(pred_id)].append(aid)
        for rel in act.get("relationships", []):
            pred_id = str(rel.get("predecessor_id", ""))
            if pred_id:
                successors[pred_id].append(aid)

    result: list[dict] = []
    for act in enriched:
        aid = str(act["id"])
        succs = successors.get(aid, [])
        free_float = min(es_map[s] for s in succs) - ef_map[aid] if succs else act["total_float"]
        result.append({**act, "free_float": free_float})

    return result


async def find_near_critical_paths(
    activities: list[dict], threshold_days: int = 5
) -> list[list[str]]:
    """Find paths with total float less than *threshold_days*.

    Returns list of paths (each path is a list of activity ids) that are
    near-critical, i.e. their total float is between 1 and *threshold_days*
    (inclusive).  The true critical path (float == 0) is excluded.
    """
    cpm_result = await calculate_cpm(activities)
    enriched = cpm_result["activities"]

    # Build successor map
    successors: dict[str, list[str]] = defaultdict(list)
    activity_map: dict[str, dict] = {}
    for act in enriched:
        aid = str(act["id"])
        activity_map[aid] = act
        for pred_id in act.get("predecessors", []):
            successors[str(pred_id)].append(aid)

    # Identify near-critical activities (0 < float <= threshold)
    near_critical_ids = {str(a["id"]) for a in enriched if 0 < a["total_float"] <= threshold_days}

    if not near_critical_ids:
        return []

    # Find start nodes among near-critical activities (those without a
    # near-critical predecessor)
    start_nodes: list[str] = []
    for aid in near_critical_ids:
        preds = [str(p) for p in activity_map[aid].get("predecessors", [])]
        if not any(p in near_critical_ids for p in preds):
            start_nodes.append(aid)

    # DFS to enumerate near-critical paths
    paths: list[list[str]] = []

    def _dfs(node: str, current_path: list[str], depth: int = 0) -> None:
        if depth >= 5000:
            return
        current_path.append(node)
        nc_succs = [s for s in successors.get(node, []) if s in near_critical_ids]
        if not nc_succs:
            paths.append(list(current_path))
        else:
            for succ in nc_succs:
                _dfs(succ, current_path, depth + 1)
        current_path.pop()

    for start in start_nodes:
        _dfs(start, [])

    logger.info(
        "Found %d near-critical paths (threshold=%d days)",
        len(paths),
        threshold_days,
    )
    return paths
