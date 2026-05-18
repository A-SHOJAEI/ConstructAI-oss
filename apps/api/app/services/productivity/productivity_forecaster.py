"""Productivity forecasting using time series analysis.

When historical data is insufficient, falls back to baseline
productivity rates from the curated seed data (200+ activities).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Baseline productivity rates from seed data
# ---------------------------------------------------------------------------

_SEED_FILE = Path(__file__).resolve().parents[3] / "data" / "seed" / "productivity_rates_v1.json"

_baseline_cache: dict[str, list[dict]] | None = None


def _load_baseline_rates() -> dict[str, list[dict]]:
    """Load baseline productivity rates from seed JSON, indexed by trade."""
    global _baseline_cache
    if _baseline_cache is not None:
        return _baseline_cache

    _baseline_cache = {}

    if not _SEED_FILE.exists():
        logger.warning("Productivity rates seed file not found: %s", _SEED_FILE)
        return _baseline_cache

    with open(_SEED_FILE) as f:
        raw = json.load(f)

    entries = raw if isinstance(raw, list) else raw.get("rates", raw.get("activities", []))

    for entry in entries:
        trade = entry.get("trade", "unknown")
        if trade not in _baseline_cache:
            _baseline_cache[trade] = []
        _baseline_cache[trade].append(entry)

    logger.info(
        "Loaded baseline productivity rates: %d activities across %d trades",
        len(entries),
        len(_baseline_cache),
    )
    return _baseline_cache


def get_baseline_rate(trade: str, activity_code: str | None = None) -> dict | None:
    """Look up a baseline productivity rate by trade and optional activity code.

    Returns the matching rate dict, or the first rate for the trade if no
    activity_code is specified.
    """
    rates = _load_baseline_rates()
    trade_rates = rates.get(trade.lower(), [])

    if not trade_rates:
        return None

    if activity_code:
        for r in trade_rates:
            if r.get("activity_code") == activity_code:
                return r

    return trade_rates[0]


def get_trade_summary(trade: str) -> dict:
    """Get summary statistics for a trade's baseline rates."""
    rates = _load_baseline_rates()
    trade_rates = rates.get(trade.lower(), [])

    if not trade_rates:
        return {"trade": trade, "activity_count": 0, "avg_manhours_per_unit": 0}

    mh_values = [r.get("manhours_per_unit", 0) for r in trade_rates]
    return {
        "trade": trade,
        "activity_count": len(trade_rates),
        "avg_manhours_per_unit": round(sum(mh_values) / len(mh_values), 4) if mh_values else 0,
        "activities": [
            {"code": r["activity_code"], "name": r["activity_name"], "unit": r["unit"]}
            for r in trade_rates
        ],
    }


def clear_baseline_cache() -> None:
    """Clear the baseline rates cache (for testing)."""
    global _baseline_cache
    _baseline_cache = None


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------


async def forecast_productivity(
    historical_data: list[dict],
    trade: str,
    forecast_days: int = 14,
    *,
    db: AsyncSession | None = None,
) -> dict:
    """Forecast crew productivity based on historical data.

    Parameters
    ----------
    historical_data: List of dicts with work_date,
        actual_units, planned_units, crew_size
    trade: Trade/craft name
    forecast_days: Number of days to forecast
    db: Optional async DB session for loading rates from database

    When historical data is insufficient (<3 records), returns
    baseline rates from the curated seed data instead of an empty
    forecast.

    Returns dict with forecast dates, predicted rates,
    confidence intervals, trend, and optional baseline_rate.
    """
    if not historical_data or len(historical_data) < 3:
        # Try baseline rates as fallback
        baseline = get_baseline_rate(trade)
        if baseline:
            return _baseline_forecast(trade, forecast_days, baseline)
        return _empty_forecast(trade, forecast_days)

    # Extract productivity rates
    rates = []
    for entry in historical_data:
        actual = float(entry.get("actual_units", 0))
        planned = float(entry.get("planned_units", 1))
        if planned > 0:
            rates.append(actual / planned)

    if not rates:
        return _empty_forecast(trade, forecast_days)

    rates_arr = np.array(rates)
    mean_rate = float(np.mean(rates_arr))
    std_rate = float(np.std(rates_arr))

    # Simple linear trend
    x = np.arange(len(rates_arr))
    if len(rates_arr) >= 2:
        coeffs = np.polyfit(x, rates_arr, 1)
        slope = float(coeffs[0])
    else:
        slope = 0.0

    # Determine trend
    if slope > 0.01:
        trend = "improving"
    elif slope < -0.01:
        trend = "declining"
    else:
        trend = "stable"

    # Generate forecast
    last_date = historical_data[-1].get("work_date")
    if isinstance(last_date, str):
        last_date = date.fromisoformat(last_date)
    if last_date is None:
        last_date = date.today()

    forecast_dates = []
    predicted_rates = []
    confidence_intervals = []

    for i in range(1, forecast_days + 1):
        forecast_date = last_date + timedelta(days=i)
        forecast_dates.append(forecast_date)

        predicted = mean_rate + slope * (len(rates) + i)
        predicted = max(0.0, predicted)
        predicted_rates.append(round(predicted, 4))

        # Widen CI over time
        ci_width = std_rate * (1 + i * 0.05)
        confidence_intervals.append(
            {
                "lower": round(max(0, predicted - ci_width), 4),
                "upper": round(predicted + ci_width, 4),
            }
        )

    logger.info(
        "Productivity forecast for %s: trend=%s, mean_rate=%.3f",
        trade,
        trend,
        mean_rate,
    )

    return {
        "project_id": historical_data[0].get("project_id", ""),
        "trade": trade,
        "forecast_dates": forecast_dates,
        "predicted_rates": predicted_rates,
        "confidence_intervals": confidence_intervals,
        "trend": trend,
    }


def _baseline_forecast(
    trade: str,
    forecast_days: int,
    baseline: dict,
) -> dict:
    """Return a forecast using baseline productivity rates when no history exists."""
    today = date.today()
    forecast_dates = [today + timedelta(days=i) for i in range(1, forecast_days + 1)]

    # Use baseline daily_output / (crew_size * 8) as predicted rate
    daily_output = float(baseline.get("daily_output", 0))
    manhours = float(baseline.get("manhours_per_unit", 0))

    logger.info(
        "Using baseline rate for %s: %s (%.2f %s/day, %.4f mh/unit)",
        trade,
        baseline.get("activity_name", "unknown"),
        daily_output,
        baseline.get("unit", ""),
        manhours,
    )

    return {
        "project_id": "",
        "trade": trade,
        "forecast_dates": forecast_dates,
        "predicted_rates": [1.0] * forecast_days,
        "confidence_intervals": [{"lower": 0.7, "upper": 1.3}] * forecast_days,
        "trend": "baseline",
        "baseline_rate": {
            "activity_code": baseline.get("activity_code"),
            "activity_name": baseline.get("activity_name"),
            "daily_output": daily_output,
            "unit": baseline.get("unit"),
            "manhours_per_unit": manhours,
            "crew_size": float(baseline.get("crew_size", 0)),
            "crew_composition": baseline.get("crew_composition", {}),
        },
    }


def _empty_forecast(
    trade: str,
    forecast_days: int,
) -> dict:
    """Return empty forecast when insufficient data and no baseline."""
    return {
        "project_id": "",
        "trade": trade,
        "forecast_dates": [],
        "predicted_rates": [],
        "confidence_intervals": [],
        "trend": "insufficient_data",
    }
