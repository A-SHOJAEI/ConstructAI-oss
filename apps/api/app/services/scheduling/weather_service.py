"""Weather integration for construction schedule impact analysis.

Multi-provider fallback chain:
  1. NOAA/NWS  (primary forecast — free, no key required)
  2. Open-Meteo (forecast + historical archive — free, no key)
  3. OpenWeatherMap (backup forecast — requires API key)

Construction-specific impact functions evaluate site conditions against
industry-standard thresholds and return risk-level assessments.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PI-08: Reusable HTTP client for connection pooling
# ---------------------------------------------------------------------------

_weather_http_client: httpx.AsyncClient | None = None
_weather_client_lock = asyncio.Lock()


async def _get_weather_http_client() -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient for weather API calls."""
    global _weather_http_client
    if httpx is None:
        raise RuntimeError("httpx is required for weather service")
    if _weather_http_client is None or _weather_http_client.is_closed:
        async with _weather_client_lock:
            if _weather_http_client is None or _weather_http_client.is_closed:
                _weather_http_client = httpx.AsyncClient(
                    timeout=30.0,
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                        keepalive_expiry=120,
                    ),
                )
    return _weather_http_client


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WeatherDataUnavailableError(Exception):
    """Raised when no weather provider can return data."""


# ---------------------------------------------------------------------------
# Risk levels & data classes
# ---------------------------------------------------------------------------


class RiskLevel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass
class WeatherImpact:
    """Result of a construction-specific weather assessment."""

    activity: str
    allowed: bool
    risk_level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_weather_cache: dict[str, tuple[float, list[dict]]] = {}
# Construction safety decisions require recent weather data.
# Stale cache limited to 1 hour to avoid acting on outdated conditions.
_STALE_CACHE_MAX_AGE = 3600


# ---------------------------------------------------------------------------
# Default weather sensitivity thresholds by activity type
# ---------------------------------------------------------------------------

DEFAULT_SENSITIVITY: dict[str, dict[str, float]] = {
    "concrete_pour": {
        "min_temp": 40.0,
        "max_temp": 95.0,
        "max_precip": 0.1,
        "max_wind": 25.0,
    },
    "steel_erection": {
        "max_wind": 30.0,
        "max_precip": 0.5,
    },
    "excavation": {
        "max_precip": 0.5,
    },
    "roofing": {
        "max_precip": 0.0,
        "max_wind": 20.0,
        "min_temp": 35.0,
    },
    "painting_exterior": {
        "min_temp": 50.0,
        "max_precip": 0.0,
    },
    "crane_operation": {
        "max_wind": 30.0,
        "max_precip": 0.5,
    },
    "general": {
        "min_temp": 20.0,
        "max_precip": 1.0,
        "max_wind": 40.0,
    },
}


# ---------------------------------------------------------------------------
# Construction-specific weather impact functions
# ---------------------------------------------------------------------------


def can_pour_concrete(weather: dict) -> WeatherImpact:
    """Evaluate whether conditions allow concrete placement.

    ACI 306R cold-weather thresholds, ACI 305R hot-weather thresholds:
    - Temp must be 40-95 F (below 40 requires cold-weather protection;
      above 95 risks flash set and plastic shrinkage cracking)
    - Precipitation < 0.10 in — fresh concrete surface wash-out
    - Wind < 25 mph — accelerates surface drying / plastic cracking
    - Humidity < 20% at high temp increases evaporation risk
    """
    reasons: list[str] = []
    recs: list[str] = []
    risk = RiskLevel.GREEN

    temp_min = weather.get("temperature_min", 60.0)
    temp_max = weather.get("temperature_max", 70.0)
    precip_mm = weather.get("precipitation_mm", 0.0)
    wind = weather.get("wind_speed_max", 0.0)
    humidity = weather.get("humidity", 50.0)

    precip_in = precip_mm / 25.4

    if temp_min < 35:
        reasons.append(f"Temperature {temp_min:.0f}F below 35F — risk of freezing fresh concrete")
        recs.append("Use heated enclosures and insulating blankets per ACI 306R")
        risk = RiskLevel.RED
    elif temp_min < 40:
        reasons.append(f"Temperature {temp_min:.0f}F below 40F — cold weather precautions needed")
        recs.append("Use hot water in mix and insulating blankets")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if temp_max > 95:
        reasons.append(f"Temperature {temp_max:.0f}F exceeds 95F — flash set risk")
        recs.append("Use retarders, ice in mix water, pour in early morning")
        risk = RiskLevel.RED
    elif temp_max > 85:
        reasons.append(f"Temperature {temp_max:.0f}F above 85F — accelerated curing")
        recs.append("Consider retarding admixtures and evaporation control")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if precip_in > 0.1:
        reasons.append(f"Precipitation {precip_in:.2f}in exceeds 0.10in — surface washout risk")
        recs.append("Delay pour or prepare tarps/covers")
        risk = RiskLevel.RED
    elif precip_in > 0.0:
        reasons.append(f"Trace precipitation {precip_in:.2f}in")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if wind > 25:
        reasons.append(f"Wind {wind:.0f}mph exceeds 25mph — plastic shrinkage cracking risk")
        recs.append("Use wind breaks and apply evaporation retarder")
        risk = RiskLevel.RED
    elif wind > 15:
        reasons.append(f"Wind {wind:.0f}mph — increased surface evaporation")
        recs.append("Monitor evaporation rate; apply curing compound promptly")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if temp_max > 80 and humidity < 20:
        reasons.append(f"Low humidity {humidity:.0f}% with high temp — rapid evaporation")
        recs.append("Apply evaporation retarder and fog-spray surface")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    return WeatherImpact(
        activity="concrete_pour",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def can_operate_crane(weather: dict) -> WeatherImpact:
    """Evaluate crane operation safety.

    OSHA 1926.1431 & manufacturer guidelines:
    - Wind > 30 mph — cease all crane operations
    - Wind 20-30 mph — restrict to lighter loads, no personnel hoisting
    - Lightning within 10 miles — cease operations
    - Visibility < 0.25 miles — cease operations
    """
    reasons: list[str] = []
    recs: list[str] = []
    risk = RiskLevel.GREEN

    wind = weather.get("wind_speed_max", 0.0)
    weather_code = weather.get("weather_code", 0)
    precip_mm = weather.get("precipitation_mm", 0.0)

    if wind > 35:
        reasons.append(f"Wind {wind:.0f}mph exceeds 35mph — all crane ops must cease")
        recs.append("Secure boom, lower loads, stand down until wind subsides")
        risk = RiskLevel.RED
    elif wind > 30:
        reasons.append(f"Wind {wind:.0f}mph exceeds 30mph — cease personnel hoisting")
        recs.append("Restrict to light loads only, continuous wind monitoring")
        risk = RiskLevel.RED
    elif wind > 20:
        reasons.append(f"Wind {wind:.0f}mph — load chart derating may apply")
        recs.append("Review load chart for wind derating; use tag lines")
        risk = RiskLevel.YELLOW

    # WMO codes 95-99 = thunderstorm
    if weather_code >= 95:
        reasons.append("Thunderstorm indicated — lightning risk")
        recs.append("Cease crane operations, seek shelter, 30-min stand down after last strike")
        risk = RiskLevel.RED

    if precip_mm / 25.4 > 0.5:
        reasons.append(f"Heavy precipitation {precip_mm / 25.4:.2f}in — reduced visibility")
        recs.append("Ensure operator visibility; use signal person if needed")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    return WeatherImpact(
        activity="crane_operation",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def can_excavate(weather: dict) -> WeatherImpact:
    """Evaluate excavation conditions.

    OSHA 1926 Subpart P — soil stability is weather-dependent:
    - Heavy rain saturates soil, increasing cave-in risk
    - Frozen ground may create false stability that fails on thaw
    - Standing water in trenches requires pumping
    """
    reasons: list[str] = []
    recs: list[str] = []
    risk = RiskLevel.GREEN

    precip_mm = weather.get("precipitation_mm", 0.0)
    temp_min = weather.get("temperature_min", 60.0)
    precip_in = precip_mm / 25.4

    if precip_in > 1.0:
        reasons.append(f"Heavy rain {precip_in:.2f}in — saturated soil, cave-in risk")
        recs.append("Delay excavation 24-48h; reassess soil conditions per competent person")
        risk = RiskLevel.RED
    elif precip_in > 0.5:
        reasons.append(f"Moderate rain {precip_in:.2f}in — soil saturation risk")
        recs.append("Inspect trench walls; install additional shoring if needed")
        risk = RiskLevel.YELLOW
    elif precip_in > 0.1:
        reasons.append(f"Light rain {precip_in:.2f}in")
        recs.append("Monitor soil conditions; have dewatering equipment ready")
        risk = RiskLevel.YELLOW

    if temp_min < 25:
        reasons.append(f"Temperature {temp_min:.0f}F — frozen ground conditions")
        recs.append("Frozen soil may mask instability; extra caution on thaw days")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    return WeatherImpact(
        activity="excavation",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def can_do_roofing(weather: dict) -> WeatherImpact:
    """Evaluate roofing work conditions.

    OSHA fall protection + material requirements:
    - ANY precipitation on sloped roofs = stop work (slip hazard)
    - Wind > 20 mph = loose materials become projectiles
    - Temp < 35 F = adhesives and sealants won't cure properly
    - Temp > 100 F = worker heat stress on exposed roof
    """
    reasons: list[str] = []
    recs: list[str] = []
    risk = RiskLevel.GREEN

    precip_mm = weather.get("precipitation_mm", 0.0)
    wind = weather.get("wind_speed_max", 0.0)
    temp_min = weather.get("temperature_min", 60.0)
    temp_max = weather.get("temperature_max", 70.0)

    if precip_mm > 0:
        reasons.append(f"Precipitation {precip_mm / 25.4:.2f}in — slip hazard on roof surfaces")
        recs.append("Cease roofing work; wait for surfaces to dry completely")
        risk = RiskLevel.RED

    if wind > 25:
        reasons.append(f"Wind {wind:.0f}mph — loose materials and fall risk")
        recs.append("Secure all materials; cease work at height")
        risk = RiskLevel.RED
    elif wind > 20:
        reasons.append(f"Wind {wind:.0f}mph — materials may become airborne")
        recs.append("Secure materials; use additional tie-downs")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if temp_min < 35:
        reasons.append(f"Temperature {temp_min:.0f}F — adhesives/sealants won't cure")
        recs.append("Use cold-weather adhesive formulations or delay roofing")
        risk = RiskLevel.RED
    elif temp_min < 45:
        reasons.append(f"Temperature {temp_min:.0f}F — slow adhesive cure times")
        recs.append("Verify adhesive temperature range; allow extra cure time")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if temp_max > 100:
        reasons.append(f"Temperature {temp_max:.0f}F — extreme heat on exposed roof")
        recs.append("Implement heat illness prevention plan; frequent hydration breaks")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    return WeatherImpact(
        activity="roofing",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def can_paint_exterior(weather: dict) -> WeatherImpact:
    """Evaluate exterior painting conditions.

    Paint manufacturer specs (typical latex/acrylic):
    - Temperature 50-90 F for application and 4h after
    - No rain for 4-6 hours after application
    - Humidity < 85% (high humidity prevents proper film formation)
    - Surface temperature must be above dew point
    """
    reasons: list[str] = []
    recs: list[str] = []
    risk = RiskLevel.GREEN

    temp_min = weather.get("temperature_min", 60.0)
    temp_max = weather.get("temperature_max", 70.0)
    precip_mm = weather.get("precipitation_mm", 0.0)
    humidity = weather.get("humidity", 50.0)
    wind = weather.get("wind_speed_max", 0.0)

    if precip_mm > 0:
        reasons.append(f"Precipitation {precip_mm / 25.4:.2f}in — paint washout before cure")
        recs.append("Delay exterior painting until dry conditions forecast for 6+ hours")
        risk = RiskLevel.RED

    if temp_min < 50:
        reasons.append(f"Temperature {temp_min:.0f}F below 50F — latex paint won't form film")
        recs.append("Use cold-weather paint formula or delay painting")
        risk = RiskLevel.RED
    elif temp_min < 55:
        reasons.append(f"Temperature {temp_min:.0f}F — marginal for standard latex")
        recs.append("Paint mid-day only; verify surface temperature above 50F")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if temp_max > 90:
        reasons.append(f"Temperature {temp_max:.0f}F — paint may dry too fast, lap marks")
        recs.append("Paint on shaded side; use extended-dry-time additives")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if humidity > 85:
        reasons.append(f"Humidity {humidity:.0f}% — paint film won't form properly")
        recs.append("Delay painting until humidity drops below 85%")
        risk = RiskLevel.RED
    elif humidity > 70:
        reasons.append(f"Humidity {humidity:.0f}% — extended dry time expected")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    if wind > 25:
        reasons.append(f"Wind {wind:.0f}mph — overspray and uneven application")
        recs.append("Use brush/roller instead of spray; shield work area")
        risk = max(risk, RiskLevel.YELLOW, key=lambda r: list(RiskLevel).index(r))

    return WeatherImpact(
        activity="painting_exterior",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def weather_impact_score(weather: dict) -> WeatherImpact:
    """Compute an overall construction weather impact score (0-100).

    0 = perfect conditions, 100 = impossible to work.
    Weighted factors: temperature (30%), precipitation (30%),
    wind (20%), visibility/storms (20%).
    """
    score = 0.0
    reasons: list[str] = []

    temp_min = weather.get("temperature_min", 60.0)
    temp_max = weather.get("temperature_max", 70.0)
    precip_mm = weather.get("precipitation_mm", 0.0)
    wind = weather.get("wind_speed_max", 0.0)
    weather_code = weather.get("weather_code", 0)

    avg_temp = (temp_min + temp_max) / 2.0

    # Temperature score (0-30): optimal 55-75F
    if avg_temp < 20 or avg_temp > 105:
        temp_score = 30.0
    elif avg_temp < 35 or avg_temp > 95:
        temp_score = 25.0
    elif avg_temp < 45 or avg_temp > 85:
        temp_score = 15.0
    elif avg_temp < 55 or avg_temp > 75:
        temp_score = 5.0
    else:
        temp_score = 0.0
    score += temp_score
    if temp_score > 0:
        reasons.append(f"Temperature impact: {temp_score:.0f}/30 (avg {avg_temp:.0f}F)")

    # Precipitation score (0-30)
    precip_in = precip_mm / 25.4
    if precip_in > 1.0:
        precip_score = 30.0
    elif precip_in > 0.5:
        precip_score = 25.0
    elif precip_in > 0.1:
        precip_score = 15.0
    elif precip_in > 0.0:
        precip_score = 5.0
    else:
        precip_score = 0.0
    score += precip_score
    if precip_score > 0:
        reasons.append(f"Precipitation impact: {precip_score:.0f}/30 ({precip_in:.2f}in)")

    # Wind score (0-20)
    if wind > 40:
        wind_score = 20.0
    elif wind > 30:
        wind_score = 15.0
    elif wind > 20:
        wind_score = 10.0
    elif wind > 10:
        wind_score = 3.0
    else:
        wind_score = 0.0
    score += wind_score
    if wind_score > 0:
        reasons.append(f"Wind impact: {wind_score:.0f}/20 ({wind:.0f}mph)")

    # Storm/visibility score (0-20)
    if weather_code >= 95:
        storm_score = 20.0
    elif weather_code >= 65:
        storm_score = 15.0
    elif weather_code >= 61:
        storm_score = 8.0
    elif weather_code >= 3:
        storm_score = 3.0
    else:
        storm_score = 0.0
    score += storm_score
    if storm_score > 0:
        reasons.append(f"Storm/visibility impact: {storm_score:.0f}/20 (code {weather_code})")

    score = min(100.0, score)

    if score >= 60:
        risk = RiskLevel.RED
    elif score >= 30:
        risk = RiskLevel.YELLOW
    else:
        risk = RiskLevel.GREEN

    return WeatherImpact(
        activity="overall",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=[*reasons, f"Overall impact score: {score:.0f}/100"],
        recommendations=[],
    )


def heat_illness_risk(weather: dict) -> WeatherImpact:
    """Assess heat illness risk for outdoor construction workers.

    Uses OSHA/NIOSH heat index categories:
    - < 91 F heat index: Lower risk (green)
    - 91-103 F: Moderate risk (yellow) — implement precautions
    - 103-115 F: High risk (red) — additional protections required
    - > 115 F: Very high risk (red) — consider stopping outdoor work

    Heat index approximation from temperature and humidity.
    """
    reasons: list[str] = []
    recs: list[str] = []

    temp = weather.get("temperature_max", 70.0)
    humidity = weather.get("humidity", 50.0)

    # Simplified Rothfusz heat index equation
    hi = _heat_index(temp, humidity) if temp >= 80 else temp

    if hi >= 115:
        risk = RiskLevel.RED
        reasons.append(f"Heat index {hi:.0f}F (Very High) — outdoor work extremely dangerous")
        recs.append("Consider stopping outdoor work entirely")
        recs.append("If work continues: mandatory buddy system, cooling stations every 15 min")
    elif hi >= 103:
        risk = RiskLevel.RED
        reasons.append(f"Heat index {hi:.0f}F (High) — heat stroke risk elevated")
        recs.append("Mandatory 15-min rest per hour in shaded/cooled area")
        recs.append("Deploy on-site medical personnel; ensure cold water access")
    elif hi >= 91:
        risk = RiskLevel.YELLOW
        reasons.append(f"Heat index {hi:.0f}F (Moderate) — heat illness precautions needed")
        recs.append("Ensure water, rest, shade (OSHA campaign)")
        recs.append("Schedule heavy work for early morning; acclimatize new workers")
    else:
        risk = RiskLevel.GREEN
        reasons.append(f"Heat index {hi:.0f}F — lower heat illness risk")

    return WeatherImpact(
        activity="heat_illness",
        allowed=risk != RiskLevel.RED,
        risk_level=risk,
        reasons=reasons,
        recommendations=recs,
    )


def _heat_index(temp_f: float, rh: float) -> float:
    """Compute the NOAA/NWS heat index (Rothfusz regression)."""
    T = temp_f
    R = rh
    hi = (
        -42.379
        + 2.04901523 * T
        + 10.14333127 * R
        - 0.22475541 * T * R
        - 6.83783e-3 * T**2
        - 5.481717e-2 * R**2
        + 1.22874e-3 * T**2 * R
        + 8.5282e-4 * T * R**2
        - 1.99e-6 * T**2 * R**2
    )
    # Adjustment for low humidity
    if R < 13 and 80 <= T <= 112:
        adj = ((13 - R) / 4) * math.sqrt((17 - abs(T - 95)) / 17)
        hi -= adj
    # Adjustment for high humidity
    elif R > 85 and 80 <= T <= 87:
        adj = ((R - 85) / 10) * ((87 - T) / 5)
        hi += adj
    return round(hi, 1)


# Map of activity-type keywords to assessment functions
IMPACT_FUNCTIONS: dict[str, Any] = {
    "concrete_pour": can_pour_concrete,
    "concrete": can_pour_concrete,
    "crane_operation": can_operate_crane,
    "steel_erection": can_operate_crane,
    "crane": can_operate_crane,
    "excavation": can_excavate,
    "earthwork": can_excavate,
    "roofing": can_do_roofing,
    "painting_exterior": can_paint_exterior,
    "painting": can_paint_exterior,
}


# ---------------------------------------------------------------------------
# Provider 1: NOAA/NWS (primary — free, no API key)
# ---------------------------------------------------------------------------


async def _fetch_noaa_forecast(latitude: float, longitude: float) -> list[dict]:
    """Fetch 7-day forecast from NOAA/NWS api.weather.gov.

    Two-step process:
      1. GET /points/{lat},{lon} → returns forecast grid URL
      2. GET the gridpoint forecast URL → returns daily periods

    NOAA API requires a User-Agent header identifying the application.
    """
    if httpx is None:
        raise WeatherDataUnavailableError("httpx not installed")

    headers = {
        "User-Agent": "(ConstructAI, constructai@example.com)",
        "Accept": "application/geo+json",
    }

    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        # Step 1: resolve grid coordinates
        points_url = f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}"
        resp = await client.get(points_url)
        resp.raise_for_status()
        points_data = resp.json()

        forecast_url = points_data["properties"]["forecast"]

        # SECURITY: Validate that the forecast URL points to the expected
        # NOAA domain to prevent SSRF via a crafted JSON response.
        if not forecast_url.startswith("https://api.weather.gov/"):
            raise WeatherDataUnavailableError(
                f"NOAA returned unexpected forecast URL domain: {forecast_url[:80]}"
            )

        # Step 2: fetch the actual forecast
        resp2 = await client.get(forecast_url)
        resp2.raise_for_status()
        forecast_data = resp2.json()

    periods = forecast_data.get("properties", {}).get("periods", [])

    # NOAA returns day/night periods — merge into daily records
    daily: dict[str, dict] = {}
    for period in periods:
        # startTime example: "2025-03-01T06:00:00-05:00"
        date_str = period["startTime"][:10]
        if date_str not in daily:
            daily[date_str] = {
                "date": date_str,
                "temperature_max": period["temperature"],
                "temperature_min": period["temperature"],
                "precipitation_mm": 0.0,
                "wind_speed_max": _parse_wind_speed(period.get("windSpeed", "0 mph")),
                "weather_code": _noaa_short_to_wmo(period.get("shortForecast", "")),
            }
        else:
            entry = daily[date_str]
            entry["temperature_max"] = max(entry["temperature_max"], period["temperature"])
            entry["temperature_min"] = min(entry["temperature_min"], period["temperature"])
            wind = _parse_wind_speed(period.get("windSpeed", "0 mph"))
            entry["wind_speed_max"] = max(entry["wind_speed_max"], wind)
            # Take the worst weather code
            code = _noaa_short_to_wmo(period.get("shortForecast", ""))
            if code > entry["weather_code"]:
                entry["weather_code"] = code

        # Estimate precipitation from probability and short forecast
        pop = period.get("probabilityOfPrecipitation", {}).get("value")
        short = (period.get("shortForecast", "") or "").lower()
        if pop and pop > 50:
            if "heavy" in short:
                daily[date_str]["precipitation_mm"] = max(daily[date_str]["precipitation_mm"], 20.0)
            elif "rain" in short or "shower" in short or "snow" in short:
                daily[date_str]["precipitation_mm"] = max(daily[date_str]["precipitation_mm"], 8.0)
            elif "drizzle" in short or "light" in short:
                daily[date_str]["precipitation_mm"] = max(daily[date_str]["precipitation_mm"], 2.0)

    result = sorted(daily.values(), key=lambda d: d["date"])
    logger.info(
        "NOAA/NWS returned %d forecast days for (%.4f, %.4f)", len(result), latitude, longitude
    )
    return result


def _parse_wind_speed(wind_str: str) -> float:
    """Parse NOAA wind string like '10 to 20 mph' or '15 mph'."""
    import re

    numbers = re.findall(r"(\d+)", wind_str)
    if not numbers:
        return 0.0
    return max(float(n) for n in numbers)


def _noaa_short_to_wmo(short_forecast: str) -> int:
    """Map NOAA shortForecast text to approximate WMO weather code."""
    s = short_forecast.lower()
    if "thunderstorm" in s:
        return 95
    if "heavy rain" in s or "heavy snow" in s:
        return 65
    if "rain" in s or "showers" in s:
        return 63
    if "drizzle" in s or "light rain" in s:
        return 61
    if "snow" in s or "sleet" in s or "ice" in s:
        return 71
    if "fog" in s:
        return 45
    if "overcast" in s or "cloudy" in s:
        return 3
    if "partly" in s:
        return 2
    if "mostly clear" in s or "mainly clear" in s:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Provider 2: Open-Meteo (forecast + historical archive — free, no key)
# ---------------------------------------------------------------------------


async def _fetch_open_meteo_forecast(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch forecast from Open-Meteo API.

    Open-Meteo provides up to 16-day forecasts for free without an API key.
    Also includes hourly humidity data.
    """
    if httpx is None:
        raise WeatherDataUnavailableError("httpx not installed")

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,"
            "weather_code"
        ),
        "hourly": "relative_humidity_2m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=cast(dict[str, Any], params))
        response.raise_for_status()
        raw = response.json()

    daily = raw.get("daily", {})
    dates = daily.get("time", [])

    # Compute daily average humidity from hourly data
    hourly = raw.get("hourly", {})
    hourly_times = hourly.get("time", [])
    hourly_rh = hourly.get("relative_humidity_2m", [])
    daily_humidity: dict[str, float] = {}
    if hourly_times and hourly_rh:
        day_rh: dict[str, list[float]] = defaultdict(list)
        for t, rh in zip(hourly_times, hourly_rh, strict=False):
            if rh is not None:
                day_rh[t[:10]].append(rh)
        for d, vals in day_rh.items():
            daily_humidity[d] = sum(vals) / len(vals) if vals else 50.0

    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    wind = daily.get("wind_speed_10m_max", [])
    codes = daily.get("weather_code", [])

    result: list[dict] = []
    for idx, dt in enumerate(dates):
        result.append(
            {
                "date": dt,
                "temperature_max": temp_max[idx] if idx < len(temp_max) else None,
                "temperature_min": temp_min[idx] if idx < len(temp_min) else None,
                "precipitation_mm": (precip[idx] or 0.0) if idx < len(precip) else 0.0,
                "wind_speed_max": (wind[idx] or 0.0) if idx < len(wind) else 0.0,
                "weather_code": (codes[idx] or 0) if idx < len(codes) else 0,
                "humidity": round(daily_humidity.get(dt, 50.0), 1),
            }
        )

    logger.info(
        "Open-Meteo returned %d forecast days for (%.4f, %.4f)",
        len(result),
        latitude,
        longitude,
    )
    return result


async def fetch_historical_weather(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch historical daily weather from Open-Meteo Archive API.

    The archive API provides data back to 1940 for free. Useful for
    Monte Carlo weather delay modeling — pull 10 years of data to
    build statistical distributions of weather conditions by month.

    Parameters
    ----------
    start_date, end_date:
        ISO date strings, e.g. "2015-01-01", "2024-12-31".

    Returns
    -------
    list of daily weather dicts with same schema as forecast data.
    """
    if httpx is None:
        raise WeatherDataUnavailableError("httpx not installed")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,"
            "weather_code"
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
    }

    client = await _get_weather_http_client()
    response = await client.get(url, params=cast(dict[str, Any], params))
    response.raise_for_status()
    raw = response.json()

    daily = raw.get("daily", {})
    dates = daily.get("time", [])
    h_temp_max = daily.get("temperature_2m_max", [])
    h_temp_min = daily.get("temperature_2m_min", [])
    h_precip = daily.get("precipitation_sum", [])
    h_wind = daily.get("wind_speed_10m_max", [])
    h_codes = daily.get("weather_code", [])

    result: list[dict] = []
    for idx, dt in enumerate(dates):
        result.append(
            {
                "date": dt,
                "temperature_max": h_temp_max[idx] if idx < len(h_temp_max) else None,
                "temperature_min": h_temp_min[idx] if idx < len(h_temp_min) else None,
                "precipitation_mm": (h_precip[idx] or 0.0) if idx < len(h_precip) else 0.0,
                "wind_speed_max": (h_wind[idx] or 0.0) if idx < len(h_wind) else 0.0,
                "weather_code": (h_codes[idx] or 0) if idx < len(h_codes) else 0,
            }
        )

    logger.info(
        "Open-Meteo archive returned %d historical days for (%.4f, %.4f)",
        len(result),
        latitude,
        longitude,
    )
    return result


# ---------------------------------------------------------------------------
# Provider 3: OpenWeatherMap (backup — requires API key)
# ---------------------------------------------------------------------------


async def _fetch_owm_forecast(
    latitude: float,
    longitude: float,
    api_key: str,
) -> list[dict]:
    """Fetch 5-day/3-hour forecast from OpenWeatherMap and aggregate to daily.

    Uses the free-tier /forecast endpoint (limited to 5 days).
    """
    if httpx is None:
        raise WeatherDataUnavailableError("httpx not installed")

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": latitude,
        "lon": longitude,
        "appid": api_key,
        "units": "imperial",  # Fahrenheit, mph
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params=cast(dict[str, Any], params))
        response.raise_for_status()
        raw = response.json()

    # Aggregate 3-hour periods into daily
    daily: dict[str, dict] = {}
    for entry in raw.get("list", []):
        dt_txt = entry.get("dt_txt", "")
        date_str = dt_txt[:10]
        main = entry.get("main", {})
        wind_data = entry.get("wind", {})
        rain = entry.get("rain", {})
        weather_list = entry.get("weather", [{}])

        temp = main.get("temp", 60.0)
        wind_speed = wind_data.get("speed", 0.0)  # mph in imperial
        rain_3h_mm = rain.get("3h", 0.0)
        humidity = main.get("humidity", 50.0)

        # OWM weather condition ID to WMO-ish code
        owm_id = weather_list[0].get("id", 800) if len(weather_list) > 0 else 800
        wmo = _owm_id_to_wmo(owm_id)

        if date_str not in daily:
            daily[date_str] = {
                "date": date_str,
                "temperature_max": temp,
                "temperature_min": temp,
                "precipitation_mm": rain_3h_mm,
                "wind_speed_max": wind_speed,
                "weather_code": wmo,
                "humidity": humidity,
            }
        else:
            d = daily[date_str]
            d["temperature_max"] = max(d["temperature_max"], temp)
            d["temperature_min"] = min(d["temperature_min"], temp)
            d["precipitation_mm"] += rain_3h_mm
            d["wind_speed_max"] = max(d["wind_speed_max"], wind_speed)
            d["humidity"] = max(d["humidity"], humidity)
            if wmo > d["weather_code"]:
                d["weather_code"] = wmo

    # Round precipitation
    for d in daily.values():
        d["precipitation_mm"] = round(d["precipitation_mm"], 1)

    result = sorted(daily.values(), key=lambda d: d["date"])
    logger.info(
        "OpenWeatherMap returned %d forecast days for (%.4f, %.4f)",
        len(result),
        latitude,
        longitude,
    )
    return result


def _owm_id_to_wmo(owm_id: int) -> int:
    """Map OpenWeatherMap condition ID to approximate WMO weather code."""
    if owm_id >= 200 and owm_id < 300:
        return 95  # Thunderstorm
    if owm_id >= 300 and owm_id < 400:
        return 61  # Drizzle
    if owm_id >= 500 and owm_id < 510:
        return 63  # Rain
    if owm_id >= 510 and owm_id < 600:
        return 65  # Heavy rain
    if owm_id >= 600 and owm_id < 700:
        return 71  # Snow
    if owm_id >= 700 and owm_id < 800:
        return 45  # Atmosphere (fog, mist)
    if owm_id == 800:
        return 0  # Clear
    if owm_id == 801:
        return 1  # Few clouds
    if owm_id == 802:
        return 2  # Scattered clouds
    if owm_id >= 803:
        return 3  # Overcast
    return 0


# ---------------------------------------------------------------------------
# Multi-provider fallback chain
# ---------------------------------------------------------------------------


async def get_weather_forecast(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch weather forecast with multi-provider fallback.

    Provider chain:
      1. NOAA/NWS — free, no key, US locations only
      2. Open-Meteo — free, global, up to 16-day forecast
      3. OpenWeatherMap — requires API key, 5-day forecast

    Falls back to next provider on any error. If all fail, returns stale
    cached data (up to 24h old) or raises WeatherDataUnavailableError.

    Parameters
    ----------
    latitude, longitude:
        Site coordinates in decimal degrees.
    start_date, end_date:
        Date range as ISO strings (``YYYY-MM-DD``).

    Returns
    -------
    list of daily weather dicts with: date, temperature_max, temperature_min,
    precipitation_mm, wind_speed_max, weather_code, and optionally humidity.
    """
    from app.config import settings

    cache_ttl = settings.WEATHER_CACHE_TTL

    # Check fresh cache
    cache_key = f"{latitude:.4f},{longitude:.4f},{start_date},{end_date}"
    cached = _weather_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < cache_ttl:
        logger.debug("Weather cache hit for %s", cache_key)
        return cached[1]

    errors: list[str] = []

    # Provider 1: NOAA/NWS
    try:
        data = await _fetch_noaa_forecast(latitude, longitude)
        if data:
            # Filter to requested date range
            data = _filter_date_range(data, start_date, end_date)
            if data:
                _weather_cache[cache_key] = (time.monotonic(), data)
                return data
            else:
                errors.append("NOAA: no data in requested date range")
    except Exception as exc:
        errors.append(f"NOAA: {exc}")
        logger.warning("NOAA/NWS forecast failed: %s", exc)

    # Provider 2: Open-Meteo
    try:
        data = await _fetch_open_meteo_forecast(latitude, longitude, start_date, end_date)
        if data:
            _weather_cache[cache_key] = (time.monotonic(), data)
            return data
    except Exception as exc:
        errors.append(f"Open-Meteo: {exc}")
        logger.warning("Open-Meteo forecast failed: %s", exc)

    # Provider 3: OpenWeatherMap (backup)
    owm_key = settings.OPENWEATHERMAP_API_KEY
    if owm_key:
        try:
            data = await _fetch_owm_forecast(latitude, longitude, owm_key)
            if data:
                data = _filter_date_range(data, start_date, end_date)
                if data:
                    _weather_cache[cache_key] = (time.monotonic(), data)
                    return data
                else:
                    errors.append("OWM: no data in requested date range")
        except Exception as exc:
            errors.append(f"OWM: {exc}")
            logger.warning("OpenWeatherMap forecast failed: %s", exc)
    else:
        errors.append("OWM: no API key configured")

    # All providers failed — try stale cache
    if cached and (time.monotonic() - cached[0]) < _STALE_CACHE_MAX_AGE:
        logger.warning(
            "All weather providers failed; returning stale cache (age=%.0fs). Errors: %s",
            time.monotonic() - cached[0],
            "; ".join(errors),
        )
        return cached[1]

    raise WeatherDataUnavailableError(f"All weather providers failed: {'; '.join(errors)}")


def _filter_date_range(data: list[dict], start_date: str, end_date: str) -> list[dict]:
    """Filter weather data to only include dates in [start_date, end_date]."""
    return [d for d in data if start_date <= d["date"] <= end_date]


# ---------------------------------------------------------------------------
# Weather impact analysis (existing interface preserved)
# ---------------------------------------------------------------------------


async def analyze_weather_impact(
    activities: list[dict],
    weather_data: list[dict],
    activity_weather_sensitivity: dict | None = None,
) -> dict:
    """Analyze weather impact on schedule activities.

    Parameters
    ----------
    activities:
        Schedule activities. Each should have ``id``, ``name``,
        ``activity_type`` (maps to sensitivity table), ``start_date``,
        ``end_date``.
    weather_data:
        Daily weather data as returned by ``get_weather_forecast``.
    activity_weather_sensitivity:
        Optional override for sensitivity thresholds.

    Returns
    -------
    dict with impact_days, weather_events, adjusted_end_date, risk_level,
    monthly_breakdown, construction_impacts.
    """
    import contextlib

    sensitivity = {**DEFAULT_SENSITIVITY}
    if activity_weather_sensitivity:
        sensitivity.update(activity_weather_sensitivity)

    weather_by_date: dict[str, dict] = {w["date"]: w for w in weather_data}

    total_impact_days = 0
    weather_events: list[dict] = []
    monthly_impact: dict[str, int] = defaultdict(int)
    impacted_dates: set[str] = set()
    construction_impacts: list[dict] = []

    for activity in activities:
        act_type = activity.get("activity_type", "general")
        thresholds = sensitivity.get(act_type, sensitivity["general"])
        act_start = activity.get("start_date", "")
        act_end = activity.get("end_date", "")

        if not act_start or not act_end:
            continue

        try:
            start_dt = datetime.strptime(act_start, "%Y-%m-%d")
            end_dt = datetime.strptime(act_end, "%Y-%m-%d")
        except ValueError:
            logger.warning("Skipping activity %s: invalid date format", activity.get("id"))
            continue

        current = start_dt
        while current <= end_dt:
            date_str = current.strftime("%Y-%m-%d")
            weather = weather_by_date.get(date_str)
            if weather is None:
                current += timedelta(days=1)
                continue

            violations: list[str] = []
            severity = "low"

            # Check minimum temperature
            min_temp_threshold = thresholds.get("min_temp")
            if min_temp_threshold is not None and weather["temperature_min"] < min_temp_threshold:
                violations.append(
                    f"Low temperature: {weather['temperature_min']}F (min {min_temp_threshold}F)"
                )
                severity = "medium"

            # Check maximum temperature
            max_temp_threshold = thresholds.get("max_temp")
            if max_temp_threshold is not None and weather["temperature_max"] > max_temp_threshold:
                violations.append(
                    f"High temperature: {weather['temperature_max']}F (max {max_temp_threshold}F)"
                )
                severity = "medium"

            # Check precipitation (convert mm to inches for comparison)
            max_precip_threshold = thresholds.get("max_precip")
            if max_precip_threshold is not None:
                precip_inches = weather["precipitation_mm"] / 25.4
                if precip_inches > max_precip_threshold:
                    violations.append(
                        f"Precipitation: {precip_inches:.2f}in (max {max_precip_threshold}in)"
                    )
                    severity = "high" if precip_inches > 0.5 else "medium"

            # Check wind speed
            max_wind_threshold = thresholds.get("max_wind")
            if max_wind_threshold is not None and weather["wind_speed_max"] > max_wind_threshold:
                violations.append(
                    f"High wind: {weather['wind_speed_max']}mph (max {max_wind_threshold}mph)"
                )
                severity = "high" if weather["wind_speed_max"] > 40 else severity

            if violations:
                if date_str not in impacted_dates:
                    total_impact_days += 1
                    impacted_dates.add(date_str)
                    month_key = date_str[:7]
                    monthly_impact[month_key] += 1

                weather_events.append(
                    {
                        "date": date_str,
                        "type": ", ".join(violations),
                        "severity": severity,
                        "affected_activities": [str(activity.get("id", ""))],
                    }
                )

            # Run construction-specific impact assessment
            impact_fn = IMPACT_FUNCTIONS.get(act_type)
            if impact_fn:
                impact = impact_fn(weather)
                if not impact.allowed:
                    construction_impacts.append(
                        {
                            "date": date_str,
                            "activity_id": str(activity.get("id", "")),
                            "activity_type": act_type,
                            "risk_level": impact.risk_level.value,
                            "reasons": impact.reasons,
                            "recommendations": impact.recommendations,
                        }
                    )

            current += timedelta(days=1)

    # Consolidate events on the same date
    consolidated: dict[str, dict] = {}
    for event in weather_events:
        key = event["date"]
        if key in consolidated:
            consolidated[key]["affected_activities"].extend(event["affected_activities"])
            sev_order = {"low": 0, "medium": 1, "high": 2}
            if sev_order.get(event["severity"], 0) > sev_order.get(
                consolidated[key]["severity"], 0
            ):
                consolidated[key]["severity"] = event["severity"]
                consolidated[key]["type"] = event["type"]
        else:
            consolidated[key] = event
    weather_events = sorted(consolidated.values(), key=lambda e: e["date"])

    # Determine adjusted end date
    if activities and weather_data:
        end_dates = []
        for act in activities:
            ed = act.get("end_date", "")
            if ed:
                with contextlib.suppress(ValueError):
                    end_dates.append(datetime.strptime(ed, "%Y-%m-%d"))
        if end_dates:
            latest_end = max(end_dates)
            adjusted_end = latest_end + timedelta(days=total_impact_days)
            adjusted_end_date = adjusted_end.strftime("%Y-%m-%d")
        else:
            adjusted_end_date = ""
    else:
        adjusted_end_date = ""

    # Risk level
    if total_impact_days == 0 or total_impact_days <= 5:
        risk_level = "low"
    elif total_impact_days <= 15:
        risk_level = "medium"
    else:
        risk_level = "high"

    logger.info(
        "Weather impact analysis: %d impact days, risk=%s, events=%d",
        total_impact_days,
        risk_level,
        len(weather_events),
    )

    return {
        "impact_days": total_impact_days,
        "weather_events": weather_events,
        "adjusted_end_date": adjusted_end_date,
        "risk_level": risk_level,
        "monthly_breakdown": dict(monthly_impact),
        "construction_impacts": construction_impacts,
    }


# ---------------------------------------------------------------------------
# Scheduling integration: get_weather_impact
# ---------------------------------------------------------------------------


async def get_weather_impact(
    location: str,
    start_date: Any,
    end_date: Any,
    activities: list[dict] | None = None,
) -> dict:
    """High-level entry point used by the scheduling API endpoint.

    Geocodes the location string to lat/lon, fetches weather, and runs
    impact analysis against provided activities.

    Parameters
    ----------
    location:
        Location string. Supports "lat,lon" format or will attempt geocoding.
    start_date, end_date:
        date or str objects for the analysis period.
    activities:
        Optional list of schedule activities to analyze.

    Returns
    -------
    dict with impact_days, weather_events, adjusted_end_date, risk_level.
    """
    lat, lon = _parse_location(location)

    start_str = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)
    end_str = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)

    weather_data = await get_weather_forecast(lat, lon, start_str, end_str)

    if activities:
        return await analyze_weather_impact(activities, weather_data)

    # No activities — just return weather-based overall assessment
    impact_days = 0
    events: list[dict] = []
    for day in weather_data:
        score = weather_impact_score(day)
        if not score.allowed:
            impact_days += 1
            events.append(
                {
                    "date": day["date"],
                    "type": "; ".join(score.reasons),
                    "severity": "high" if score.risk_level == RiskLevel.RED else "medium",
                    "affected_activities": [],
                }
            )

    if impact_days == 0 or impact_days <= 5:
        risk_level = "low"
    elif impact_days <= 15:
        risk_level = "medium"
    else:
        risk_level = "high"

    # Compute adjusted end date
    from datetime import date as date_type

    if isinstance(end_date, date_type):
        adjusted = end_date + timedelta(days=impact_days)
        adjusted_end_date = adjusted.isoformat()
    else:
        adjusted_end_date = end_str

    return {
        "impact_days": impact_days,
        "weather_events": events,
        "adjusted_end_date": adjusted_end_date,
        "risk_level": risk_level,
    }


def _parse_location(location: str) -> tuple[float, float]:
    """Parse a location string into (latitude, longitude).

    Supports:
    - "lat,lon" format (e.g., "40.7128,-74.0060")
    - Named locations with a built-in lookup table of major US cities
    """
    # Try lat,lon parsing first
    parts = location.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass

    # Simple geocoding fallback for common US construction hubs
    CITY_COORDS: dict[str, tuple[float, float]] = {
        "new york": (40.7128, -74.0060),
        "nyc": (40.7128, -74.0060),
        "los angeles": (34.0522, -118.2437),
        "la": (34.0522, -118.2437),
        "chicago": (41.8781, -87.6298),
        "houston": (29.7604, -95.3698),
        "phoenix": (33.4484, -112.0740),
        "philadelphia": (39.9526, -75.1652),
        "san antonio": (29.4241, -98.4936),
        "san diego": (32.7157, -117.1611),
        "dallas": (32.7767, -96.7970),
        "austin": (30.2672, -97.7431),
        "denver": (39.7392, -104.9903),
        "seattle": (47.6062, -122.3321),
        "miami": (25.7617, -80.1918),
        "atlanta": (33.7490, -84.3880),
        "boston": (42.3601, -71.0589),
        "nashville": (36.1627, -86.7816),
        "portland": (45.5152, -122.6784),
        "las vegas": (36.1699, -115.1398),
        "san francisco": (37.7749, -122.4194),
        "washington": (38.9072, -77.0369),
        "dc": (38.9072, -77.0369),
        "detroit": (42.3314, -83.0458),
        "minneapolis": (44.9778, -93.2650),
    }

    loc_lower = location.lower().strip()
    if loc_lower in CITY_COORDS:
        return CITY_COORDS[loc_lower]

    # Partial match
    for city, coords in CITY_COORDS.items():
        if city in loc_lower or loc_lower in city:
            return coords

    # Default to NYC if unknown
    logger.warning("Could not geocode location '%s'; defaulting to NYC", location)
    return (40.7128, -74.0060)


# ---------------------------------------------------------------------------
# Historical weather statistics for Monte Carlo
# ---------------------------------------------------------------------------


async def get_monthly_weather_stats(
    latitude: float,
    longitude: float,
    years: int = 10,
) -> dict[int, dict]:
    """Compute monthly weather statistics from historical data.

    Pulls ``years`` of historical data from Open-Meteo archive and computes
    per-month statistics useful for Monte Carlo weather delay modeling.

    Returns
    -------
    dict keyed by month number (1-12), each containing:
        avg_temp_max, avg_temp_min, avg_precip_mm, avg_wind_max,
        pct_rain_days (days with > 1mm), pct_severe_days (days with
        weather_code >= 61), sample_days.
    """
    today = datetime.now(UTC)
    end = today - timedelta(days=7)  # archive may lag a few days
    start = end - timedelta(days=365 * years)

    data = await fetch_historical_weather(
        latitude,
        longitude,
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )

    # Aggregate by month
    monthly: dict[int, list[dict]] = defaultdict(list)
    for day in data:
        month = int(day["date"][5:7])
        monthly[month].append(day)

    stats: dict[int, dict] = {}
    for month in range(1, 13):
        days = monthly.get(month, [])
        if not days:
            stats[month] = {
                "avg_temp_max": 0,
                "avg_temp_min": 0,
                "avg_precip_mm": 0,
                "avg_wind_max": 0,
                "pct_rain_days": 0,
                "pct_severe_days": 0,
                "sample_days": 0,
            }
            continue

        n = len(days)
        stats[month] = {
            "avg_temp_max": round(sum(d.get("temperature_max", 0) or 0 for d in days) / n, 1),
            "avg_temp_min": round(sum(d.get("temperature_min", 0) or 0 for d in days) / n, 1),
            "avg_precip_mm": round(sum(d.get("precipitation_mm", 0) or 0 for d in days) / n, 1),
            "avg_wind_max": round(sum(d.get("wind_speed_max", 0) or 0 for d in days) / n, 1),
            "pct_rain_days": round(
                sum(1 for d in days if (d.get("precipitation_mm", 0) or 0) > 1.0) / n * 100, 1
            ),
            "pct_severe_days": round(
                sum(1 for d in days if (d.get("weather_code", 0) or 0) >= 61) / n * 100, 1
            ),
            "sample_days": n,
        }

    logger.info(
        "Computed monthly weather stats from %d years of data for (%.4f, %.4f)",
        years,
        latitude,
        longitude,
    )
    return stats
