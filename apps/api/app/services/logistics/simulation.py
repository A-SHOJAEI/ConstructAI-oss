"""SimPy discrete-event simulation for construction site operations.

Models resource contention, task arrival and processing on a construction
site to identify bottlenecks and generate optimization recommendations.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from typing import Any

try:
    import simpy
except ImportError:
    simpy = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simulation internals
# ---------------------------------------------------------------------------


class _SiteMetrics:
    """Mutable container for simulation metrics collection."""

    def __init__(self) -> None:
        self.timeline: list[dict] = []
        self.wait_times: dict[str, list[float]] = defaultdict(list)
        self.resource_busy: dict[str, float] = defaultdict(float)
        self.resource_idle: dict[str, float] = defaultdict(float)
        self.tasks_completed: int = 0
        self.tasks_started: int = 0


def _task_process(
    env: Any,  # simpy.Environment
    task: dict,
    resources: dict[str, Any],  # simpy.Resource
    metrics: _SiteMetrics,
) -> Any:
    """SimPy process for a single construction task."""
    task_name = task["name"]
    duration_hours = float(task.get("duration_hours", 1))
    resources_needed: dict[str, int] = task.get("resources_needed", {})
    _arrive_time = env.now

    metrics.timeline.append(
        {
            "time": round(env.now, 2),
            "event": "task_arrived",
            "resource": None,
            "task": task_name,
        }
    )

    # Request all needed resources
    requests = []
    for res_name, qty in resources_needed.items():
        res = resources.get(res_name)
        if res is None:
            continue
        for _ in range(qty):
            req = res.request()
            requests.append((res_name, res, req))

    # Wait for all resource requests to be fulfilled
    for res_name, _res, req in requests:
        wait_start = env.now
        yield req
        wait_end = env.now
        wait_duration = wait_end - wait_start
        metrics.wait_times[res_name].append(wait_duration)

        metrics.timeline.append(
            {
                "time": round(env.now, 2),
                "event": "resource_acquired",
                "resource": res_name,
                "task": task_name,
            }
        )

    # Process the task
    metrics.tasks_started += 1
    _process_start = env.now

    metrics.timeline.append(
        {
            "time": round(env.now, 2),
            "event": "task_started",
            "resource": None,
            "task": task_name,
        }
    )

    yield env.timeout(duration_hours)

    _process_end = env.now

    # Track busy time per resource
    for res_name, qty in resources_needed.items():
        metrics.resource_busy[res_name] += duration_hours * qty

    # Release resources
    for res_name, res, req in requests:
        res.release(req)
        metrics.timeline.append(
            {
                "time": round(env.now, 2),
                "event": "resource_released",
                "resource": res_name,
                "task": task_name,
            }
        )

    metrics.tasks_completed += 1
    metrics.timeline.append(
        {
            "time": round(env.now, 2),
            "event": "task_completed",
            "resource": None,
            "task": task_name,
        }
    )


def _task_generator(
    env: Any,  # simpy.Environment
    tasks: list[dict],
    arrival_rate: float,
    resources: dict[str, Any],
    metrics: _SiteMetrics,
    duration_hours: float,
    rng: random.Random | None = None,
) -> Any:
    """Generate tasks according to arrival rate (Poisson process)."""
    _rng = rng or random.Random()
    task_idx = 0
    while env.now < duration_hours:
        # Pick next task from the list (cycle through)
        task = tasks[task_idx % len(tasks)]
        task_idx += 1

        env.process(_task_process(env, task, resources, metrics))

        # Inter-arrival time (exponential distribution)
        if arrival_rate > 0:
            inter_arrival = _rng.expovariate(arrival_rate / 24.0)  # per hour
            yield env.timeout(max(inter_arrival, 0.1))
        else:
            yield env.timeout(24.0)


def _run_simulation(scenario: dict, duration_days: int) -> _SiteMetrics:
    """Execute the SimPy simulation synchronously."""
    if simpy is None:
        raise ImportError("SimPy is required for site simulation. Install with: pip install simpy")

    env = simpy.Environment()
    duration_hours = duration_days * 24.0

    # Create resources
    resource_config = scenario.get("resources", {})
    resources: dict[str, Any] = {}
    for res_name, capacity in resource_config.items():
        resources[res_name] = simpy.Resource(env, capacity=int(capacity))

    metrics = _SiteMetrics()

    tasks = scenario.get("tasks", [])
    arrival_rate = float(scenario.get("arrival_rate", 5.0))

    if not tasks:
        return metrics

    # Use a local Random instance to avoid polluting the global random state
    rng = random.Random(42)

    # Start the task generator process
    env.process(
        _task_generator(env, tasks, arrival_rate, resources, metrics, duration_hours, rng=rng)
    )

    # Run the simulation
    env.run(until=duration_hours)

    # Calculate idle time per resource
    for res_name, capacity in resource_config.items():
        total_available = duration_hours * int(capacity)
        busy = metrics.resource_busy.get(res_name, 0.0)
        metrics.resource_idle[res_name] = max(0.0, total_available - busy)

    return metrics


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_site_simulation(scenario: dict, duration_days: int = 30) -> dict:
    """Run discrete-event simulation of construction site operations.

    Parameters
    ----------
    scenario:
        Configuration dict with:
        - ``resources``: {cranes: int, trucks: int, crews: int}
        - ``tasks``: list of {name, duration_hours, resources_needed: dict,
          priority: int}
        - ``arrival_rate``: float (tasks per day)
    duration_days:
        Simulation duration in days.

    Returns
    -------
    dict with timeline, bottlenecks, utilization, throughput, avg_wait_time,
    recommendations.
    """
    metrics = _run_simulation(scenario, duration_days)
    resource_config = scenario.get("resources", {})
    duration_hours = duration_days * 24.0

    # Build utilization report
    utilization: dict[str, dict[str, float]] = {}
    for res_name, capacity in resource_config.items():
        total_available = duration_hours * int(capacity)
        busy = metrics.resource_busy.get(res_name, 0.0)
        idle = metrics.resource_idle.get(res_name, 0.0)
        util_pct = (busy / total_available * 100.0) if total_available > 0 else 0.0

        utilization[res_name] = {
            "utilization_pct": round(util_pct, 1),
            "idle_pct": round(100.0 - util_pct, 1),
            "busy_hours": round(busy, 1),
            "idle_hours": round(idle, 1),
        }

    # Identify bottlenecks (resources with high utilization or long waits)
    bottlenecks: list[dict] = []
    for res_name in resource_config:
        wait_data = metrics.wait_times.get(res_name, [])
        avg_wait = sum(wait_data) / len(wait_data) if wait_data else 0.0
        util_pct = utilization.get(res_name, {}).get("utilization_pct", 0.0)

        if util_pct > 80 or avg_wait > 2.0:
            recommendation = ""
            if util_pct > 90:
                recommendation = (
                    f"Consider adding more {res_name} capacity. "
                    f"Current utilization at {util_pct:.0f}% indicates a "
                    f"significant bottleneck."
                )
            elif util_pct > 80:
                recommendation = (
                    f"{res_name} utilization is high ({util_pct:.0f}%). "
                    f"Monitor closely and consider adding capacity if demand increases."
                )
            elif avg_wait > 2.0:
                recommendation = (
                    f"Average wait time for {res_name} is {avg_wait:.1f} hours. "
                    f"Consider staggering task schedules to reduce contention."
                )

            bottlenecks.append(
                {
                    "resource": res_name,
                    "utilization_pct": round(util_pct, 1),
                    "wait_time_avg": round(avg_wait, 2),
                    "recommendation": recommendation,
                }
            )

    # Throughput
    throughput = metrics.tasks_completed / duration_days if duration_days > 0 else 0.0

    # Average wait time across all resources
    all_waits: list[float] = []
    for waits in metrics.wait_times.values():
        all_waits.extend(waits)
    avg_wait_time = sum(all_waits) / len(all_waits) if all_waits else 0.0

    # Generate high-level recommendations
    recommendations: list[str] = []

    if bottlenecks:
        recommendations.append(
            f"Identified {len(bottlenecks)} resource bottleneck(s): "
            + ", ".join(b["resource"] for b in bottlenecks)
            + "."
        )

    underutilized = [name for name, stats in utilization.items() if stats["utilization_pct"] < 40]
    if underutilized:
        recommendations.append(
            "Resources with low utilization (<40%): "
            + ", ".join(underutilized)
            + ". Consider reducing capacity or reassigning."
        )

    if avg_wait_time > 4.0:
        recommendations.append(
            f"Overall average wait time is {avg_wait_time:.1f} hours. "
            f"Review task scheduling and resource allocation."
        )

    if throughput < scenario.get("arrival_rate", 0):
        recommendations.append(
            "Throughput is below task arrival rate. The system may not be "
            "able to keep up with demand. Add resources or reduce task scope."
        )

    if not recommendations:
        recommendations.append(
            "Simulation indicates the site is operating within acceptable "
            "parameters. No immediate changes recommended."
        )

    # Limit timeline to keep response manageable
    timeline = metrics.timeline[:500]

    logger.info(
        "Simulation complete: %d days, %d tasks completed, throughput=%.1f/day",
        duration_days,
        metrics.tasks_completed,
        throughput,
    )

    return {
        "timeline": timeline,
        "bottlenecks": bottlenecks,
        "utilization": utilization,
        "throughput": round(throughput, 2),
        "avg_wait_time": round(avg_wait_time, 2),
        "recommendations": recommendations,
    }
