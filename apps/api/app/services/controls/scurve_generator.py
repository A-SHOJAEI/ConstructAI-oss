"""S-Curve data generation for project performance."""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# Guard scipy import; fall back to linear extrapolation if unavailable
try:
    from scipy.optimize import curve_fit as _curve_fit

    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _curve_fit = None  # type: ignore[assignment]
    _HAS_SCIPY = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logistic S-curve fitting
# ---------------------------------------------------------------------------


def _logistic(t: float, L: float, k: float, t0: float) -> float:
    """Standard logistic function: L / (1 + exp(-k * (t - t0)))."""
    return L / (1.0 + math.exp(-k * (t - t0)))


def _logistic_array(t_arr, L: float, k: float, t0: float):
    """Vectorised logistic for curve_fit (accepts numpy arrays)."""
    import numpy as np

    return L / (1.0 + np.exp(-k * (t_arr - t0)))


def _fit_logistic(
    time_points: list[float],
    cumulative_values: list[float],
) -> tuple[float, float, float] | None:
    """Fit a logistic function to cumulative data.

    Returns (L, k, t0) parameters or None if fitting fails.
    """
    if not _HAS_SCIPY or len(time_points) < 3:
        return None

    import numpy as np

    t_arr = np.array(time_points, dtype=float)
    y_arr = np.array(cumulative_values, dtype=float)

    if y_arr.max() <= 0:
        return None

    # Initial guesses
    L0 = float(y_arr.max()) * 1.2
    t0_guess = float(t_arr[len(t_arr) // 2])
    k0 = 0.05

    try:
        popt, _ = _curve_fit(  # pyright: ignore[reportOptionalCall]
            _logistic_array,
            t_arr,
            y_arr,
            p0=[L0, k0, t0_guess],
            maxfev=5000,
        )
        return float(popt[0]), float(popt[1]), float(popt[2])
    except Exception:
        return None


def _compute_forecast_bands(
    time_points: list[float],
    cumulative_values: list[float],
    params: tuple[float, float, float],
    forecast_times: list[float],
) -> dict[str, list[float]]:
    """Compute P10/P50/P90 forecast bands from logistic fit residuals.

    Uses the standard deviation of residuals to estimate uncertainty,
    growing wider into the future.
    """
    import numpy as np

    L, k, t0 = params
    t_arr = np.array(time_points, dtype=float)
    y_arr = np.array(cumulative_values, dtype=float)

    # Compute residuals
    predicted = np.array([_logistic(t, L, k, t0) for t in t_arr])
    residuals = y_arr - predicted
    residual_std = float(np.std(residuals)) if len(residuals) > 1 else 0.0

    last_data_time = time_points[-1] if time_points else 0.0

    p10_values = []
    p50_values = []
    p90_values = []

    for ft in forecast_times:
        base = _logistic(ft, L, k, t0)
        # Uncertainty grows with distance from last data point
        time_distance = max(0.0, ft - last_data_time)
        uncertainty_growth = 1.0 + 0.01 * time_distance
        margin = 1.28 * residual_std * uncertainty_growth  # 1.28 ~ z for P10/P90

        p10_values.append(max(0.0, base - margin))
        p50_values.append(max(0.0, base))
        p90_values.append(base + margin)

    return {
        "p10": p10_values,
        "p50": p50_values,
        "p90": p90_values,
    }


async def generate_scurve_data(
    snapshots: list[dict],
    bac: Decimal,
    start_date: date,
    end_date: date | None = None,
) -> dict:
    """Generate S-Curve data points from EVM snapshots.

    Parameters
    ----------
    snapshots:
        List of EVM snapshot dicts with
        snapshot_date, pv, ev, ac
    bac: Budget at Completion
    start_date: Project start date
    end_date: Optional forecast end date

    Returns
    -------
    Dict with data_points list, forecast info, logistic fit parameters,
    and forecast_bands (P10/P50/P90 projections).
    """
    if not snapshots:
        return {
            "data_points": [],
            "bac": float(bac),
            "forecast_completion": None,
            "forecast_bands": None,
        }

    # Sort snapshots by date
    sorted_snapshots = sorted(
        snapshots,
        key=lambda s: s.get("snapshot_date", ""),
    )

    data_points: list[dict[str, Any]] = []
    for snap in sorted_snapshots:
        snap_date_raw = snap.get("snapshot_date")
        if isinstance(snap_date_raw, str):
            snap_date = date.fromisoformat(snap_date_raw)
        elif isinstance(snap_date_raw, date):
            snap_date = snap_date_raw
        else:
            continue  # skip malformed entries

        data_points.append(
            {
                "date": snap_date,
                "planned_value": float(Decimal(str(snap.get("pv", 0)))),
                "earned_value": float(Decimal(str(snap.get("ev", 0)))),
                "actual_cost": float(Decimal(str(snap.get("ac", 0)))),
            }
        )

    # Prepare time-series data for logistic fitting
    time_points: list[float] = []
    ev_values: list[float] = []
    for dp in data_points:
        dp_date: date = dp["date"]
        days_from_start = (dp_date - start_date).days
        time_points.append(float(days_from_start))
        ev_values.append(float(dp["earned_value"]))

    # Attempt logistic fit
    logistic_params = _fit_logistic(time_points, ev_values)

    # Forecast completion date
    last_snap = sorted_snapshots[-1]
    ev_last = Decimal(str(last_snap.get("ev", 0)))
    spi_last = Decimal(str(last_snap.get("spi", 1)))
    last_date_raw = last_snap.get("snapshot_date")
    if isinstance(last_date_raw, str):
        last_date = date.fromisoformat(last_date_raw)
    elif isinstance(last_date_raw, date):
        last_date = last_date_raw
    else:
        last_date = start_date

    forecast_completion = None

    if logistic_params is not None:
        # Use logistic model for forecast completion
        L, k, t0 = logistic_params
        bac_float = float(bac)
        target = bac_float
        if target > 0 and k > 0:
            try:
                if bac_float <= L:
                    # Logistic asymptote is at or above BAC -- use inverse logistic
                    # Inverse logistic: t = t0 - ln(L/target - 1) / k
                    ratio = L / target - 1.0
                    if ratio > 0:
                        t_completion = t0 - math.log(ratio) / k
                        forecast_completion = start_date + timedelta(days=int(t_completion))
                else:
                    # Asymptote L < BAC: logistic will never reach BAC.
                    # Use linear extrapolation beyond the last data point.
                    if time_points and len(time_points) >= 2:
                        last_t = time_points[-1]
                        last_ev = _logistic(last_t, L, k, t0)
                        # Estimate local slope from last two fitted points
                        prev_t = time_points[-2]
                        prev_ev = _logistic(prev_t, L, k, t0)
                        slope = (last_ev - prev_ev) / (last_t - prev_t) if last_t != prev_t else 0.0
                        if slope > 0:
                            days_extra = (bac_float - last_ev) / slope
                            t_completion = last_t + days_extra
                            forecast_completion = start_date + timedelta(days=int(t_completion))
            except (ValueError, ZeroDivisionError, OverflowError):
                pass

    if forecast_completion is None:
        # Fallback to linear extrapolation (original logic)
        if spi_last > 0 and ev_last > 0 and bac > ev_last:
            remaining = bac - ev_last
            elapsed = max(1, (last_date - start_date).days)
            daily_rate = ev_last / elapsed
            if daily_rate > 0:
                adjusted_rate = daily_rate * spi_last
                days_remaining = int(remaining / adjusted_rate)
                forecast_completion = last_date + timedelta(days=days_remaining)

    # Compute forecast bands
    forecast_bands = None
    if logistic_params is not None and time_points:
        # Generate forecast time points into the future
        last_day = time_points[-1]
        # Forecast out to end_date or 50% beyond current elapsed
        forecast_end_day = float((end_date - start_date).days) if end_date else last_day * 1.5

        step = max(1.0, (forecast_end_day - time_points[0]) / 50.0)
        forecast_times = []
        t = time_points[0]
        while t <= forecast_end_day:
            forecast_times.append(t)
            t += step

        bands = _compute_forecast_bands(time_points, ev_values, logistic_params, forecast_times)

        # Convert forecast times to dates
        forecast_dates = [
            (start_date + timedelta(days=int(ft))).isoformat() for ft in forecast_times
        ]

        forecast_bands = {
            "dates": forecast_dates,
            "p10": [round(v, 2) for v in bands["p10"]],
            "p50": [round(v, 2) for v in bands["p50"]],
            "p90": [round(v, 2) for v in bands["p90"]],
        }

    logger.info(
        "S-Curve generated: %d data points, forecast completion: %s, logistic fit: %s",
        len(data_points),
        forecast_completion,
        "yes" if logistic_params else "no (using linear extrapolation)",
    )

    result = {
        "data_points": data_points,
        "bac": float(bac),
        "forecast_completion": forecast_completion,
        "forecast_bands": forecast_bands,
    }

    if logistic_params:
        L, k, t0 = logistic_params
        result["logistic_fit"] = {
            "L": round(L, 4),
            "k": round(k, 6),
            "t0": round(t0, 2),
        }

    return result
