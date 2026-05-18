"""
Create productivity and equipment data:

- 30 days of crew_productivity data for 4 trades
- 30 days of equipment_telemetry data for 3 pieces of equipment
- Daily logs with work patterns
"""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.database import async_session
from app.models import CrewProductivity, EquipmentTelemetry, DailyLog

random.seed(42)
TODAY = date(2026, 2, 23)


async def seed_productivity(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    super_id = ctx["super_user_id"]

    async with async_session() as db:
        # --- 30 days of crew productivity for 4 trades ---
        trades = [
            ("concrete", "CY", 8, 12, 18),    # crew_size, planned_min, planned_max
            ("steel", "tons", 6, 3, 6),
            ("electrical", "LF", 5, 150, 250),
            ("drywall", "SF", 7, 400, 600),
        ]

        for day_offset in range(30):
            work_date = TODAY - timedelta(days=day_offset)
            if work_date.weekday() >= 5:  # Skip weekends
                continue

            for trade, unit, crew_size, plan_min, plan_max in trades:
                planned = round(random.uniform(plan_min, plan_max), 1)
                # Direct work rate between 0.45-0.65
                direct_rate = round(random.uniform(0.45, 0.65), 3)
                actual = round(planned * direct_rate * random.uniform(0.85, 1.15), 1)
                prod_rate = round(actual / (crew_size * 8), 4) if crew_size > 0 else 0
                pf = round(actual / planned, 4) if planned > 0 else 0

                # Weather impact on some days
                conditions = {}
                if random.random() < 0.2:
                    conditions = {"weather_delay": True, "delay_hours": round(random.uniform(0.5, 3), 1)}

                crew = CrewProductivity(
                    project_id=project_id,
                    trade=trade,
                    crew_size=crew_size + random.randint(-1, 1),
                    work_date=work_date,
                    planned_units=Decimal(str(planned)),
                    actual_units=Decimal(str(actual)),
                    unit_of_measure=unit,
                    productivity_rate=Decimal(str(prod_rate)),
                    pf_ratio=Decimal(str(pf)),
                    conditions=conditions,
                )
                db.add(crew)

        # --- 30 days of equipment telemetry for 3 pieces ---
        equipment = [
            ("EQ-CR01", "crane", 8.0, 12.0, 0.55, 0.80),   # engine_hrs_min/max, util_min/max
            ("EQ-EX01", "excavator", 6.0, 10.0, 0.60, 0.85),
            ("EQ-CP01", "concrete_pump", 3.0, 8.0, 0.40, 0.70),
        ]

        for day_offset in range(30):
            work_date = TODAY - timedelta(days=day_offset)
            if work_date.weekday() >= 5:
                continue

            for eq_id, eq_type, hrs_min, hrs_max, util_min, util_max in equipment:
                engine_hrs = round(random.uniform(hrs_min, hrs_max), 1)
                idle_hrs = round(engine_hrs * random.uniform(0.1, 0.3), 1)
                utilization = round(random.uniform(util_min, util_max) * 100, 1)
                fuel = round(engine_hrs * random.uniform(3, 8), 1)

                ts = datetime.combine(work_date, datetime.min.time()).replace(
                    hour=17, tzinfo=timezone.utc
                )

                telemetry = EquipmentTelemetry(
                    project_id=project_id,
                    equipment_id=eq_id,
                    equipment_type=eq_type,
                    timestamp=ts,
                    engine_hours=Decimal(str(engine_hrs)),
                    fuel_consumption=Decimal(str(fuel)),
                    idle_time_hours=Decimal(str(idle_hrs)),
                    utilization_pct=Decimal(str(utilization)),
                    location_data={"lat": 37.2710, "lon": -79.9414},
                    raw_payload={"seeded": True, "date": str(work_date)},
                )
                db.add(telemetry)

        # --- 30 days of daily logs ---
        weather_conditions = [
            {"temp_f": 45, "condition": "partly_cloudy", "wind_mph": 8, "precip_in": 0},
            {"temp_f": 52, "condition": "sunny", "wind_mph": 5, "precip_in": 0},
            {"temp_f": 38, "condition": "overcast", "wind_mph": 12, "precip_in": 0},
            {"temp_f": 42, "condition": "rain", "wind_mph": 15, "precip_in": 0.3},
            {"temp_f": 55, "condition": "sunny", "wind_mph": 3, "precip_in": 0},
            {"temp_f": 35, "condition": "snow", "wind_mph": 20, "precip_in": 0.5},
        ]

        for day_offset in range(30):
            work_date = TODAY - timedelta(days=day_offset)
            if work_date.weekday() >= 5:
                continue

            weather = random.choice(weather_conditions)
            crew_count = random.randint(35, 65)
            work_hours = round(random.uniform(7.5, 10.0), 1)

            log = DailyLog(
                project_id=project_id,
                log_date=work_date,
                weather=weather,
                crew_count=crew_count,
                work_hours=Decimal(str(work_hours)),
                activities_completed=[
                    f"Activity {random.choice(['A042', 'A050', 'A060', 'A070'])} progress"
                ],
                delays=[{"reason": "weather", "hours": 1.5}] if weather.get("precip_in", 0) > 0 else [],
                notes=f"Day {30 - day_offset} of demo period. {crew_count} workers on site.",
                created_by=super_id,
            )
            db.add(log)

        await db.commit()

    return {}
