"""Tests for weather → activity impact rules.

Pure functions, no DB or external API. Pin every threshold so a
refactor can't quietly green-light a concrete pour at 100°F or a
crane operation at 40 mph wind.
"""

from __future__ import annotations

from app.services.scheduling.weather_service import (
    RiskLevel,
    WeatherDataUnavailableError,
    can_excavate,
    can_operate_crane,
    can_paint_exterior,
    can_pour_concrete,
    weather_impact_score,
)


def _w(**kwargs):
    """Build a weather dict with sane defaults; override per-test."""
    base = {
        "temperature_min": 60.0,
        "temperature_max": 70.0,
        "precipitation_mm": 0.0,
        "wind_speed_max": 0.0,
        "humidity": 50.0,
        "weather_code": 0,
    }
    base.update(kwargs)
    return base


# =========================================================================
# can_pour_concrete (ACI 306R / 305R thresholds)
# =========================================================================


def test_concrete_perfect_conditions_green():
    impact = can_pour_concrete(_w(temperature_min=55, temperature_max=75))
    assert impact.activity == "concrete_pour"
    assert impact.allowed is True
    assert impact.risk_level == RiskLevel.GREEN


def test_concrete_below_freezing_red():
    impact = can_pour_concrete(_w(temperature_min=30, temperature_max=45))
    assert impact.allowed is False
    assert impact.risk_level == RiskLevel.RED
    assert any("freezing" in r.lower() for r in impact.reasons)


def test_concrete_below_40_yellow_with_recommendation():
    impact = can_pour_concrete(_w(temperature_min=37, temperature_max=55))
    assert impact.allowed is True
    assert impact.risk_level == RiskLevel.YELLOW
    assert any(
        "hot water" in rec.lower() or "blanket" in rec.lower() for rec in impact.recommendations
    )


def test_concrete_above_95_red_flash_set():
    """ACI 305R hot-weather: above 95°F triggers flash-set risk."""
    impact = can_pour_concrete(_w(temperature_min=80, temperature_max=98))
    assert impact.allowed is False
    assert impact.risk_level == RiskLevel.RED
    assert any("flash set" in r.lower() for r in impact.reasons)


def test_concrete_heavy_rain_blocks_pour():
    """Wet concrete + rain = surface washout — must stop the pour."""
    impact = can_pour_concrete(_w(precipitation_mm=10))  # 0.4 in
    assert impact.allowed is False
    assert impact.risk_level == RiskLevel.RED


def test_concrete_high_wind_blocks_pour():
    impact = can_pour_concrete(_w(wind_speed_max=30))
    assert impact.allowed is False
    assert impact.risk_level == RiskLevel.RED


def test_concrete_low_humidity_with_high_temp_yellow():
    """Low humidity + high temp = rapid evaporation. Not a stop, but a
    yellow flag with a specific mitigation."""
    impact = can_pour_concrete(_w(temperature_max=88, humidity=15))
    assert impact.risk_level == RiskLevel.YELLOW
    assert any("evaporation retarder" in rec.lower() for rec in impact.recommendations)


# =========================================================================
# can_operate_crane (OSHA 1926.1431)
# =========================================================================


def test_crane_normal_wind_green():
    impact = can_operate_crane(_w(wind_speed_max=10))
    assert impact.allowed is True
    assert impact.risk_level == RiskLevel.GREEN


def test_crane_wind_above_35_must_stand_down():
    impact = can_operate_crane(_w(wind_speed_max=40))
    assert impact.allowed is False
    assert any("cease" in r.lower() for r in impact.reasons)


def test_crane_wind_above_30_no_personnel_hoist():
    """30-35 mph: ops continue but no personnel hoisting."""
    impact = can_operate_crane(_w(wind_speed_max=32))
    assert impact.allowed is False  # personnel-hoist restriction is a stop
    assert any("personnel hoisting" in r.lower() for r in impact.reasons)


def test_crane_wind_20_30_yellow():
    impact = can_operate_crane(_w(wind_speed_max=25))
    assert impact.risk_level == RiskLevel.YELLOW
    assert impact.allowed is True


def test_crane_thunderstorm_must_cease():
    """WMO weather code ≥ 95 → thunderstorm → lightning → cease ops."""
    impact = can_operate_crane(_w(weather_code=95))
    assert impact.allowed is False
    assert any("thunderstorm" in r.lower() or "lightning" in r.lower() for r in impact.reasons)


def test_crane_heavy_rain_yellow_visibility():
    """0.5+ in precip → reduced visibility → yellow."""
    impact = can_operate_crane(_w(precipitation_mm=15))  # ~0.6 in
    assert impact.risk_level == RiskLevel.YELLOW


# =========================================================================
# can_excavate
# =========================================================================


def test_excavate_normal_conditions_green():
    impact = can_excavate(_w())
    assert impact.allowed is True


def test_excavate_after_heavy_rain_yellow_or_red():
    """Heavy rain destabilises trench walls — should at minimum yellow."""
    impact = can_excavate(_w(precipitation_mm=30))
    assert impact.risk_level in (RiskLevel.YELLOW, RiskLevel.RED)


# =========================================================================
# can_paint_exterior
# =========================================================================


def test_paint_below_50_red():
    """Most exterior paints fail to cure below 50°F."""
    impact = can_paint_exterior(_w(temperature_min=40, temperature_max=55))
    assert impact.risk_level in (RiskLevel.YELLOW, RiskLevel.RED)


def test_paint_with_imminent_rain_blocks():
    impact = can_paint_exterior(_w(precipitation_mm=5))
    assert impact.risk_level in (RiskLevel.YELLOW, RiskLevel.RED)


# =========================================================================
# weather_impact_score (overall summary)
# =========================================================================


def test_overall_impact_green_for_perfect_day():
    impact = weather_impact_score(_w(temperature_min=55, temperature_max=70, wind_speed_max=5))
    assert impact.risk_level == RiskLevel.GREEN


def test_overall_impact_red_for_severe_storm():
    """Severe conditions cross the 60-point cap → RED. Storm + heavy
    precip + extreme wind + extreme temp adds to >60 across all four
    weighted factors."""
    impact = weather_impact_score(
        _w(
            weather_code=95,  # storm = 20
            wind_speed_max=45,  # > 40 = 20
            precipitation_mm=30,  # > 1.0in = 30
            temperature_min=10,
            temperature_max=15,  # avg 12 → 30
        )
    )
    assert impact.risk_level == RiskLevel.RED


def test_overall_impact_yellow_for_moderate_disruption():
    """Storm + 30 mph wind without extreme temps lands in YELLOW
    territory (≥30, <60). Pin the threshold."""
    impact = weather_impact_score(_w(weather_code=95, wind_speed_max=35))
    assert impact.risk_level == RiskLevel.YELLOW


# =========================================================================
# Exception class
# =========================================================================


def test_weather_data_unavailable_is_exception():
    assert issubclass(WeatherDataUnavailableError, Exception)


def test_weather_data_unavailable_carries_message():
    e = WeatherDataUnavailableError("OWM rate limit hit")
    assert str(e) == "OWM rate limit hit"


# =========================================================================
# RiskLevel ordering (used internally to combine impacts)
# =========================================================================


def test_risk_level_enum_order():
    """The list order is the elevation order — RED is the worst, used
    as the cap for ``allowed``. Pin it so a refactor can't reorder."""
    levels = list(RiskLevel)
    assert levels[0] == RiskLevel.GREEN
    assert levels[-1] == RiskLevel.RED
