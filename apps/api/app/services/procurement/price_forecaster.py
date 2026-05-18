"""ARIMA + Prophet ensemble price forecasting for construction materials.

Fetches real Producer Price Index data from the FRED API (Federal Reserve
Economic Data) and generates 3/6/12-month forward forecasts.  Synthetic
fallback data has been **removed** -- if FRED data is unavailable the
functions raise or return an explicit error so callers can handle it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency imports with graceful degradation
# ---------------------------------------------------------------------------

try:
    import numpy as np
    from statsmodels.tsa.arima.model import ARIMA as ARIMA_MODEL

    _HAS_ARIMA = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    ARIMA_MODEL = None  # type: ignore[assignment,misc]
    _HAS_ARIMA = False
    logger.warning("statsmodels/numpy not installed; ARIMA forecasting unavailable")

try:
    from prophet import Prophet

    _HAS_PROPHET = True
except ImportError:  # pragma: no cover
    Prophet = None  # type: ignore[assignment,misc]
    _HAS_PROPHET = False
    logger.warning("prophet not installed; Prophet forecasting unavailable")

try:
    import pmdarima as _pmdarima

    _HAS_PMDARIMA = True
except ImportError:  # pragma: no cover
    _pmdarima = None  # type: ignore[assignment]
    _HAS_PMDARIMA = False
    logger.info("pmdarima not installed; falling back to fixed ARIMA order (1,1,1)")

# ---------------------------------------------------------------------------
# FRED series catalog with CSI MasterFormat code mapping
# ---------------------------------------------------------------------------

FRED_SERIES_MAP: dict[str, dict] = {
    # ── Material PPIs ──────────────────────────────────────────────────
    "WPU1017": {
        "description": "Steel Mill Products",
        "csi_division": "05",
        "category": "steel_mill",
    },
    "WPU08": {
        "description": "Lumber & Wood Products",
        "csi_division": "06",
        "category": "lumber",
    },
    "WPU133": {
        "description": "Concrete Products",
        "csi_division": "03",
        "category": "concrete",
    },
    "WPU102502": {
        "description": "Copper & Brass Mill Shapes",
        "csi_division": "26",
        "category": "copper",
    },
    "WPU0553": {
        "description": "Asphalt & Other Petroleum/Coal Products",
        "csi_division": "32",
        "category": "asphalt",
    },
    "WPUSI012011": {
        "description": "Construction Materials Composite",
        "csi_division": None,
        "category": "construction_composite",
    },
    "WPU0241": {
        "description": "Flat Glass",
        "csi_division": "08",
        "category": "glass",
    },
    "WPU07210603": {
        "description": "Plastic Pipe",
        "csi_division": "22",
        "category": "plastic_pipe",
    },
    "WPU0542": {
        "description": "Gypsum Products",
        "csi_division": "09",
        "category": "gypsum",
    },
    "WPU0531": {
        "description": "Insulation Materials",
        "csi_division": "07",
        "category": "insulation",
    },
    "WPU0812": {
        "description": "Plywood",
        "csi_division": "06",
        "category": "plywood",
    },
    "WPU102301": {
        "description": "Structural Steel Shapes",
        "csi_division": "05",
        "category": "structural_steel",
    },
    "WPU101706": {
        "description": "Steel Pipe & Tube",
        "csi_division": "22",
        "category": "steel_pipe",
    },
    # ── Macro / leading indicators ─────────────────────────────────────
    "CPIAUCSL": {
        "description": "CPI-Urban (All Items, Seasonally Adjusted)",
        "csi_division": None,
        "category": "cpi_urban",
    },
    "USCONS": {
        "description": "Total Construction Spending",
        "csi_division": None,
        "category": "construction_spending",
    },
    "PERMIT": {
        "description": "New Private Housing Units Authorized (Building Permits)",
        "csi_division": None,
        "category": "building_permits",
    },
}

# Reverse lookup: category name -> series_id
_CATEGORY_TO_SERIES: dict[str, str] = {v["category"]: k for k, v in FRED_SERIES_MAP.items()}

# Reverse lookup: CSI division -> list of series_ids
_CSI_TO_SERIES: dict[str, list[str]] = {}
for _sid, _meta in FRED_SERIES_MAP.items():
    _div = _meta.get("csi_division")
    if _div:
        _CSI_TO_SERIES.setdefault(_div, []).append(_sid)

# ---------------------------------------------------------------------------
# Material-specific seasonal adjustment factors
# ---------------------------------------------------------------------------

_SEASONAL_FACTORS: dict[str, dict[int, float]] = {
    "concrete": {3: 1.02, 4: 1.05, 5: 1.08, 6: 1.10, 7: 1.10, 8: 1.08, 9: 1.05, 10: 1.02},
    "steel": {1: 1.03, 2: 1.05, 3: 1.04, 4: 1.02},
    "lumber": {3: 1.05, 4: 1.08, 5: 1.10, 6: 1.08, 7: 1.05},
    "asphalt": {4: 1.05, 5: 1.10, 6: 1.12, 7: 1.12, 8: 1.10, 9: 1.05},
}

# ---------------------------------------------------------------------------
# FRED API rate limiter (120 requests/minute)
# ---------------------------------------------------------------------------

_FRED_RATE_LIMIT = 120
_FRED_RATE_WINDOW = 60.0  # seconds
_fred_request_times: list[float] = []
_fred_rate_lock = asyncio.Lock()


async def _fred_rate_limit_wait() -> None:
    """Block until a FRED API request slot is available.

    Enforces the 120-requests-per-minute rate limit by maintaining a
    sliding window of request timestamps.
    """
    sleep_for = 0.0

    # Phase 1: Calculate wait time under the lock, then release
    async with _fred_rate_lock:
        now = time.monotonic()
        # Prune timestamps outside the window
        cutoff = now - _FRED_RATE_WINDOW
        while _fred_request_times and _fred_request_times[0] < cutoff:
            _fred_request_times.pop(0)

        if len(_fred_request_times) >= _FRED_RATE_LIMIT:
            # Calculate how long to wait until the oldest request falls outside the window
            sleep_for = _fred_request_times[0] - cutoff

    # Phase 2: Sleep outside the lock
    if sleep_for > 0:
        logger.debug("FRED rate limit: sleeping %.2fs", sleep_for)
        await asyncio.sleep(sleep_for)

    # Phase 3: Reacquire lock to prune again and record the timestamp
    async with _fred_rate_lock:
        now = time.monotonic()
        cutoff = now - _FRED_RATE_WINDOW
        while _fred_request_times and _fred_request_times[0] < cutoff:
            _fred_request_times.pop(0)

        _fred_request_times.append(time.monotonic())


# ---------------------------------------------------------------------------
# Reusable HTTP client for connection pooling (PI-08)
# ---------------------------------------------------------------------------

# Module-level httpx.AsyncClient for connection reuse across requests.
# This avoids creating a new TCP connection + TLS handshake per API call.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient, creating it on first use."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=120,
            ),
        )
    return _http_client


# ---------------------------------------------------------------------------
# Caching infrastructure
# ---------------------------------------------------------------------------

# FRED cache: series_id -> (result_list, timestamp)
_fred_cache: dict[str, tuple[list[dict], float]] = {}
_FRED_CACHE_TTL = 7 * 86400  # 7 days in seconds

# BLS cache: series_id -> (result_list, timestamp)
_bls_cache: dict[str, tuple[list[dict], float]] = {}
_BLS_CACHE_TTL = 7 * 86400  # 7 days in seconds

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_fred_api_key() -> str | None:
    """Read the FRED API key from settings or environment."""
    try:
        from app.config import settings

        key = getattr(settings, "FRED_API_KEY", None)
        if key:
            return key
    except Exception:
        logger.debug("Could not load FRED_API_KEY from settings, trying env var")
    return os.environ.get("FRED_API_KEY")


def _apply_seasonal_adjustments(
    forecasts: list[dict],
    material_category: str,
) -> list[dict]:
    """Apply material-specific seasonal factors to forecast values."""
    cat_lower = material_category.lower()
    factors: dict[int, float] | None = None

    if cat_lower in _SEASONAL_FACTORS:
        factors = _SEASONAL_FACTORS[cat_lower]
    else:
        for key, val in _SEASONAL_FACTORS.items():
            if key in cat_lower:
                factors = val
                break

    if factors is None:
        return forecasts

    adjusted: list[dict] = []
    for fc in forecasts:
        try:
            month = datetime.strptime(fc["date"], "%Y-%m-%d").month
        except (ValueError, KeyError):
            adjusted.append(fc)
            continue

        factor = factors.get(month, 1.0)
        adjusted.append(
            {
                "date": fc["date"],
                "forecast_value": round(fc["forecast_value"] * factor, 2),
                "lower_bound": round(fc["lower_bound"] * factor, 2),
                "upper_bound": round(fc["upper_bound"] * factor, 2),
            }
        )

    return adjusted


def _select_arima_order(
    series: np.ndarray,  # type: ignore[name-defined]
) -> tuple[int, int, int]:
    """Choose the best ARIMA (p,d,q) order for *series*."""
    if _HAS_PMDARIMA:
        try:
            auto_result = _pmdarima.auto_arima(  # pyright: ignore[reportOptionalMemberAccess]
                series,
                seasonal=True,
                m=12,
                stepwise=True,
                suppress_warnings=True,
            )
            order = auto_result.order
            logger.info("auto_arima selected order %s", order)
            return order  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("auto_arima failed, falling back to (1,1,1): %s", exc)

    return (1, 1, 1)


def _linear_trend_forecast(
    historical_data: list[dict], horizon_months: int
) -> tuple[list[dict], float]:
    """Simple linear trend extrapolation as a fallback forecaster."""
    n = len(historical_data)
    if n < 2:
        last_val = historical_data[0]["price_index"] if historical_data else 0.0
        base_date = (
            datetime.strptime(historical_data[0]["date"], "%Y-%m-%d")
            if historical_data
            else datetime.now(UTC)
        )
        fallback_forecasts: list[dict] = []
        for m in range(1, horizon_months + 1):
            fdate = base_date + timedelta(days=30 * m)
            fallback_forecasts.append(
                {
                    "date": fdate.strftime("%Y-%m-%d"),
                    "forecast_value": round(last_val, 2),
                    "lower_bound": round(last_val * 0.95, 2),
                    "upper_bound": round(last_val * 1.05, 2),
                }
            )
        return fallback_forecasts, 0.0

    x_vals = list(range(n))
    y_vals = [d["price_index"] for d in historical_data]

    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals, strict=False))
    denominator = sum((x - x_mean) ** 2 for x in x_vals)

    slope = numerator / denominator if denominator != 0 else 0.0
    intercept = y_mean - slope * x_mean

    residuals = [y - (slope * x + intercept) for x, y in zip(x_vals, y_vals, strict=False)]
    residual_std = (sum(r**2 for r in residuals) / max(n - 2, 1)) ** 0.5

    last_date = datetime.strptime(historical_data[-1]["date"], "%Y-%m-%d")
    forecasts: list[dict] = []

    for m in range(1, horizon_months + 1):
        x_future = n - 1 + m
        forecast_val = slope * x_future + intercept
        margin = 1.96 * residual_std * (1 + m / n) ** 0.5
        fdate = last_date + timedelta(days=30 * m)
        forecasts.append(
            {
                "date": fdate.strftime("%Y-%m-%d"),
                "forecast_value": round(forecast_val, 2),
                "lower_bound": round(forecast_val - margin, 2),
                "upper_bound": round(forecast_val + margin, 2),
            }
        )

    return forecasts, slope


def _determine_trend(slope: float) -> str:
    """Categorize the forecast trend based on slope magnitude."""
    if slope > 0.5:
        return "rising"
    elif slope < -0.5:
        return "falling"
    return "stable"


# ---------------------------------------------------------------------------
# FRED data fetching
# ---------------------------------------------------------------------------


class FREDDataUnavailableError(Exception):
    """Raised when FRED data cannot be retrieved and no cache exists."""


async def fetch_fred_data(
    series_id: str,
    start_date: str | None = None,
    *,
    full_history: bool = False,
) -> list[dict]:
    """Fetch time series data from FRED API.

    Parameters
    ----------
    series_id:
        FRED series identifier (e.g. ``"WPU1017"``).
    start_date:
        Optional ISO date string to limit results.  When *None* (default),
        fetches the last 36 months unless *full_history* is True.
    full_history:
        When True, fetches all available data (``observation_start=1947-01-01``).

    Returns
    -------
    List of ``{date, value}`` dicts.

    Raises
    ------
    FREDDataUnavailableError
        If the FRED API key is not configured or the API call fails and
        no cached data is available.
    """
    # 1. Check module-level cache
    now = time.time()
    cached = _fred_cache.get(series_id)
    if cached is not None:
        result, ts = cached
        if now - ts < _FRED_CACHE_TTL:
            logger.debug("FRED cache hit for %s (age=%.0fs)", series_id, now - ts)
            if start_date:
                return [r for r in result if r["date"] >= start_date]
            return result

    # 2. Attempt real FRED API call via httpx
    api_key = _get_fred_api_key()
    if not api_key:
        logger.error(
            "FRED_API_KEY not configured. Cannot fetch series %s. "
            "Set FRED_API_KEY in .env or environment.",
            series_id,
        )
        # Return stale cache if available
        if cached is not None:
            logger.warning("Returning stale cached data for FRED series %s", series_id)
            result, _ = cached
            if start_date:
                return [r for r in result if r["date"] >= start_date]
            return result
        raise FREDDataUnavailableError(
            f"FRED_API_KEY not configured and no cached data for series {series_id}"
        )

    try:
        await _fred_rate_limit_wait()

        today = date.today()
        obs_end = today.isoformat()
        if full_history:
            obs_start = "1947-01-01"
        else:
            obs_start = start_date or (today - timedelta(days=36 * 30)).isoformat()

        # SECURITY: Use params= dict to keep the API key out of the URL
        # string that may appear in logs, tracebacks, or error messages.
        base_url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": obs_start,
            "observation_end": obs_end,
        }

        client = _get_http_client()
        resp = await client.get(base_url, params=params)
        resp.raise_for_status()
        data = resp.json()

        observations = data.get("observations", [])
        results: list[dict] = []
        for obs in observations:
            val_str = obs.get("value", "")
            # FRED uses "." for missing values
            if val_str == "." or val_str == "":
                continue
            results.append(
                {
                    "date": obs["date"],
                    "value": float(val_str),
                }
            )

        if results:
            _fred_cache[series_id] = (results, now)
            logger.info(
                "Fetched %d data points from FRED for series %s",
                len(results),
                series_id,
            )
            return results

        logger.warning(
            "FRED API returned 0 usable observations for series %s",
            series_id,
        )
        # Return stale cache if available
        if cached is not None:
            logger.warning("Returning stale cached data for FRED series %s", series_id)
            result, _ = cached
            return result

        raise FREDDataUnavailableError(f"FRED API returned no usable data for series {series_id}")

    except FREDDataUnavailableError:
        raise
    except Exception as exc:
        logger.warning("FRED API call failed for %s: %s", series_id, exc)
        # Return stale cache if available
        if cached is not None:
            logger.warning("Returning stale cached data for FRED series %s", series_id)
            result, _ = cached
            if start_date:
                return [r for r in result if r["date"] >= start_date]
            return result
        raise FREDDataUnavailableError(
            f"FRED API call failed for series {series_id}: {exc}"
        ) from exc


async def get_bls_ppi_series(series_id: str) -> list[dict]:
    """Fetch BLS PPI data for a series.

    Parameters
    ----------
    series_id:
        BLS series identifier (e.g. ``"WPUIP2300001"``).

    Returns
    -------
    List of ``{date, price_index}`` dicts sorted chronologically.

    Raises
    ------
    FREDDataUnavailableError
        If the BLS API is unreachable and no cached data exists.
    """
    # 1. Check module-level cache
    now = time.time()
    cached = _bls_cache.get(series_id)
    if cached is not None:
        result, ts = cached
        if now - ts < _BLS_CACHE_TTL:
            logger.debug("BLS cache hit for %s (age=%.0fs)", series_id, now - ts)
            return result

    # 2. Attempt real BLS API call
    current_year = date.today().year
    bls_key = os.environ.get("BLS_API_KEY")

    try:
        url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        payload: dict = {
            "seriesid": [series_id],
            "startyear": str(current_year - 3),
            "endyear": str(current_year),
        }
        if bls_key:
            payload["registrationkey"] = bls_key

        headers = {"Content-Type": "application/json"}

        client = _get_http_client()
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "REQUEST_SUCCEEDED":
            raise ValueError(f"BLS API returned status: {data.get('status')}")

        results: list[dict] = []
        for series in data.get("Results", {}).get("series", []):
            for entry in series.get("data", []):
                year = entry["year"]
                period = entry["period"]  # M01..M12
                if not period.startswith("M"):
                    continue
                month = period.replace("M", "").zfill(2)
                date_str = f"{year}-{month}-01"
                results.append(
                    {
                        "date": date_str,
                        "price_index": float(entry["value"]),
                    }
                )

        results.sort(key=lambda d: d["date"])

        if results:
            _bls_cache[series_id] = (results, now)
            logger.info(
                "Fetched %d BLS PPI data points for series %s",
                len(results),
                series_id,
            )
            return results

        logger.warning(
            "BLS API returned 0 usable data points for %s",
            series_id,
        )

    except Exception as exc:
        logger.warning("BLS API call failed for %s: %s", series_id, exc)

    # 3. Return stale cache if available, otherwise raise
    if cached is not None:
        logger.warning("Returning stale cached BLS data for series %s", series_id)
        result, _ = cached
        return result

    raise FREDDataUnavailableError(f"BLS API unavailable and no cached data for series {series_id}")


# ---------------------------------------------------------------------------
# Backfill: pull full FRED historical data
# ---------------------------------------------------------------------------


async def backfill_fred_history(
    series_ids: list[str] | None = None,
    db_session=None,
) -> dict[str, int]:
    """Pull full historical data from FRED for each series and store it.

    Parameters
    ----------
    series_ids:
        Specific series to backfill.  Defaults to all series in
        ``FRED_SERIES_MAP``.
    db_session:
        Optional async SQLAlchemy session.  When provided, data is
        persisted to the ``fred_price_history`` table.  When ``None``,
        data is only stored in the in-memory cache.

    Returns
    -------
    Dict mapping series_id to count of observations fetched.
    """
    if series_ids is None:
        series_ids = list(FRED_SERIES_MAP.keys())

    results: dict[str, int] = {}

    for series_id in series_ids:
        try:
            data = await fetch_fred_data(series_id, full_history=True)
            results[series_id] = len(data)
            logger.info(
                "Backfilled %d observations for FRED series %s",
                len(data),
                series_id,
            )

            # Persist to database if session provided
            if db_session is not None:
                await _persist_fred_history(db_session, series_id, data)

        except FREDDataUnavailableError as exc:
            logger.error("Backfill failed for %s: %s", series_id, exc)
            results[series_id] = 0
        except Exception as exc:
            logger.error("Unexpected error backfilling %s: %s", series_id, exc)
            results[series_id] = 0

    total = sum(results.values())
    logger.info(
        "FRED backfill complete: %d series, %d total observations",
        len(results),
        total,
    )
    return results


async def _persist_fred_history(
    db_session,
    series_id: str,
    data: list[dict],
) -> None:
    """Write FRED observations to the fred_price_history table.

    Uses an upsert pattern (ON CONFLICT DO UPDATE) so backfill is
    idempotent.
    """
    try:
        from sqlalchemy import text as sa_text

        meta = FRED_SERIES_MAP.get(series_id, {})
        category = meta.get("category", "unknown")
        csi_division = meta.get("csi_division")

        # Batch insert with upsert
        for obs in data:
            await db_session.execute(
                sa_text(
                    """
                    INSERT INTO fred_price_history
                        (series_id, observation_date, value, category, csi_division)
                    VALUES
                        (:series_id, :obs_date, :value, :category, :csi_division)
                    ON CONFLICT (series_id, observation_date)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                    """
                ),
                {
                    "series_id": series_id,
                    "obs_date": obs["date"],
                    "value": obs["value"],
                    "category": category,
                    "csi_division": csi_division,
                },
            )

        await db_session.commit()
        logger.info(
            "Persisted %d observations for series %s to fred_price_history",
            len(data),
            series_id,
        )
    except Exception as exc:
        logger.error("Failed to persist FRED history for %s: %s", series_id, exc)
        await db_session.rollback()
        raise


# ---------------------------------------------------------------------------
# Daily refresh helper
# ---------------------------------------------------------------------------


async def refresh_fred_data(
    db_session=None,
) -> dict[str, bool]:
    """Refresh FRED data for all tracked series (last 36 months).

    Designed to be called by a daily scheduled task.

    Returns
    -------
    Dict mapping series_id to True if refresh succeeded.
    """
    results: dict[str, bool] = {}

    for series_id in FRED_SERIES_MAP:
        try:
            # Clear cache to force fresh fetch
            _fred_cache.pop(series_id, None)

            data = await fetch_fred_data(series_id)
            results[series_id] = True

            if db_session is not None:
                await _persist_fred_history(db_session, series_id, data)

            logger.info(
                "FRED refresh OK: %s (%s) - %d observations",
                series_id,
                FRED_SERIES_MAP[series_id]["description"],
                len(data),
            )
        except Exception as exc:
            results[series_id] = False
            logger.error("FRED refresh failed for %s: %s", series_id, exc)

    succeeded = sum(1 for v in results.values() if v)
    logger.info(
        "FRED daily refresh complete: %d/%d series succeeded",
        succeeded,
        len(results),
    )
    return results


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def get_series_for_category(category: str) -> str | None:
    """Return the FRED series_id for a material category name."""
    cat_lower = category.lower().replace(" ", "_")
    # Direct lookup
    if cat_lower in _CATEGORY_TO_SERIES:
        return _CATEGORY_TO_SERIES[cat_lower]
    # Substring match
    for cat_key, series_id in _CATEGORY_TO_SERIES.items():
        if cat_key in cat_lower or cat_lower in cat_key:
            return series_id
    return None


def get_series_for_csi(csi_division: str) -> list[str]:
    """Return all FRED series_ids mapped to a CSI MasterFormat division."""
    div = csi_division.strip().zfill(2)
    return _CSI_TO_SERIES.get(div, [])


# ---------------------------------------------------------------------------
# Public API -- forecast_prices
# ---------------------------------------------------------------------------


async def forecast_prices(
    historical_data: list[dict] | None = None,
    horizon_months: int = 6,
    material_category: str = "concrete",
    *,
    series_id: str | None = None,
) -> dict:
    """Generate price forecast using ARIMA+Prophet ensemble.

    The function can be called in two ways:

    1. **With pre-fetched data** -- pass ``historical_data`` directly
       (list of ``{date, price_index}`` dicts).
    2. **With a series_id** -- pass ``series_id`` (and optionally
       ``material_category``); the function fetches FRED data internally.

    When ``series_id`` is provided but ``historical_data`` is not, the
    function fetches real FRED data.  If FRED data is unavailable the
    function returns an error result rather than synthetic data.

    Parameters
    ----------
    historical_data:
        Pre-fetched list of ``{date, price_index}`` sorted by date.
    horizon_months:
        Number of months to forecast (default 6; supports 3, 6, 12).
    material_category:
        Category label for the material being forecast.
    series_id:
        FRED series identifier.  When provided and ``historical_data``
        is None, data is fetched from FRED automatically.

    Returns
    -------
    dict with forecasts, model_used, rmse, trend, summary.
    """
    # ── Resolve historical data ──────────────────────────────────────
    if historical_data is None and series_id is not None:
        try:
            fred_data = await fetch_fred_data(series_id)
            historical_data = [{"date": d["date"], "price_index": d["value"]} for d in fred_data]
            logger.info(
                "Loaded %d observations from FRED series %s for forecasting",
                len(historical_data),
                series_id,
            )
        except FREDDataUnavailableError as exc:
            logger.error("Cannot forecast %s: FRED data unavailable: %s", material_category, exc)
            return {
                "forecasts": [],
                "model_used": "none",
                "rmse": 0.0,
                "trend": "stable",
                "summary": (
                    f"FRED data unavailable for {material_category} (series {series_id}): {exc}"
                ),
            }
    elif historical_data is None and series_id is None:
        # Try to auto-resolve series from category
        resolved = get_series_for_category(material_category)
        if resolved:
            return await forecast_prices(
                horizon_months=horizon_months,
                material_category=material_category,
                series_id=resolved,
            )
        return {
            "forecasts": [],
            "model_used": "none",
            "rmse": 0.0,
            "trend": "stable",
            "summary": (
                f"No historical data provided and no FRED series mapped "
                f"for category '{material_category}'."
            ),
        }

    if not historical_data:
        return {
            "forecasts": [],
            "model_used": "none",
            "rmse": 0.0,
            "trend": "stable",
            "summary": "No historical data provided for forecasting.",
        }

    # ── Run forecasting models ───────────────────────────────────────
    arima_forecasts: list[dict] | None = None
    arima_rmse: float = 0.0
    prophet_forecasts: list[dict] | None = None
    prophet_rmse: float = 0.0

    values = [d["price_index"] for d in historical_data]
    last_date = datetime.strptime(historical_data[-1]["date"], "%Y-%m-%d")

    # --- Try ARIMA ---
    if _HAS_ARIMA and len(historical_data) >= 6:
        try:
            import numpy as _np

            series = _np.array(values, dtype=float)
            order = _select_arima_order(series)
            model = ARIMA_MODEL(series, order=order)  # pyright: ignore[reportOptionalCall]
            fitted = model.fit()

            forecast_result = fitted.get_forecast(steps=horizon_months)
            predicted = forecast_result.predicted_mean
            conf_int = forecast_result.conf_int(alpha=0.05)

            arima_forecasts = []
            for m in range(horizon_months):
                fdate = last_date + timedelta(days=30 * (m + 1))
                arima_forecasts.append(
                    {
                        "date": fdate.strftime("%Y-%m-%d"),
                        "forecast_value": round(float(predicted[m]), 2),
                        "lower_bound": round(float(conf_int[m, 0]), 2),
                        "upper_bound": round(float(conf_int[m, 1]), 2),
                    }
                )

            residuals = fitted.resid
            arima_rmse = float(_np.sqrt(_np.mean(residuals**2)))
            logger.info(
                "ARIMA(%d,%d,%d) forecast complete for %s: RMSE=%.2f",
                *order,
                material_category,
                arima_rmse,
            )
        except Exception as exc:
            logger.warning("ARIMA forecasting failed for %s: %s", material_category, exc)
            arima_forecasts = None

    # --- Try Prophet ---
    if _HAS_PROPHET and len(historical_data) >= 6:
        try:
            import pandas as pd

            df = pd.DataFrame(
                {
                    "ds": [d["date"] for d in historical_data],
                    "y": values,
                }
            )
            df["ds"] = pd.to_datetime(df["ds"])

            model = Prophet()  # pyright: ignore[reportOptionalCall]
            model.fit(df)

            future = model.make_future_dataframe(periods=horizon_months, freq="MS")
            prediction = model.predict(future)

            forecast_rows = prediction.tail(horizon_months)
            prophet_forecasts = []
            for _, row in forecast_rows.iterrows():
                prophet_forecasts.append(
                    {
                        "date": row["ds"].strftime("%Y-%m-%d"),  # pyright: ignore[reportAttributeAccessIssue]
                        "forecast_value": round(float(row["yhat"]), 2),
                        "lower_bound": round(float(row["yhat_lower"]), 2),
                        "upper_bound": round(float(row["yhat_upper"]), 2),
                    }
                )

            in_sample = prediction.head(len(historical_data))
            residuals = [values[i] - float(in_sample.iloc[i]["yhat"]) for i in range(len(values))]
            prophet_rmse = (sum(r**2 for r in residuals) / len(residuals)) ** 0.5
            logger.info(
                "Prophet forecast complete for %s: RMSE=%.2f",
                material_category,
                prophet_rmse,
            )
        except Exception as exc:
            logger.warning("Prophet forecasting failed for %s: %s", material_category, exc)
            prophet_forecasts = None

    # --- Build ensemble or use best available ---
    if arima_forecasts is not None and prophet_forecasts is not None:
        forecasts: list[dict] = []
        for a, p in zip(arima_forecasts, prophet_forecasts, strict=False):
            forecasts.append(
                {
                    "date": a["date"],
                    "forecast_value": round((a["forecast_value"] + p["forecast_value"]) / 2, 2),
                    "lower_bound": round(min(a["lower_bound"], p["lower_bound"]), 2),
                    "upper_bound": round(max(a["upper_bound"], p["upper_bound"]), 2),
                }
            )
        model_used = "arima+prophet_ensemble"
        if _HAS_ARIMA and len(historical_data) >= 6:
            import numpy as _np

            try:
                series = _np.array(values, dtype=float)
                order = _select_arima_order(series)
                arima_model = ARIMA_MODEL(series, order=order)  # pyright: ignore[reportOptionalCall]
                arima_fitted = arima_model.fit()
                arima_in_sample = arima_fitted.fittedvalues

                import pandas as pd

                df = pd.DataFrame({"ds": [d["date"] for d in historical_data], "y": values})
                df["ds"] = pd.to_datetime(df["ds"])
                prophet_model = Prophet()  # pyright: ignore[reportOptionalCall]
                prophet_model.fit(df)
                prophet_in_sample = prophet_model.predict(df)["yhat"].values

                n_common = min(len(arima_in_sample), len(prophet_in_sample), len(values))
                arima_vals = _np.array(arima_in_sample[-n_common:], dtype=float)
                prophet_vals = _np.array(prophet_in_sample[-n_common:], dtype=float)
                actual_vals = _np.array(values[-n_common:], dtype=float)
                ensemble_vals = (arima_vals + prophet_vals) / 2.0
                rmse = round(float(_np.sqrt(_np.mean((ensemble_vals - actual_vals) ** 2))), 4)
            except Exception:
                rmse = round((arima_rmse + prophet_rmse) / 2, 4)
        else:
            rmse = round((arima_rmse + prophet_rmse) / 2, 4)
    elif arima_forecasts is not None:
        forecasts = arima_forecasts
        model_used = "arima_only"
        rmse = round(arima_rmse, 4)
    elif prophet_forecasts is not None:
        forecasts = prophet_forecasts
        model_used = "prophet_only"
        rmse = round(prophet_rmse, 4)
    else:
        forecasts_and_slope = _linear_trend_forecast(historical_data, horizon_months)
        forecasts = forecasts_and_slope[0]
        model_used = "linear_trend"
        n = len(historical_data)
        x_vals = list(range(n))
        y_vals = values
        x_mean = sum(x_vals) / n
        y_mean = sum(y_vals) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals, strict=False))
        den = sum((x - x_mean) ** 2 for x in x_vals)
        s = num / den if den != 0 else 0.0
        intercept = y_mean - s * x_mean
        residuals_list = [y - (s * x + intercept) for x, y in zip(x_vals, y_vals, strict=False)]
        rmse = round((sum(r**2 for r in residuals_list) / n) ** 0.5, 4)

    # --- Apply material-specific seasonal adjustments ---
    forecasts = _apply_seasonal_adjustments(forecasts, material_category)

    # Determine trend
    if len(forecasts) >= 2:
        forecast_slope = (forecasts[-1]["forecast_value"] - forecasts[0]["forecast_value"]) / max(
            len(forecasts) - 1, 1
        )
    else:
        forecast_slope = 0.0

    trend = _determine_trend(forecast_slope)

    trend_label = {
        "rising": "an upward",
        "falling": "a downward",
        "stable": "a stable",
    }[trend]

    summary = (
        (
            f"Price forecast for {material_category} over {horizon_months} months "
            f"using {model_used}. The forecast indicates {trend_label} trend "
            f"with in-sample RMSE of {rmse:.2f}. "
            f"Projected range: {forecasts[0]['lower_bound']:.2f} - "
            f"{forecasts[-1]['upper_bound']:.2f}."
        )
        if forecasts
        else f"No forecast generated for {material_category}."
    )

    logger.info(
        "Price forecast for %s: model=%s, trend=%s, horizon=%d months",
        material_category,
        model_used,
        trend,
        horizon_months,
    )

    return {
        "forecasts": forecasts,
        "model_used": model_used,
        "rmse": rmse,
        "trend": trend,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Multi-horizon convenience
# ---------------------------------------------------------------------------


async def forecast_prices_multi_horizon(
    series_id: str,
    material_category: str = "concrete",
    horizons: tuple[int, ...] = (3, 6, 12),
) -> dict[int, dict]:
    """Generate forecasts for multiple horizons (3/6/12 months).

    Returns a dict keyed by horizon month count.
    """
    results: dict[int, dict] = {}
    for h in horizons:
        results[h] = await forecast_prices(
            horizon_months=h,
            material_category=material_category,
            series_id=series_id,
        )
    return results
