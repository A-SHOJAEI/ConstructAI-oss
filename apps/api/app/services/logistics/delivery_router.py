"""OR-Tools VRPTW delivery routing for construction site logistics.

Optimizes delivery vehicle routes with capacity constraints and time
windows using Google OR-Tools' constraint solver.
"""

from __future__ import annotations

import logging
import math
import time

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError:
    pywrapcp = None  # type: ignore[assignment]
    routing_enums_pb2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

_KM_PER_DEGREE = 111.0  # approximate km per degree of lat/lng


def _haversine_approx(loc_a: dict[str, float], loc_b: dict[str, float]) -> float:
    """Approximate distance in km using Euclidean on degree coords * 111."""
    dlat = loc_a["lat"] - loc_b["lat"]
    dlng = loc_a["lng"] - loc_b["lng"]
    # Adjust longitude distance by latitude
    avg_lat = math.radians((loc_a["lat"] + loc_b["lat"]) / 2)
    dlng_adjusted = dlng * math.cos(avg_lat)
    return math.sqrt(dlat * dlat + dlng_adjusted * dlng_adjusted) * _KM_PER_DEGREE


def _build_distance_matrix(
    locations: list[dict[str, float]],
) -> list[list[int]]:
    """Build an integer distance matrix (metres) for all locations."""
    n = len(locations)
    matrix: list[list[int]] = []
    for i in range(n):
        row: list[int] = []
        for j in range(n):
            if i == j:
                row.append(0)
            else:
                dist_km = _haversine_approx(locations[i], locations[j])
                row.append(int(dist_km * 1000))  # metres
        matrix.append(row)
    return matrix


def _time_to_minutes(time_str: str) -> int:
    """Convert 'HH:MM' time string to minutes from midnight."""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def optimize_delivery_routes(
    deliveries: list[dict],
    vehicles: list[dict],
    depot: dict,
    date: str,
) -> dict:
    """Optimize delivery routes using Vehicle Routing Problem with Time Windows.

    Parameters
    ----------
    deliveries:
        List of delivery dicts with ``id``, ``location`` ({lat, lng}),
        ``demand_units``, ``time_window`` ({start, end} as "HH:MM"),
        ``duration_minutes``.
    vehicles:
        List of vehicle dicts with ``id``, ``capacity_units``,
        ``cost_per_km``, ``max_distance_km``.
    depot:
        Depot dict with ``location`` ({lat, lng}), ``open_time``,
        ``close_time`` (as "HH:MM").
    date:
        Delivery date as ISO string (for reference in output).

    Returns
    -------
    dict with routes, total_cost, total_distance, unassigned, computation_time_ms.
    """
    start_time = time.monotonic()

    if pywrapcp is None or routing_enums_pb2 is None:
        raise ImportError(
            "Google OR-Tools is required for delivery routing. Install with: pip install ortools"
        )

    if not deliveries:
        return {
            "routes": [],
            "total_cost": 0.0,
            "total_distance": 0.0,
            "unassigned": [],
            "computation_time_ms": 0,
        }

    num_vehicles = len(vehicles)
    num_deliveries = len(deliveries)

    # Node 0 = depot, nodes 1..N = deliveries
    locations: list[dict[str, float]] = [depot["location"]]
    demands: list[int] = [0]  # depot has zero demand
    time_windows: list[tuple[int, int]] = [
        (
            _time_to_minutes(depot.get("open_time", "06:00")),
            _time_to_minutes(depot.get("close_time", "20:00")),
        ),
    ]
    service_times: list[int] = [0]

    delivery_id_map: dict[int, str] = {}
    for idx, delivery in enumerate(deliveries):
        node_idx = idx + 1
        delivery_id_map[node_idx] = str(delivery["id"])
        locations.append(delivery["location"])
        demands.append(int(delivery.get("demand_units", 1)))

        tw = delivery.get("time_window", {})
        tw_start = _time_to_minutes(tw.get("start", "06:00"))
        tw_end = _time_to_minutes(tw.get("end", "20:00"))
        time_windows.append((tw_start, tw_end))
        service_times.append(int(delivery.get("duration_minutes", 15)))

    # Distance matrix (in metres)
    dist_matrix = _build_distance_matrix(locations)

    # Travel time matrix (assume 30 km/h average speed -> 0.5 km/min)
    speed_km_per_min = 0.5

    def travel_time_minutes(from_idx: int, to_idx: int) -> int:
        dist_km = dist_matrix[from_idx][to_idx] / 1000.0
        return math.ceil(dist_km / speed_km_per_min)

    # Create routing index manager
    num_nodes = 1 + num_deliveries
    manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # Distance callback
    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return dist_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Capacity constraint
    def demand_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    vehicle_capacities = [int(v.get("capacity_units", 100)) for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # null capacity slack
        vehicle_capacities,
        True,  # start cumul to zero
        "Capacity",
    )

    # Time window constraint
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        travel = travel_time_minutes(from_node, to_node)
        service = service_times[from_node]
        return travel + service

    time_callback_index = routing.RegisterTransitCallback(time_callback)
    max_time = _time_to_minutes("23:59")
    routing.AddDimension(
        time_callback_index,
        60,  # allow waiting up to 60 minutes
        max_time,
        False,  # don't force start cumul to zero
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Set time windows for each node
    for node_idx in range(num_nodes):
        index = manager.NodeToIndex(node_idx)
        tw_start, tw_end = time_windows[node_idx]
        time_dimension.CumulVar(index).SetRange(tw_start, tw_end)

    # Set vehicle start/end time windows at depot
    for v in range(num_vehicles):
        start_index = routing.Start(v)
        end_index = routing.End(v)
        depot_tw = time_windows[0]
        time_dimension.CumulVar(start_index).SetRange(depot_tw[0], depot_tw[1])
        time_dimension.CumulVar(end_index).SetRange(depot_tw[0], depot_tw[1])

    # Maximum distance per vehicle
    max_distances = [int(v.get("max_distance_km", 500) * 1000) for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(
        transit_callback_index,
        0,
        max_distances,
        True,
        "Distance",
    )

    # Allow dropping nodes with a penalty (handle infeasible assignments)
    penalty = 100_000
    for node_idx in range(1, num_nodes):
        routing.AddDisjunction([manager.NodeToIndex(node_idx)], penalty)

    # Search parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(30)

    # Solve
    solution = routing.SolveWithParameters(search_params)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    if solution is None:
        logger.warning("OR-Tools could not find a feasible solution.")
        return {
            "routes": [],
            "total_cost": 0.0,
            "total_distance": 0.0,
            "unassigned": [str(d["id"]) for d in deliveries],
            "computation_time_ms": elapsed_ms,
        }

    # Extract routes
    routes: list[dict] = []
    total_cost = 0.0
    total_distance_m = 0
    assigned_nodes: set[int] = set()

    for v_idx in range(num_vehicles):
        vehicle = vehicles[v_idx]
        cost_per_km = float(vehicle.get("cost_per_km", 1.0))
        route_stops: list[dict] = []
        route_node_indices: list[int] = []
        route_distance_m = 0

        index = routing.Start(v_idx)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            next_index = solution.Value(routing.NextVar(index))
            next_node = manager.IndexToNode(next_index)

            route_distance_m += dist_matrix[node][next_node]

            if node != 0:  # skip depot
                assigned_nodes.add(node)
                route_node_indices.append(node)
                arrival = solution.Min(time_dimension.CumulVar(index))
                departure = arrival + service_times[node]
                route_stops.append(
                    {
                        "delivery_id": delivery_id_map[node],
                        "arrival_time": f"{arrival // 60:02d}:{arrival % 60:02d}",
                        "departure_time": f"{departure // 60:02d}:{departure % 60:02d}",
                    }
                )

            index = next_index

        if route_stops:
            route_distance_km = round(route_distance_m / 1000.0, 2)
            route_duration = sum(service_times[n] for n in route_node_indices) + int(
                route_distance_m / 1000.0 / speed_km_per_min
            )
            route_cost = round(route_distance_km * cost_per_km, 2)

            routes.append(
                {
                    "vehicle_id": str(vehicle["id"]),
                    "stops": route_stops,
                    "total_distance_km": route_distance_km,
                    "total_duration_minutes": route_duration,
                    "total_cost": route_cost,
                }
            )

            total_distance_m += route_distance_m
            total_cost += route_cost

    # Unassigned deliveries
    unassigned: list[str] = []
    for node_idx in range(1, num_nodes):
        if node_idx not in assigned_nodes:
            unassigned.append(delivery_id_map[node_idx])

    logger.info(
        "Delivery routing complete: %d routes, %.1f km total, %d unassigned, %d ms",
        len(routes),
        total_distance_m / 1000.0,
        len(unassigned),
        elapsed_ms,
    )

    return {
        "routes": routes,
        "total_cost": round(total_cost, 2),
        "total_distance": round(total_distance_m / 1000.0, 2),
        "unassigned": unassigned,
        "computation_time_ms": elapsed_ms,
    }
