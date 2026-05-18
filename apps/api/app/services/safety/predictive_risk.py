"""Predictive safety risk engine.

Uses OSHA enforcement data, weather forecasts, project characteristics,
and scheduled activities to calculate daily risk scores BEFORE incidents
happen.  Generates pre-task safety briefings for morning huddles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RiskCategory:
    """A single hazard-category risk assessment."""

    name: str
    score: int  # 0-100
    label: str  # low / moderate / elevated / high / critical
    factors: list[str] = field(default_factory=list)
    mitigations: list[str] = field(default_factory=list)


@dataclass
class DailyRiskResult:
    """Complete daily risk assessment for a project."""

    project_id: str
    score_date: date
    overall_score: int
    category_scores: dict[str, int]
    categories: list[RiskCategory]
    top_risks: list[dict]
    recommended_mitigations: list[str]
    weather_factors: dict
    schedule_factors: dict
    project_factors: dict
    osha_factors: dict


# ---------------------------------------------------------------------------
# OSHA "Focus Four" + heat illness hazard categories
# ---------------------------------------------------------------------------

# OSHA standards associated with each hazard category
_HAZARD_STANDARDS: dict[str, list[str]] = {
    "fall_risk": [
        "1926.501",
        "1926.502",
        "1926.503",  # Fall protection
        "1926.450",
        "1926.451",
        "1926.452",
        "1926.453",
        "1926.454",  # Scaffolding
        "1926.1050",
        "1926.1051",
        "1926.1052",
        "1926.1053",
        "1926.1060",  # Ladders
        "1926.750",
        "1926.760",
        "1926.761",  # Steel erection
    ],
    "struck_by_risk": [
        "1926.550",
        "1926.551",
        "1926.552",
        "1926.553",
        "1926.556",  # Cranes
        "1926.250",
        "1926.251",
        "1926.252",  # Materials handling
        "1926.600",
        "1926.601",
        "1926.602",  # Motor vehicles
        "1926.200",
        "1926.201",
        "1926.202",  # Signs/barricades
    ],
    "electrical_risk": [
        "1926.400",
        "1926.402",
        "1926.403",
        "1926.404",
        "1926.405",
        "1926.416",
        "1926.417",
        "1926.431",
        "1926.432",
        "1926.449",
    ],
    "excavation_risk": [
        "1926.650",
        "1926.651",
        "1926.652",  # Excavation
    ],
    "heat_illness_risk": [
        "1926.21",  # Safety training and education
        "1926.95",  # PPE (head protection against heat)
    ],
}

# Activity keyword → hazard mapping
_ACTIVITY_HAZARD_MAP: dict[str, list[str]] = {
    "excavat": ["excavation_risk", "struck_by_risk"],
    "trench": ["excavation_risk"],
    "foundation": ["excavation_risk", "fall_risk"],
    "steel": ["fall_risk", "struck_by_risk"],
    "iron": ["fall_risk", "struck_by_risk"],
    "erect": ["fall_risk", "struck_by_risk"],
    "roof": ["fall_risk", "heat_illness_risk"],
    "scaffold": ["fall_risk"],
    "ladder": ["fall_risk"],
    "crane": ["struck_by_risk"],
    "hoist": ["struck_by_risk"],
    "rigging": ["struck_by_risk"],
    "demolit": ["fall_risk", "struck_by_risk"],
    "electr": ["electrical_risk"],
    "wiring": ["electrical_risk"],
    "panel": ["electrical_risk"],
    "conduit": ["electrical_risk"],
    "concrete": ["struck_by_risk"],
    "form": ["fall_risk"],
    "pour": ["struck_by_risk"],
    "paint": ["fall_risk", "heat_illness_risk"],
    "weld": ["electrical_risk"],
    "masonry": ["fall_risk", "struck_by_risk"],
    "curtain wall": ["fall_risk"],
    "glazing": ["fall_risk"],
    "framing": ["fall_risk"],
    "deck": ["fall_risk"],
    "siding": ["fall_risk"],
    "plumb": ["excavation_risk"],
    "pipe": ["struck_by_risk"],
    "hvac": ["electrical_risk"],
    "mechanical": ["struck_by_risk"],
    "grade": ["excavation_risk", "struck_by_risk"],
    "backfill": ["excavation_risk", "struck_by_risk"],
}

# NAICS sub-code to project type description
_NAICS_PROJECT_TYPE: dict[str, str] = {
    "2361": "residential",
    "2362": "commercial",
    "2371": "utility",
    "2372": "land_subdivision",
    "2373": "highway_street",
    "2379": "heavy_civil",
    "2381": "foundation_structure",
    "2382": "mechanical_electrical",
    "2383": "finishing",
    "2389": "specialty",
}

# Category weights for the overall score
_CATEGORY_WEIGHTS: dict[str, float] = {
    "fall_risk": 0.30,
    "struck_by_risk": 0.25,
    "excavation_risk": 0.20,
    "electrical_risk": 0.15,
    "heat_illness_risk": 0.10,
}

# Invariant: weights must sum to 1.0 to produce a valid 0-100 composite score.
_weight_sum = sum(_CATEGORY_WEIGHTS.values())
assert abs(_weight_sum - 1.0) < 1e-9, f"_CATEGORY_WEIGHTS must sum to 1.0, got {_weight_sum}"


def _score_label(score: int) -> str:
    """Convert 0-100 score to human label."""
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "elevated"
    if score >= 20:
        return "moderate"
    return "low"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> int:
    """Clamp and round to int."""
    return int(max(lo, min(hi, round(value))))


# ---------------------------------------------------------------------------
# Heat index calculation (NWS formula)
# ---------------------------------------------------------------------------


def _heat_index(temp_f: float, rh: float) -> float:
    """Calculate heat index in Fahrenheit (NWS regression)."""
    if temp_f < 80:
        return temp_f
    hi = (
        -42.379
        + 2.04901523 * temp_f
        + 10.14333127 * rh
        - 0.22475541 * temp_f * rh
        - 0.00683783 * temp_f**2
        - 0.05481717 * rh**2
        + 0.00122874 * temp_f**2 * rh
        + 0.00085282 * temp_f * rh**2
        - 0.00000199 * temp_f**2 * rh**2
    )
    return hi


# ---------------------------------------------------------------------------
# PredictiveRiskEngine
# ---------------------------------------------------------------------------


class PredictiveRiskEngine:
    """Calculate daily safety risk scores for construction projects."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def calculate_daily_risk_score(
        self,
        db: AsyncSession,
        project_id: str,
        project: dict,
        weather: list[dict] | None = None,
        today_activities: list[dict] | None = None,
        daily_log: dict | None = None,
    ) -> DailyRiskResult:
        """Generate a 0-100 risk score broken down by hazard category.

        Parameters
        ----------
        db : AsyncSession
            Database session for OSHA queries.
        project_id : str
            UUID of the project.
        project : dict
            Project data (type, address, state, start_date, etc.).
        weather : list[dict] | None
            Today's weather forecast data (from get_weather_forecast).
        today_activities : list[dict] | None
            Schedule activities active today.
        daily_log : dict | None
            Today's daily log (crew_count, manpower_by_trade).
        """
        today = date.today()
        features = self._gather_features(
            project,
            weather,
            today_activities,
            daily_log,
            today,
        )

        # Query OSHA enforcement data for this project type/location
        osha_data = await self._query_osha_patterns(
            db,
            state=features.get("state"),
            naics_prefix=features.get("naics_prefix"),
        )

        # Score each hazard category
        categories = [
            self._score_fall_risk(features, osha_data),
            self._score_struck_by_risk(features, osha_data),
            self._score_electrical_risk(features, osha_data),
            self._score_excavation_risk(features, osha_data),
            self._score_heat_risk(features, osha_data),
        ]

        # Calculate weighted overall score.
        # Individual category scores are already clamped to [0, 100] by _clamp()
        # in each _score_*() method.  We clamp again here defensively so that any
        # future scoring method that forgets to call _clamp() cannot produce an
        # out-of-range overall score.
        category_scores = {c.name: _clamp(c.score) for c in categories}
        overall = sum(category_scores[name] * weight for name, weight in _CATEGORY_WEIGHTS.items())
        overall_score = _clamp(overall)

        # Sort to get top risks
        ranked = sorted(categories, key=lambda c: c.score, reverse=True)
        top_risks = [
            {
                "category": c.name,
                "score": c.score,
                "label": c.label,
                "factors": c.factors,
            }
            for c in ranked[:3]
            if c.score >= 20
        ]

        # Aggregate mitigations from top risks
        mitigations: list[str] = []
        seen: set[str] = set()
        for c in ranked:
            if c.score < 20:
                continue
            for m in c.mitigations:
                if m not in seen:
                    mitigations.append(m)
                    seen.add(m)

        # Build factor dicts for storage
        weather_factors = self._extract_weather_factors(features)
        schedule_factors = self._extract_schedule_factors(features, today_activities)
        project_factors = self._extract_project_factors(features)
        osha_factors_dict = {
            "total_inspections": osha_data.get("total_inspections", 0),
            "total_violations": osha_data.get("total_violations", 0),
            "top_standards": osha_data.get("top_standards", [])[:5],
            "violation_rate": osha_data.get("violation_rate", 0.0),
        }

        return DailyRiskResult(
            project_id=project_id,
            score_date=today,
            overall_score=overall_score,
            category_scores=category_scores,
            categories=categories,
            top_risks=top_risks,
            recommended_mitigations=mitigations,
            weather_factors=weather_factors,
            schedule_factors=schedule_factors,
            project_factors=project_factors,
            osha_factors=osha_factors_dict,
        )

    async def generate_safety_briefing(
        self,
        risk_result: DailyRiskResult,
        project: dict,
        weather: list[dict] | None = None,
        today_activities: list[dict] | None = None,
    ) -> str:
        """Generate a natural-language safety briefing for the morning huddle.

        Uses the LLM gateway to create a practical, specific briefing.
        Falls back to a template-based briefing if LLM is unavailable.
        """
        weather_summary = "No forecast available"
        if weather:
            w = weather[0]
            temp_hi = w.get("temperature_max", "N/A")
            temp_lo = w.get("temperature_min", "N/A")
            precip = w.get("precipitation_mm", 0)
            wind = w.get("wind_speed_max", 0)
            weather_summary = (
                f"High {temp_hi}°F / Low {temp_lo}°F, precip {precip}mm, wind {wind} mph"
            )

        activity_list = "No scheduled activities loaded"
        if today_activities:
            names = [
                sanitize_for_prompt(a.get("name", "Unknown"), max_length=200)
                for a in today_activities[:10]
            ]
            activity_list = ", ".join(names)

        top_risk_text = "No elevated risks"
        if risk_result.top_risks:
            lines = []
            for r in risk_result.top_risks:
                factors_str = "; ".join(r["factors"][:2]) if r["factors"] else ""
                lines.append(
                    f"- {r['category'].replace('_', ' ').title()}: "
                    f"{r['label']} ({r['score']}/100) — {factors_str}"
                )
            top_risk_text = "\n".join(lines)

        mitigation_text = (
            "\n".join(f"- {m}" for m in risk_result.recommended_mitigations[:5])
            or "No specific mitigations required"
        )

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "You are a construction safety manager writing a pre-task safety "
                    "briefing for a morning huddle. Be practical, specific, and brief. "
                    "No generic platitudes. Reference specific weather conditions and "
                    "activities. Format for spoken delivery in under 3 minutes."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Generate today's safety briefing.\n\n"
                    f"Project: <user_data>"
                    f"{sanitize_for_prompt(project.get('name', 'Unknown'), max_length=200)}"
                    f"</user_data>\n"
                    f"Date: {risk_result.score_date.isoformat()}\n"
                    f"Overall Risk Score: {risk_result.overall_score}/100 "
                    f"({_score_label(risk_result.overall_score)})\n\n"
                    f"Weather: {weather_summary}\n\n"
                    f"Today's Activities: <user_data>{activity_list}</user_data>\n\n"
                    f"Risk Assessment:\n{top_risk_text}\n\n"
                    f"Required Mitigations:\n{mitigation_text}\n\n"
                    f"Format the briefing as:\n"
                    f"1. Weather awareness (1 sentence)\n"
                    f"2. Today's high-risk activities and specific precautions (2-3 points)\n"
                    f"3. Required PPE reminders specific to today's work\n"
                    f"4. Emergency contacts reminder"
                ),
            },
        ]

        try:
            from app.services.reliability.llm_gateway import get_llm_gateway

            gateway = await get_llm_gateway()
            result = await gateway.complete(
                messages=prompt_messages,
                agent_name="safety_briefing",
            )
            return result.get(
                "content",
                self._template_briefing(
                    risk_result,
                    project,
                    weather_summary,
                    activity_list,
                ),
            )
        except Exception as exc:
            logger.warning("LLM briefing generation failed: %s; using template", exc)
            return self._template_briefing(
                risk_result,
                project,
                weather_summary,
                activity_list,
            )

    # ------------------------------------------------------------------
    # Feature gathering
    # ------------------------------------------------------------------

    def _gather_features(
        self,
        project: dict,
        weather: list[dict] | None,
        activities: list[dict] | None,
        daily_log: dict | None,
        today: date,
    ) -> dict[str, Any]:
        """Collect all features used for risk scoring."""
        features: dict[str, Any] = {}

        # Project characteristics
        features["project_type"] = project.get("type", "commercial")
        state = self._extract_state(project)
        features["state"] = state
        features["month"] = today.month
        features["day_of_week"] = today.weekday()  # 0=Mon, 6=Sun

        # Project age (months since start)
        start_date = project.get("start_date")
        if start_date:
            if isinstance(start_date, str):
                try:
                    from datetime import datetime

                    start_date = datetime.fromisoformat(start_date).date()
                except (ValueError, TypeError):
                    start_date = None
            if start_date and isinstance(start_date, date):
                features["project_age_months"] = max(0, (today - start_date).days / 30)
            else:
                features["project_age_months"] = 6.0
        else:
            features["project_age_months"] = 6.0

        # NAICS prefix for OSHA queries
        naics = project.get("naics_code", "")
        if naics and len(naics) >= 4:
            features["naics_prefix"] = naics[:4]
        else:
            ptype = features["project_type"] or "commercial"
            features["naics_prefix"] = {
                "residential": "2361",
                "commercial": "2362",
                "infrastructure": "237",
                "industrial": "2362",
            }.get(ptype, "23")

        # Worker count
        if daily_log:
            features["num_workers"] = daily_log.get("crew_count", 0) or 0
        else:
            features["num_workers"] = 0

        # Weather features
        if weather and len(weather) > 0:
            w = weather[0]
            features["temp_high"] = w.get("temperature_max", 72)
            features["temp_low"] = w.get("temperature_min", 55)
            features["wind_speed"] = w.get("wind_speed_max", 5)
            features["precipitation_mm"] = w.get("precipitation_mm", 0)
            features["humidity"] = w.get("humidity", 50)
            features["weather_code"] = w.get("weather_code", 0)
            features["heat_index"] = _heat_index(
                features["temp_high"],
                features["humidity"],
            )
        else:
            features["temp_high"] = 72
            features["temp_low"] = 55
            features["wind_speed"] = 5
            features["precipitation_mm"] = 0
            features["humidity"] = 50
            features["heat_index"] = 72

        # Activity-based hazard flags
        activity_names_lower = ""
        if activities:
            activity_names_lower = " ".join(a.get("name", "").lower() for a in activities)

        for keyword, hazards in _ACTIVITY_HAZARD_MAP.items():
            key = f"has_{keyword}"
            features[key] = keyword in activity_names_lower

        # Aggregate: which hazard categories are active today?
        active_hazards: set[str] = set()
        for keyword, hazards in _ACTIVITY_HAZARD_MAP.items():
            if features.get(f"has_{keyword}", False):
                active_hazards.update(hazards)
        features["active_hazards"] = active_hazards

        return features

    def _extract_state(self, project: dict) -> str | None:
        """Extract 2-letter state code from project address."""
        address = project.get("address", "") or ""
        # Try to find a 2-letter state code in the address
        import re

        match = re.search(r"\b([A-Z]{2})\b", address)
        if match:
            code = match.group(1)
            if code in _US_STATES:
                return code
        # Fall through — check for a state field directly
        return project.get("state") or project.get("site_state")

    # ------------------------------------------------------------------
    # OSHA data queries
    # ------------------------------------------------------------------

    async def _query_osha_patterns(
        self,
        db: AsyncSession,
        state: str | None,
        naics_prefix: str | None,
    ) -> dict:
        """Query OSHA enforcement data for violation patterns."""
        since_date = date.today() - timedelta(days=5 * 365)

        try:
            # Get violation counts by standard
            sql = text("""
                SELECT
                    v.standard_parsed AS standard,
                    COUNT(*) AS count,
                    COUNT(*) FILTER (WHERE v.violation_type = 'S') AS serious_count,
                    COUNT(*) FILTER (WHERE v.violation_type = 'W') AS willful_count,
                    COALESCE(AVG(v.gravity), 0) AS avg_gravity
                FROM osha_violations v
                JOIN osha_inspections i ON v.activity_nr = i.activity_nr
                WHERE v.standard_parsed IS NOT NULL
                  AND i.open_date >= :since_date
                  AND (:state::text IS NULL OR i.site_state = :state)
                  AND (:naics_prefix::text IS NULL OR i.naics_code LIKE :naics_prefix || '%')
                GROUP BY v.standard_parsed
                ORDER BY count DESC
                LIMIT 25
            """)
            result = await db.execute(
                sql,
                {
                    "since_date": since_date,
                    "state": state,
                    "naics_prefix": naics_prefix,
                },
            )
            rows = result.mappings().all()

            top_standards = []
            for row in rows:
                top_standards.append(
                    {
                        "standard": row["standard"],
                        "count": row["count"],
                        "serious_count": row["serious_count"],
                        "willful_count": row["willful_count"],
                        "avg_gravity": float(row["avg_gravity"]),
                    }
                )

            # Get totals
            totals_sql = text("""
                SELECT
                    COUNT(DISTINCT i.activity_nr) AS total_inspections,
                    COUNT(v.id) AS total_violations
                FROM osha_inspections i
                LEFT JOIN osha_violations v ON v.activity_nr = i.activity_nr
                WHERE i.open_date >= :since_date
                  AND (:state::text IS NULL OR i.site_state = :state)
                  AND (:naics_prefix::text IS NULL OR i.naics_code LIKE :naics_prefix || '%')
            """)
            totals_result = await db.execute(
                totals_sql,
                {
                    "since_date": since_date,
                    "state": state,
                    "naics_prefix": naics_prefix,
                },
            )
            totals = totals_result.mappings().first()

            total_inspections = totals["total_inspections"] if totals else 0
            total_violations = totals["total_violations"] if totals else 0
            violation_rate = total_violations / total_inspections if total_inspections > 0 else 0.0

            # Compute per-category violation rates
            category_rates: dict[str, float] = {}
            for cat_name, standards in _HAZARD_STANDARDS.items():
                cat_count = sum(s["count"] for s in top_standards if s["standard"] in standards)
                category_rates[cat_name] = cat_count / max(total_inspections, 1)

            return {
                "total_inspections": total_inspections,
                "total_violations": total_violations,
                "violation_rate": round(violation_rate, 4),
                "top_standards": top_standards,
                "category_rates": category_rates,
            }

        except Exception as exc:
            logger.warning("OSHA data query failed: %s", exc)
            return {
                "total_inspections": 0,
                "total_violations": 0,
                "violation_rate": 0.0,
                "top_standards": [],
                "category_rates": {},
            }

    # ------------------------------------------------------------------
    # Individual hazard scoring functions
    # ------------------------------------------------------------------

    def _score_fall_risk(self, features: dict, osha: dict) -> RiskCategory:
        """Score fall hazard risk (OSHA Focus Four #1)."""
        score = 10.0  # Baseline
        factors: list[str] = []
        mitigations: list[str] = []

        # Activities involving heights
        height_keywords = [
            "roof",
            "scaffold",
            "ladder",
            "steel",
            "erect",
            "framing",
            "deck",
            "siding",
            "curtain wall",
            "glazing",
            "form",
            "paint",
            "demolit",
            "masonry",
        ]
        has_height_work = any(features.get(f"has_{kw}", False) for kw in height_keywords)
        if has_height_work:
            score += 25
            factors.append("Activities involving working at height scheduled today")
            mitigations.append("Verify fall protection plans and equipment for all elevated work")
            mitigations.append("Conduct pre-task briefing on fall hazards for affected crews")

        # Wind speed increases fall risk
        wind = features.get("wind_speed", 0)
        if wind > 30:
            score += 25
            factors.append(f"High wind speed ({wind} mph) — suspend elevated work")
            mitigations.append("Suspend all work above 6 feet until wind subsides below 30 mph")
        elif wind > 20:
            score += 15
            factors.append(f"Moderate wind ({wind} mph) — increased fall hazard")
            mitigations.append(
                "Extra vigilance for scaffold and ladder work; secure loose materials"
            )
        elif wind > 10:
            score += 5

        # Precipitation makes surfaces slippery
        precip = features.get("precipitation_mm", 0)
        if precip > 5:
            score += 15
            factors.append("Rain increases slip/fall risk on elevated surfaces")
            mitigations.append("Ensure non-slip surfaces on walkways and scaffolds")
        elif precip > 0:
            score += 5

        # OSHA violation rate for fall standards
        fall_rate = osha.get("category_rates", {}).get("fall_risk", 0)
        if fall_rate > 0.5:
            score += 15
            factors.append(f"High regional fall violation rate ({fall_rate:.1%})")
        elif fall_rate > 0.2:
            score += 8

        # First month of project — elevated risk
        age = features.get("project_age_months", 6)
        if age < 1:
            score += 10
            factors.append("First month of project — workers unfamiliar with site")
            mitigations.append("Ensure all workers have completed site orientation")

        return RiskCategory(
            name="fall_risk",
            score=_clamp(score),
            label=_score_label(_clamp(score)),
            factors=factors,
            mitigations=mitigations,
        )

    def _score_struck_by_risk(self, features: dict, osha: dict) -> RiskCategory:
        """Score struck-by hazard risk (OSHA Focus Four #2)."""
        score = 8.0
        factors: list[str] = []
        mitigations: list[str] = []

        # Crane/rigging/hoisting
        has_crane = any(features.get(f"has_{kw}", False) for kw in ["crane", "hoist", "rigging"])
        if has_crane:
            score += 20
            factors.append("Crane/hoisting operations scheduled today")
            mitigations.append("Establish and enforce exclusion zones around crane operations")
            mitigations.append("Verify crane inspection and operator certification current")

        # Excavation equipment
        has_excavation_equip = features.get("has_excavat", False) or features.get(
            "has_grade", False
        )
        if has_excavation_equip:
            score += 10
            factors.append("Heavy equipment operations scheduled")
            mitigations.append("Maintain minimum clearance around operating equipment")

        # Wind + crane = high risk
        wind = features.get("wind_speed", 0)
        if has_crane and wind > 25:
            score += 20
            factors.append(f"Wind ({wind} mph) exceeds crane safe operating limit")
            mitigations.append("Suspend crane operations until wind is below 25 mph")
        elif has_crane and wind > 15:
            score += 10
            factors.append(f"Wind ({wind} mph) requires crane operator awareness")

        # Concrete/materials delivery
        has_concrete = features.get("has_concrete", False) or features.get("has_pour", False)
        if has_concrete:
            score += 8
            factors.append("Concrete/materials delivery increases struck-by exposure")
            mitigations.append("Designate traffic routes and spotters for material deliveries")

        # OSHA rates
        rate = osha.get("category_rates", {}).get("struck_by_risk", 0)
        if rate > 0.3:
            score += 12
            factors.append(f"Elevated regional struck-by violation rate ({rate:.1%})")

        return RiskCategory(
            name="struck_by_risk",
            score=_clamp(score),
            label=_score_label(_clamp(score)),
            factors=factors,
            mitigations=mitigations,
        )

    def _score_electrical_risk(self, features: dict, osha: dict) -> RiskCategory:
        """Score electrical hazard risk (OSHA Focus Four #3)."""
        score = 5.0
        factors: list[str] = []
        mitigations: list[str] = []

        has_electrical = any(
            features.get(f"has_{kw}", False)
            for kw in ["electr", "wiring", "panel", "conduit", "weld", "hvac"]
        )
        if has_electrical:
            score += 25
            factors.append("Electrical work or welding scheduled today")
            mitigations.append("Verify LOTO procedures in place before energized work")
            mitigations.append("Ensure all electrical workers have appropriate arc flash PPE")

        # Rain + electrical = high risk
        precip = features.get("precipitation_mm", 0)
        if has_electrical and precip > 2:
            score += 20
            factors.append("Wet conditions increase electrical shock hazard")
            mitigations.append("Use GFCI protection on all temporary power; inspect cords")
        elif precip > 5:
            score += 5
            mitigations.append("Inspect temporary electrical connections in wet areas")

        # OSHA rates
        rate = osha.get("category_rates", {}).get("electrical_risk", 0)
        if rate > 0.2:
            score += 10
            factors.append(f"Elevated regional electrical violation rate ({rate:.1%})")

        return RiskCategory(
            name="electrical_risk",
            score=_clamp(score),
            label=_score_label(_clamp(score)),
            factors=factors,
            mitigations=mitigations,
        )

    def _score_excavation_risk(self, features: dict, osha: dict) -> RiskCategory:
        """Score excavation/cave-in hazard risk (OSHA Focus Four #4)."""
        score = 5.0
        factors: list[str] = []
        mitigations: list[str] = []

        has_excavation = any(
            features.get(f"has_{kw}", False)
            for kw in ["excavat", "trench", "foundation", "grade", "backfill", "plumb"]
        )
        if has_excavation:
            score += 25
            factors.append("Excavation or trenching work scheduled today")
            mitigations.append("Verify competent person on-site for excavation inspection")
            mitigations.append(
                "Ensure trench protective systems (shoring/sloping/shielding) in place"
            )

        # Recent rain saturates soil → cave-in risk
        precip = features.get("precipitation_mm", 0)
        if has_excavation and precip > 10:
            score += 30
            factors.append(f"Heavy rainfall ({precip}mm) — saturated soil increases cave-in risk")
            mitigations.append("Re-inspect all open excavations before entry after rain")
            mitigations.append("Pump standing water from trenches; check soil conditions")
        elif has_excavation and precip > 2:
            score += 15
            factors.append(f"Rain ({precip}mm) — inspect excavation stability")

        # OSHA rates
        rate = osha.get("category_rates", {}).get("excavation_risk", 0)
        if rate > 0.15:
            score += 12
            factors.append(f"Elevated regional excavation violation rate ({rate:.1%})")

        # Recent month = spring thaw in northern states
        month = features.get("month", 6)
        state = features.get("state", "")
        if has_excavation and month in (3, 4) and state in _NORTHERN_STATES:
            score += 8
            factors.append("Spring thaw — unstable soil conditions likely")

        return RiskCategory(
            name="excavation_risk",
            score=_clamp(score),
            label=_score_label(_clamp(score)),
            factors=factors,
            mitigations=mitigations,
        )

    def _score_heat_risk(self, features: dict, osha: dict) -> RiskCategory:
        """Score heat illness risk."""
        score = 5.0
        factors: list[str] = []
        mitigations: list[str] = []

        heat_index = features.get("heat_index", 72)
        temp_high = features.get("temp_high", 72)

        if heat_index >= 115:
            score += 50
            factors.append(f"Extreme heat index ({heat_index:.0f}°F) — work stoppage recommended")
            mitigations.append("Consider halting outdoor work during peak heat (10am-4pm)")
            mitigations.append(
                "Mandatory buddy system; monitor all workers for heat stroke symptoms"
            )
        elif heat_index >= 103:
            score += 35
            factors.append(f"Dangerous heat index ({heat_index:.0f}°F)")
            mitigations.append("Enforce mandatory 15-min shade breaks every hour")
            mitigations.append("Provide electrolyte drinks; assign heat illness observer")
        elif heat_index >= 91:
            score += 20
            factors.append(f"Elevated heat index ({heat_index:.0f}°F)")
            mitigations.append("Ensure water stations within 100 feet of all work areas")
            mitigations.append("Schedule heavy physical work for early morning hours")
        elif heat_index >= 80:
            score += 8
            factors.append(f"Warm conditions ({heat_index:.0f}°F heat index)")

        # Outdoor work amplifies heat risk
        outdoor_keywords = ["roof", "concrete", "excavat", "grade", "steel", "masonry", "paint"]
        has_outdoor = any(features.get(f"has_{kw}", False) for kw in outdoor_keywords)
        if has_outdoor and heat_index >= 91:
            score += 10
            factors.append("Outdoor heavy work amplifies heat exposure")

        # Cold weather risks (frostbite, hypothermia)
        if temp_high < 32:
            score += 15
            factors.append(f"Freezing temperatures ({temp_high}°F) — cold stress risk")
            mitigations.append("Provide heated break areas; monitor for hypothermia signs")
        elif temp_high < 40:
            score += 5
            factors.append(f"Cold conditions ({temp_high}°F)")

        # Summer months amplify
        month = features.get("month", 6)
        if month in (6, 7, 8) and heat_index >= 85:
            score += 5

        return RiskCategory(
            name="heat_illness_risk",
            score=_clamp(score),
            label=_score_label(_clamp(score)),
            factors=factors,
            mitigations=mitigations,
        )

    # ------------------------------------------------------------------
    # Factor extraction helpers
    # ------------------------------------------------------------------

    def _extract_weather_factors(self, features: dict) -> dict:
        return {
            "temperature_high": features.get("temp_high"),
            "temperature_low": features.get("temp_low"),
            "wind_speed_max": features.get("wind_speed"),
            "precipitation_mm": features.get("precipitation_mm"),
            "humidity": features.get("humidity"),
            "heat_index": features.get("heat_index"),
        }

    def _extract_schedule_factors(self, features: dict, activities: list[dict] | None) -> dict:
        active = [kw for kw in _ACTIVITY_HAZARD_MAP if features.get(f"has_{kw}", False)]
        return {
            "active_hazard_keywords": active,
            "activity_count": len(activities) if activities else 0,
            "active_hazard_categories": list(features.get("active_hazards", set())),
        }

    def _extract_project_factors(self, features: dict) -> dict:
        return {
            "project_type": features.get("project_type"),
            "state": features.get("state"),
            "project_age_months": features.get("project_age_months"),
            "num_workers": features.get("num_workers"),
            "month": features.get("month"),
        }

    # ------------------------------------------------------------------
    # Template fallback briefing
    # ------------------------------------------------------------------

    def _template_briefing(
        self,
        risk: DailyRiskResult,
        project: dict,
        weather_summary: str,
        activity_list: str,
    ) -> str:
        """Generate a simple template-based briefing without LLM."""
        lines = [
            f"SAFETY BRIEFING — {project.get('name', 'Project')}",
            f"Date: {risk.score_date.isoformat()}",
            f"Overall Risk: {risk.overall_score}/100 ({_score_label(risk.overall_score)})",
            "",
            f"WEATHER: {weather_summary}",
            "",
            f"TODAY'S ACTIVITIES: {activity_list}",
            "",
            "TOP RISKS:",
        ]
        if risk.top_risks:
            for r in risk.top_risks:
                label = r["category"].replace("_", " ").title()
                lines.append(f"  - {label}: {r['label']} ({r['score']}/100)")
                for f in r.get("factors", [])[:2]:
                    lines.append(f"    * {f}")
        else:
            lines.append("  No elevated risks identified.")

        lines.append("")
        lines.append("REQUIRED MITIGATIONS:")
        if risk.recommended_mitigations:
            for m in risk.recommended_mitigations[:5]:
                lines.append(f"  - {m}")
        else:
            lines.append("  Standard safety procedures apply.")

        lines.append("")
        lines.append("REMINDERS:")
        lines.append("  - Report all near-misses and hazards immediately")
        lines.append("  - Know your emergency assembly point and first aid location")
        lines.append("  - If unsure about a task, STOP and ask your supervisor")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper: store risk score to database
# ---------------------------------------------------------------------------


async def store_risk_score(db: AsyncSession, result: DailyRiskResult) -> None:
    """Persist a DailyRiskResult to the daily_risk_scores table."""
    import uuid as _uuid

    from app.models.osha import DailyRiskScore

    # DailyRiskResult.project_id is str; DB column requires uuid.UUID
    pid: _uuid.UUID = (
        _uuid.UUID(result.project_id) if isinstance(result.project_id, str) else result.project_id
    )

    record = DailyRiskScore(
        project_id=pid,
        score_date=result.score_date,
        overall_score=result.overall_score,
        category_scores=result.category_scores,
        top_risks=result.top_risks,
        recommended_mitigations=result.recommended_mitigations,
        weather_factors=result.weather_factors,
        schedule_factors=result.schedule_factors,
        project_factors=result.project_factors,
        osha_factors=result.osha_factors,
        safety_briefing=None,
    )
    db.add(record)


# ---------------------------------------------------------------------------
# US state codes (for address parsing)
# ---------------------------------------------------------------------------

_US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

_NORTHERN_STATES = {
    "ME",
    "NH",
    "VT",
    "MA",
    "CT",
    "RI",
    "NY",
    "NJ",
    "PA",
    "OH",
    "MI",
    "IN",
    "IL",
    "WI",
    "MN",
    "IA",
    "ND",
    "SD",
    "NE",
    "MT",
    "WY",
    "ID",
    "WA",
    "OR",
    "CO",
}
