#!/usr/bin/env python3
"""Generate demo project data for ConstructAI sales demonstrations.

Creates "Metro Center Office Tower" — a 4-story + basement commercial office
building in Roanoke VA with 5 months of realistic project history.

Usage:
    python scripts/generate_demo_project.py
    python scripts/generate_demo_project.py --clean        # delete and recreate
    python scripts/generate_demo_project.py --db-url URL   # custom database
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bid import BidDecision, BidOpportunity
from app.models.camera import Camera, SafetyZone
from app.models.change_order_lifecycle import (
    ChangeOrderRequest,
    CORPCOLink,
    PotentialChangeOrder,
)
from app.models.communication import (
    RFI,
    MeetingMinutes,
    RfiResolutionLog,
    RfiResponse,
    Submittal,
    SubmittalReview,
)
from app.models.drawing import Drawing, DrawingRevision, DrawingSet
from app.models.evm import ChangeOrder, EVMSnapshot, IntelligenceBrief
from app.models.field_management import PunchList, PunchListItem
from app.models.organization import Organization
from app.models.osha import DailyRiskScore
from app.models.pay_application import (
    PayApplication,
    PayApplicationLineItem,
    ScheduleOfValues,
)
from app.models.productivity import DailyLog
from app.models.project import Project, ProjectMember
from app.models.quality import Inspection
from app.models.safety_incident import SafetyAlert
from app.models.scheduling import ScheduleActivity, ScheduleBaseline
from app.models.user import User
from app.utils.security import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEMO_ORG_SLUG = "metro-center-demo"
NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
TODAY = date.today()
NOW = datetime.now(UTC)
PROJECT_START = TODAY - timedelta(days=152)  # ~5 months ago
PROJECT_END = PROJECT_START + timedelta(days=14 * 30)  # 14 months
DEFAULT_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://constructai:constructai@localhost:5530/constructai_test",
)

# Roanoke VA weather baselines by month (avg high F, avg precip days/month)
ROANOKE_WEATHER = {
    1: (43, 10),
    2: (47, 9),
    3: (56, 10),
    4: (66, 11),
    5: (74, 12),
    6: (82, 10),
    7: (86, 11),
    8: (84, 10),
    9: (77, 8),
    10: (67, 8),
    11: (56, 9),
    12: (46, 10),
}

SAFETY_TOPICS = [
    "Fall protection and guardrails",
    "PPE compliance on site",
    "Excavation and trench safety",
    "Electrical safety / lockout-tagout",
    "Crane and rigging safety",
    "Scaffold inspection procedures",
    "Heat illness prevention",
    "Silica dust exposure control",
    "Fire prevention and extinguisher locations",
    "Housekeeping and trip hazards",
    "Confined space entry procedures",
    "Hand and power tool safety",
    "Ladder safety — 3-point contact",
    "Material handling and back injury prevention",
    "Emergency evacuation procedures",
    "Struck-by hazard awareness",
]


def _uid(name: str) -> uuid.UUID:
    """Deterministic UUID from a name string."""
    return uuid.uuid5(NS, name)


def _ts(d: date, hour: int = 8) -> datetime:
    """Convert date to timezone-aware datetime."""
    return datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=UTC)


def _workdays(start: date, end: date) -> list[date]:
    """Return list of Mon-Fri dates between start and end inclusive."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _add_workdays(start: date, n: int) -> date:
    """Add n workdays to start date."""
    current = start
    added = 0
    while added < n:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _weather(d: date) -> dict:
    """Generate realistic Roanoke VA weather for a given date."""
    rng = random.Random(d.toordinal())
    avg_high, precip_days = ROANOKE_WEATHER[d.month]
    temp = avg_high + rng.randint(-8, 8)
    is_precip = rng.random() < (precip_days / 30)
    if d.month in (12, 1, 2) and is_precip and temp < 36:
        conditions = "snow"
        precip = round(rng.uniform(0.5, 3.0), 1)
    elif is_precip:
        conditions = "rain"
        precip = round(rng.uniform(0.1, 1.2), 1)
    elif rng.random() < 0.3:
        conditions = "partly_cloudy"
        precip = 0.0
    else:
        conditions = "clear"
        precip = 0.0
    return {
        "temperature_high": temp,
        "temperature_low": temp - rng.randint(12, 22),
        "conditions": conditions,
        "precipitation_inches": precip,
        "wind_mph": rng.randint(3, 18),
    }


# ---------------------------------------------------------------------------
# 1. Organization & Users
# ---------------------------------------------------------------------------
async def create_org_and_users(s: AsyncSession):
    org = Organization(
        id=_uid("org"),
        name="Metro Center Development Group",
        slug=DEMO_ORG_SLUG,
        type="gc",
        settings={},
    )
    s.add(org)

    user_defs = [
        ("sarah-chen", "Sarah Chen", "sarah.chen@metrocenter.demo", "org_admin"),
        ("mike-rodriguez", "Mike Rodriguez", "mike.r@metrocenter.demo", "superintendent"),
        ("lisa-park", "Lisa Park", "lisa.park@metrocenter.demo", "safety_manager"),
        ("james-wilson", "James Wilson", "james.w@metrocenter.demo", "project_admin"),
        ("amy-nguyen", "Amy Nguyen", "amy.n@metrocenter.demo", "field_engineer"),
        ("david-brooks", "David Brooks", "david.b@metrocenter.demo", "field_engineer"),
    ]
    users = []
    pw = hash_password("DemoPass123!")
    for slug, name, email, role in user_defs:
        u = User(
            id=_uid(slug),
            org_id=org.id,
            email=email,
            hashed_password=pw,
            full_name=name,
            role=role,
            email_verified=True,
            is_active=True,
        )
        s.add(u)
        users.append(u)

    await s.flush()
    logger.info("Created org + %d users", len(users))
    return org, users


# ---------------------------------------------------------------------------
# 2. Project & Members
# ---------------------------------------------------------------------------
async def create_project(s: AsyncSession, org, users):
    project = Project(
        id=_uid("project"),
        org_id=org.id,
        name="Metro Center Office Tower",
        project_number="MCO-2025-001",
        type="commercial",
        status="active",
        address="401 S Jefferson St, Roanoke, VA 24011",
        contract_value=Decimal("12500000.00"),
        start_date=PROJECT_START,
        end_date=PROJECT_END,
        settings={"time_zone": "America/New_York"},
        metadata_={"floors": 5, "gross_sf": 85000, "stories_above": 4, "basement": True},
    )
    s.add(project)

    roles = [
        "project_manager",
        "superintendent",
        "safety_manager",
        "project_admin",
        "field_engineer",
        "field_engineer",
    ]
    for i, u in enumerate(users):
        s.add(
            ProjectMember(
                id=_uid(f"pm-{i}"),
                project_id=project.id,
                user_id=u.id,
                role=roles[i],
                invited_by=users[0].id,
            )
        )

    await s.flush()
    logger.info("Created project + %d members", len(users))
    return project


# ---------------------------------------------------------------------------
# 3. Schedule — 215 activities across 9 WBS phases
# ---------------------------------------------------------------------------
async def create_schedule(s: AsyncSession, project, users):
    baseline = ScheduleBaseline(
        id=_uid("baseline"),
        project_id=project.id,
        name="Original Baseline",
        version=1,
        baseline_date=PROJECT_START - timedelta(days=7),
        total_duration_days=305,
        critical_path_length=280,
        dcma_score=Decimal("76.00"),
        dcma_results={
            "logic_density": 82,
            "leads": 0,
            "lags": 8,
            "high_float": 12,
            "negative_float": 0,
            "missing_predecessors": 3,
            "missing_successors": 2,
        },
        source_format="manual",
        calendars=[
            {
                "id": "cal-1",
                "name": "Standard 5-Day",
                "work_days": [0, 1, 2, 3, 4],
                "hours_per_day": 8.0,
            }
        ],
        data_date=TODAY,
        created_by=users[0].id,
    )
    s.add(baseline)

    activities = []
    act_idx = 0

    def _act(
        code,
        name,
        dur,
        wbs,
        preds=None,
        is_crit=False,
        start_offset=None,
        actual_pct=None,
        delay_days=0,
    ):
        """Create a schedule activity. start_offset = workdays from PROJECT_START."""
        nonlocal act_idx
        act_idx += 1
        planned_start = (
            _add_workdays(PROJECT_START, start_offset) if start_offset is not None else None
        )
        planned_finish = _add_workdays(planned_start, dur) if planned_start else None

        # Determine progress status
        if actual_pct is not None and actual_pct >= 100:
            status = "complete"
            a_start = planned_start
            a_finish = _add_workdays(planned_start, dur + delay_days)
            pct = Decimal("100.00")
        elif actual_pct is not None and actual_pct > 0:
            status = "in_progress"
            a_start = planned_start
            a_finish = None
            pct = Decimal(str(actual_pct))
        else:
            status = "not_started"
            a_start = None
            a_finish = None
            pct = Decimal("0.00")

        total_float = 0 if is_crit else random.randint(3, 25)
        act = ScheduleActivity(
            id=_uid(f"act-{code}"),
            project_id=project.id,
            baseline_id=baseline.id,
            activity_code=code,
            name=name,
            duration_days=dur,
            start_date=planned_start,
            finish_date=planned_finish,
            early_start=planned_start,
            early_finish=planned_finish,
            late_start=_add_workdays(planned_start, total_float) if planned_start else None,
            late_finish=_add_workdays(planned_finish, total_float) if planned_finish else None,
            total_float=total_float,
            free_float=max(0, total_float - random.randint(0, 5)),
            is_critical=is_crit,
            predecessors=preds or [],
            resource_assignments=[],
            wbs_code=wbs,
            wbs_path=wbs,
            calendar_id="cal-1",
            original_id=code,
            status=status,
            actual_start=a_start,
            actual_finish=a_finish,
            pct_complete=pct,
            metadata_={},
        )
        s.add(act)
        activities.append(act)
        return code

    # --- Phase 1: Mobilization & Site Work (Weeks 1-3) ---
    _act("1010", "Mobilization", 5, "1", is_crit=True, start_offset=0, actual_pct=100)
    _act("1020", "Erosion & Sediment Control", 3, "1", ["1010"], start_offset=5, actual_pct=100)
    _act("1030", "Temporary Utilities", 4, "1", ["1010"], start_offset=5, actual_pct=100)
    _act(
        "1040",
        "Site Survey & Layout",
        3,
        "1",
        ["1010"],
        is_crit=True,
        start_offset=5,
        actual_pct=100,
    )
    _act("1050", "Tree Protection & Clearing", 3, "1", ["1020"], start_offset=8, actual_pct=100)
    _act("1060", "Construction Fencing", 2, "1", ["1010"], start_offset=5, actual_pct=100)
    _act("1070", "Temporary Roads", 4, "1", ["1050"], start_offset=11, actual_pct=100)
    _act(
        "1080",
        "Dewatering System Install",
        5,
        "1",
        ["1040"],
        is_crit=True,
        start_offset=8,
        actual_pct=100,
    )
    _act("1090", "Trailer & Laydown Setup", 3, "1", ["1060"], start_offset=7, actual_pct=100)
    _act("1100", "Utility Locate & Mark", 2, "1", ["1040"], start_offset=8, actual_pct=100)
    _act("1110", "Storm Sewer Relocation", 6, "1", ["1100"], start_offset=10, actual_pct=100)
    _act("1120", "Haul Road Construction", 3, "1", ["1070"], start_offset=15, actual_pct=100)

    # --- Phase 2: Foundation & Substructure (Weeks 2-8) ---
    _act(
        "2010",
        "Mass Excavation",
        8,
        "2",
        ["1040", "1080"],
        is_crit=True,
        start_offset=13,
        actual_pct=100,
        delay_days=5,
    )
    _act("2020", "Rock Removal", 4, "2", ["2010"], start_offset=18, actual_pct=100)
    _act(
        "2030",
        "Proof Roll Subgrade",
        2,
        "2",
        ["2010"],
        is_crit=True,
        start_offset=26,
        actual_pct=100,
    )
    _act(
        "2040",
        "Stone Base & Compaction",
        3,
        "2",
        ["2030"],
        is_crit=True,
        start_offset=28,
        actual_pct=100,
    )
    _act("2050", "Underslab Utilities", 5, "2", ["2040"], start_offset=31, actual_pct=100)
    _act(
        "2060",
        "Waterproofing — Foundation",
        4,
        "2",
        ["2040"],
        is_crit=True,
        start_offset=31,
        actual_pct=100,
    )
    _act(
        "2070",
        "Foundation Wall Forms",
        6,
        "2",
        ["2060"],
        is_crit=True,
        start_offset=35,
        actual_pct=100,
    )
    _act(
        "2080",
        "Foundation Wall Rebar",
        5,
        "2",
        ["2070"],
        is_crit=True,
        start_offset=41,
        actual_pct=100,
    )
    _act(
        "2090",
        "Foundation Wall Pour",
        3,
        "2",
        ["2080"],
        is_crit=True,
        start_offset=46,
        actual_pct=100,
    )
    _act("2100", "Strip & Cure Foundation Walls", 5, "2", ["2090"], start_offset=49, actual_pct=100)
    _act("2110", "Spread Footing Forms", 4, "2", ["2040"], start_offset=31, actual_pct=100)
    _act("2120", "Spread Footing Rebar", 3, "2", ["2110"], start_offset=35, actual_pct=100)
    _act("2130", "Spread Footing Pour", 2, "2", ["2120"], start_offset=38, actual_pct=100)
    _act("2140", "SOG Vapor Barrier", 2, "2", ["2050", "2130"], start_offset=40, actual_pct=100)
    _act("2150", "SOG Rebar & WWF", 3, "2", ["2140"], start_offset=42, actual_pct=100)
    _act(
        "2160",
        "SOG Pour — Basement",
        2,
        "2",
        ["2150"],
        is_crit=True,
        start_offset=45,
        actual_pct=100,
    )
    _act("2170", "Backfill & Compaction", 5, "2", ["2100"], start_offset=54, actual_pct=100)
    _act(
        "2180", "Foundation Drain & Dimple Board", 4, "2", ["2170"], start_offset=59, actual_pct=100
    )
    _act("2190", "Elevator Pit Construction", 5, "2", ["2160"], start_offset=47, actual_pct=100)
    _act("2200", "Sump Pit & Ejector Pit", 3, "2", ["2160"], start_offset=47, actual_pct=100)
    _act(
        "2210",
        "Basement Waterproofing — Below Grade",
        4,
        "2",
        ["2170"],
        start_offset=59,
        actual_pct=100,
    )
    _act(
        "2220",
        "Foundation Inspection Sign-off",
        1,
        "2",
        ["2180", "2210"],
        start_offset=63,
        actual_pct=100,
    )

    # --- Phase 3: Structure — per floor (Weeks 6-16) ---
    floors = [
        ("B", "Basement"),
        ("1", "1st Floor"),
        ("2", "2nd Floor"),
        ("3", "3rd Floor"),
        ("4", "4th Floor"),
    ]
    prev_deck = "2160"  # SOG basement is predecessor for 1st floor structure
    for fi, (fl, fl_name) in enumerate(floors):
        base_off = 48 + fi * 15  # stagger floors
        pct_base = max(0, min(100, 100 - fi * 25))  # descending progress
        is_f_crit = fi <= 3  # floors B-3 on critical path
        if fl == "B":
            # Basement columns only (slab already poured)
            _act(
                f"3{fl}10",
                "Basement Columns — Forms",
                4,
                "3.B",
                ["2160"],
                is_crit=True,
                start_offset=base_off,
                actual_pct=100,
            )
            _act(
                f"3{fl}20",
                "Basement Columns — Rebar",
                3,
                "3.B",
                [f"3{fl}10"],
                is_crit=True,
                start_offset=base_off + 4,
                actual_pct=100,
            )
            _act(
                f"3{fl}30",
                "Basement Columns — Pour",
                2,
                "3.B",
                [f"3{fl}20"],
                is_crit=True,
                start_offset=base_off + 7,
                actual_pct=100,
            )
            _act(
                f"3{fl}40",
                "Basement Beam Forms",
                5,
                "3.B",
                [f"3{fl}30"],
                is_crit=True,
                start_offset=base_off + 9,
                actual_pct=100,
            )
            _act(
                f"3{fl}50",
                "Basement Beam Rebar",
                3,
                "3.B",
                [f"3{fl}40"],
                is_crit=True,
                start_offset=base_off + 14,
                actual_pct=100,
            )
            _act(
                f"3{fl}60",
                "1st Floor Deck Forms",
                5,
                "3.B",
                [f"3{fl}50"],
                is_crit=True,
                start_offset=base_off + 17,
                actual_pct=100,
            )
            _act(
                f"3{fl}70",
                "1st Floor Deck Rebar",
                3,
                "3.B",
                [f"3{fl}60"],
                is_crit=True,
                start_offset=base_off + 22,
                actual_pct=100,
            )
            _act(
                f"3{fl}80",
                "1st Floor Deck Pour",
                2,
                "3.B",
                [f"3{fl}70"],
                is_crit=True,
                start_offset=base_off + 25,
                actual_pct=100,
            )
            prev_deck = f"3{fl}80"
        else:
            c = f"3{fl}00"
            _act(
                f"{c}1",
                f"{fl_name} Column Forms",
                4,
                f"3.{fl}",
                [prev_deck],
                is_crit=is_f_crit,
                start_offset=base_off,
                actual_pct=pct_base,
            )
            _act(
                f"{c}2",
                f"{fl_name} Column Rebar",
                3,
                f"3.{fl}",
                [f"{c}1"],
                is_crit=is_f_crit,
                start_offset=base_off + 4,
                actual_pct=max(0, pct_base - 10),
            )
            _act(
                f"{c}3",
                f"{fl_name} Column Pour",
                2,
                f"3.{fl}",
                [f"{c}2"],
                is_crit=is_f_crit,
                start_offset=base_off + 7,
                actual_pct=max(0, pct_base - 15),
            )
            _act(
                f"{c}4",
                f"{fl_name} Beam Forms",
                5,
                f"3.{fl}",
                [f"{c}3"],
                is_crit=is_f_crit,
                start_offset=base_off + 9,
                actual_pct=max(0, pct_base - 25),
            )
            _act(
                f"{c}5",
                f"{fl_name} Beam Rebar",
                3,
                f"3.{fl}",
                [f"{c}4"],
                is_crit=is_f_crit,
                start_offset=base_off + 14,
                actual_pct=max(0, pct_base - 35),
            )
            _act(
                f"{c}6",
                f"{fl_name} Deck Forms",
                5,
                f"3.{fl}",
                [f"{c}5"],
                is_crit=is_f_crit,
                start_offset=base_off + 17,
                actual_pct=max(0, pct_base - 45),
            )
            _act(
                f"{c}7",
                f"{fl_name} Deck Rebar & PT",
                4,
                f"3.{fl}",
                [f"{c}6"],
                is_crit=is_f_crit,
                start_offset=base_off + 22,
                actual_pct=max(0, pct_base - 55),
            )
            _act(
                f"{c}8",
                f"{fl_name} Deck Pour",
                2,
                f"3.{fl}",
                [f"{c}7"],
                is_crit=is_f_crit,
                start_offset=base_off + 26,
                actual_pct=max(0, pct_base - 60),
            )
            prev_deck = f"{c}8"
        # Steel miscellaneous per floor
        _act(
            f"3{fl}S1",
            f"Structural Steel — {fl_name}",
            5,
            f"3.{fl}",
            [prev_deck],
            start_offset=base_off + 12,
            actual_pct=max(0, pct_base - 20),
        )
        _act(
            f"3{fl}S2",
            f"Misc Metals & Embeds — {fl_name}",
            3,
            f"3.{fl}",
            [f"3{fl}S1"],
            start_offset=base_off + 17,
            actual_pct=max(0, pct_base - 30),
        )

    # --- Phase 4: Building Envelope (Weeks 14-22) ---
    env_off = 98
    env_items = [
        ("4010", "Curtain Wall Shop Drawings Review", 5, [], False, 30),
        ("4020", "Curtain Wall Fabrication Lead", 20, ["4010"], False, 0),
        ("4030", "Curtain Wall Anchors — Basement", 4, ["3B80", "4020"], True, 25, 8),
        ("4040", "Curtain Wall Install — Basement", 8, ["4030"], False, 20),
        ("4050", "Curtain Wall Anchors — 1st Floor", 4, ["31008", "4030"], True, 15),
        ("4060", "Curtain Wall Install — 1st Floor", 8, ["4050"], False, 10),
        ("4070", "Curtain Wall Anchors — 2nd Floor", 4, ["32008", "4050"], False, 5),
        ("4080", "Curtain Wall Install — 2nd Floor", 8, ["4070"], False, 0),
        ("4090", "Curtain Wall — 3rd Floor Anchors", 4, ["33008"], False, 0),
        ("4100", "Curtain Wall — 3rd Floor Install", 8, ["4090"], False, 0),
        ("4110", "Curtain Wall — 4th Floor Anchors", 4, ["34008"], False, 0),
        ("4120", "Curtain Wall — 4th Floor Install", 8, ["4110"], False, 0),
        ("4130", "Roof Deck Insulation", 4, ["34008"], False, 0),
        ("4140", "Roofing Membrane — TPO", 6, ["4130"], False, 0),
        ("4150", "Roof Flashing & Copings", 3, ["4140"], False, 0),
        ("4160", "Roof Penetrations & Curbs", 3, ["4130"], False, 0),
        ("4170", "Below-Grade Waterproofing Touch-up", 3, ["2210"], False, 100),
        ("4180", "Exterior Sealant & Caulking — B/1", 4, ["4040", "4060"], False, 5),
        ("4190", "Window Install — Stair Towers", 3, ["4060"], False, 0),
        ("4200", "Louvers & Vents Install", 3, ["4060"], False, 0),
        ("4210", "Exterior Door Frames", 4, ["4040"], False, 10),
        ("4220", "Loading Dock Door", 3, ["4210"], False, 0),
        ("4230", "Metal Panel Soffit — Entrance", 4, ["4060"], False, 0),
        ("4240", "Vapor Barrier — Exterior Walls", 5, ["4060"], False, 5),
        ("4250", "Rigid Insulation — Exterior", 5, ["4240"], False, 0),
        ("4260", "Air Barrier Inspection", 1, ["4250"], False, 0),
    ]
    for item in env_items:
        code, name, dur, preds = item[0], item[1], item[2], item[3]
        is_crit = item[4] if len(item) > 4 else False
        pct = item[5] if len(item) > 5 else 0
        delay = item[6] if len(item) > 6 else 0
        off = env_off + env_items.index(item) * 2
        _act(
            code,
            name,
            dur,
            "4",
            preds,
            is_crit=is_crit,
            start_offset=off,
            actual_pct=pct,
            delay_days=delay,
        )

    # --- Phase 5: MEP Rough-in per floor (Weeks 12-28) ---
    mep_trades = [
        ("HVAC Duct Rough", 6),
        ("Plumbing Rough", 5),
        ("Electrical Conduit", 5),
        ("Fire Sprinkler Rough", 4),
        ("Low Voltage Rough", 3),
        ("HVAC Piping", 5),
        ("Plumbing DWV", 4),
        ("Electrical Panel Feeders", 4),
    ]
    for fi, (fl, fl_name) in enumerate(floors):
        mep_off = 80 + fi * 18
        deck_pred = f"3{fl}80" if fl == "B" else f"3{fl}008"
        for ti, (trade_name, dur) in enumerate(mep_trades):
            code = f"5{fl}{ti:02d}"
            pct = max(0, 100 - fi * 30 - ti * 8) if fi <= 1 else 0
            preds_list = [deck_pred] if ti == 0 else [f"5{fl}{ti - 1:02d}"]
            _act(
                code,
                f"{trade_name} — {fl_name}",
                dur,
                f"5.{fl}",
                preds_list,
                is_crit=(fi == 0 and ti < 2),
                start_offset=mep_off + ti * 4,
                actual_pct=pct,
                delay_days=3 if code == "5B04" else 0,
            )

    # --- Phase 6: Interior Finishes per floor (Weeks 20-36) ---
    finish_trades = [
        ("Metal Stud Framing", 6),
        ("Drywall Hang", 5),
        ("Drywall Tape & Finish", 5),
        ("Acoustical Ceiling Grid", 4),
        ("Ceiling Tile Install", 3),
        ("Floor Prep & Leveling", 3),
        ("Flooring Install", 5),
        ("Paint — Primer & Finish", 5),
    ]
    for fi, (fl, fl_name) in enumerate(floors):
        fin_off = 140 + fi * 20
        for ti, (trade_name, dur) in enumerate(finish_trades):
            code = f"6{fl}{ti:02d}"
            preds_list = [f"5{fl}07"] if ti == 0 else [f"6{fl}{ti - 1:02d}"]
            _act(
                code,
                f"{trade_name} — {fl_name}",
                dur,
                f"6.{fl}",
                preds_list,
                start_offset=fin_off + ti * 4,
                actual_pct=0,
            )

    # --- Phase 7: MEP Trim & Commissioning (Weeks 32-42) ---
    trim_items = [
        ("7010", "HVAC Equipment Set", 5, ["6B07"]),
        ("7020", "AHU Installation", 6, ["7010"]),
        ("7030", "Ductwork Connections & Testing", 5, ["7020"]),
        ("7040", "Plumbing Fixture Install", 6, ["6107"]),
        ("7050", "Electrical Switchgear Set", 3, ["6107"]),
        ("7060", "Electrical Panel Terminations", 5, ["7050"]),
        ("7070", "Lighting Fixture Install", 6, ["6207"]),
        ("7080", "Fire Alarm Devices", 4, ["6207"]),
        ("7090", "Fire Sprinkler Heads & Trim", 3, ["6207"]),
        ("7100", "Low Voltage — Data/Telecom", 5, ["6307"]),
        ("7110", "BAS/BMS Controls", 6, ["7030"]),
        ("7120", "Elevator Install", 20, ["2190", "34008"]),
        ("7130", "Elevator Inspection", 2, ["7120"]),
        ("7140", "TAB — Air Balancing", 5, ["7030", "7110"]),
        ("7150", "Electrical Commissioning", 4, ["7060", "7070"]),
        ("7160", "Plumbing Commissioning", 3, ["7040"]),
        ("7170", "Fire Protection Test", 2, ["7090", "7080"]),
        ("7180", "HVAC Commissioning", 5, ["7140"]),
        ("7190", "Integrated Systems Test", 3, ["7150", "7160", "7170", "7180"]),
        ("7200", "Punch List — MEP Systems", 5, ["7190"]),
    ]
    for code, name, dur, preds in trim_items:
        _act(
            code,
            name,
            dur,
            "7",
            preds,
            start_offset=220 + trim_items.index((code, name, dur, preds)) * 5,
            actual_pct=0,
        )

    # --- Phase 8: Site Finishes (Weeks 38-44) ---
    site_items = [
        ("8010", "Concrete Sidewalks", 5, ["2170"]),
        ("8020", "Asphalt Paving — Parking", 4, ["8010"]),
        ("8030", "Striping & Signage", 2, ["8020"]),
        ("8040", "Site Lighting", 4, ["8020"]),
        ("8050", "Landscaping — Planting", 5, ["8010"]),
        ("8060", "Irrigation System", 4, ["8050"]),
        ("8070", "Retaining Wall — East", 6, ["2170"]),
        ("8080", "Site Furnishings", 2, ["8050"]),
        ("8090", "Dumpster Enclosure", 3, ["8020"]),
        ("8100", "Final Grading", 3, ["8050", "8060"]),
    ]
    for code, name, dur, preds in site_items:
        _act(
            code,
            name,
            dur,
            "8",
            preds,
            start_offset=260 + site_items.index((code, name, dur, preds)) * 4,
            actual_pct=0,
        )

    # --- Phase 9: Closeout (Weeks 42-48) ---
    close_items = [
        ("9010", "Owner Training", 3, ["7190"]),
        ("9020", "O&M Manuals", 5, ["7200"]),
        ("9030", "As-Built Drawings", 5, ["7200"]),
        ("9040", "Final Inspections — Building", 3, ["7190"]),
        ("9050", "Certificate of Occupancy", 2, ["9040"]),
        ("9060", "Punch List Walkthrough", 5, ["9050"]),
        ("9070", "Final Cleaning", 3, ["9060"]),
        ("9080", "Demobilization", 3, ["9070"]),
    ]
    for code, name, dur, preds in close_items:
        _act(
            code,
            name,
            dur,
            "9",
            preds,
            is_crit=True,
            start_offset=290 + close_items.index((code, name, dur, preds)) * 4,
            actual_pct=0,
        )

    await s.flush()
    logger.info("Created schedule baseline + %d activities", len(activities))
    return baseline, activities


# ---------------------------------------------------------------------------
# 4. Budget — SOV, Change Orders, Pay Apps, EVM
# ---------------------------------------------------------------------------
async def create_budget(s: AsyncSession, project, users):
    # --- Schedule of Values (52 items) ---
    sov_data = [
        # (item_number, description, scheduled_value, csi_code)
        ("01-001", "General Conditions", 420000, "01 10 00"),
        ("01-002", "Project Management", 180000, "01 31 00"),
        ("01-003", "Mobilization/Demobilization", 95000, "01 50 00"),
        ("01-004", "Temporary Facilities", 85000, "01 50 00"),
        ("01-005", "Insurance & Bonds", 210000, "01 40 00"),
        ("01-006", "Permits & Fees", 65000, "01 41 00"),
        ("01-007", "Quality Control & Testing", 45000, "01 45 00"),
        ("02-001", "Demolition & Site Clearing", 75000, "02 41 00"),
        ("02-002", "Earthwork & Excavation", 185000, "31 20 00"),
        ("02-003", "Dewatering", 65000, "31 23 00"),
        ("02-004", "Backfill & Compaction", 85000, "31 23 00"),
        ("02-005", "Storm Sewer", 45000, "33 40 00"),
        ("03-001", "Concrete Foundations", 380000, "03 30 00"),
        ("03-002", "Concrete SOG — Basement", 220000, "03 30 00"),
        ("03-003", "Concrete Columns", 340000, "03 30 00"),
        ("03-004", "Concrete Beams", 420000, "03 30 00"),
        ("03-005", "Concrete Decks", 680000, "03 30 00"),
        ("03-006", "Concrete Stairs", 95000, "03 30 00"),
        ("03-007", "Concrete Finishing", 120000, "03 35 00"),
        ("03-008", "Rebar Supply & Install", 480000, "03 20 00"),
        ("05-001", "Structural Steel", 680000, "05 12 00"),
        ("05-002", "Misc Metals & Embeds", 185000, "05 50 00"),
        ("05-003", "Steel Stairs & Railings", 120000, "05 51 00"),
        ("05-004", "Metal Decking", 115000, "05 31 00"),
        ("07-001", "Waterproofing — Below Grade", 145000, "07 10 00"),
        ("07-002", "Insulation — Exterior", 95000, "07 21 00"),
        ("07-003", "Air Barrier", 85000, "07 27 00"),
        ("07-004", "Roofing — TPO", 185000, "07 54 00"),
        ("07-005", "Flashing & Sheet Metal", 65000, "07 62 00"),
        ("07-006", "Sealants & Caulking", 55000, "07 92 00"),
        ("07-007", "Fireproofing", 50000, "07 81 00"),
        ("08-001", "Curtain Wall System", 980000, "08 44 00"),
        ("08-002", "Entrance Doors & Hardware", 145000, "08 11 00"),
        ("08-003", "Interior Doors & Hardware", 185000, "08 14 00"),
        ("08-004", "Glazing — Interior", 90000, "08 80 00"),
        ("09-001", "Metal Stud & Drywall", 420000, "09 21 00"),
        ("09-002", "Acoustical Ceilings", 185000, "09 51 00"),
        ("09-003", "Flooring — Carpet & LVT", 210000, "09 68 00"),
        ("09-004", "Ceramic Tile", 95000, "09 30 00"),
        ("09-005", "Painting", 145000, "09 91 00"),
        ("09-006", "Specialties — Toilet Accessories", 45000, "10 28 00"),
        ("21-001", "Fire Sprinkler System", 320000, "21 13 00"),
        ("22-001", "Plumbing — Rough & Fixtures", 385000, "22 00 00"),
        ("23-001", "HVAC — Equipment", 420000, "23 00 00"),
        ("23-002", "HVAC — Ductwork", 340000, "23 31 00"),
        ("23-003", "HVAC — Controls/BAS", 145000, "23 09 00"),
        ("23-004", "TAB", 55000, "23 05 00"),
        ("26-001", "Electrical — Power Distribution", 480000, "26 00 00"),
        ("26-002", "Electrical — Lighting", 285000, "26 51 00"),
        ("26-003", "Low Voltage / Data", 185000, "27 00 00"),
        ("26-004", "Fire Alarm System", 145000, "28 31 00"),
        ("31-001", "Site Paving & Sidewalks", 185000, "32 10 00"),
        ("31-002", "Landscaping & Irrigation", 145000, "32 90 00"),
        ("33-001", "Elevator", 320000, "14 20 00"),
    ]

    sov_items = []
    total_sov = Decimal("0")
    for i, (item_no, desc, val, csi) in enumerate(sov_data):
        sov = ScheduleOfValues(
            id=_uid(f"sov-{item_no}"),
            project_id=project.id,
            item_number=item_no,
            description=desc,
            scheduled_value=Decimal(str(val)),
            csi_code=csi,
            sort_order=i,
        )
        s.add(sov)
        sov_items.append(sov)
        total_sov += Decimal(str(val))

    await s.flush()

    # --- Change Orders ---
    co_data = [
        (
            "PCO-001",
            "High Water Table — Additional Dewatering",
            "field_condition",
            45000,
            3,
            "approved",
        ),
        ("PCO-002", "Structural Beam Upgrade — Grid C3-C5", "design_error", 32000, 0, "approved"),
        ("PCO-003", "Fire Code Compliance — Added Dampers", "regulatory", 18000, 2, "approved"),
        (
            "PCO-004",
            "Curtain Wall Revision — Architect Change",
            "owner_directed",
            28000,
            5,
            "pending_review",
        ),
        (
            "PCO-005",
            "Additional Fire Stopping — Floors 2-4",
            "regulatory",
            15000,
            0,
            "pending_review",
        ),
    ]

    pcos = []
    cors = []
    cos_approved = []
    for i, (_pco_num, title, change_type, cost, sched_days, pco_status) in enumerate(co_data):
        pco = PotentialChangeOrder(
            id=_uid(f"pco-{i}"),
            project_id=project.id,
            pco_number=i + 1,
            title=title,
            description=f"Change order for {title.lower()}. See attached documentation.",
            change_type=change_type,
            status=pco_status,
            originated_by=users[1].id,
            reviewed_by=users[0].id if pco_status == "approved" else None,
            labor_cost=Decimal(str(int(cost * 0.35))),
            material_cost=Decimal(str(int(cost * 0.45))),
            equipment_cost=Decimal(str(int(cost * 0.10))),
            subcontractor_cost=Decimal("0"),
            overhead_cost=Decimal(str(int(cost * 0.05))),
            profit_markup_pct=Decimal("5.00"),
            total_cost=Decimal(str(cost)),
            schedule_impact_days=sched_days,
        )
        s.add(pco)
        pcos.append(pco)

        if pco_status == "approved":
            cor = ChangeOrderRequest(
                id=_uid(f"cor-{i}"),
                project_id=project.id,
                cor_number=i + 1,
                title=title,
                description=pco.description,
                status="approved",
                markup_pct=Decimal("5.00"),
                overhead_pct=Decimal("5.00"),
                total_cost=Decimal(str(cost)),
                schedule_impact_days=sched_days,
                submitted_to=users[3].id,
                approved_by=users[3].id,
                submitted_at=_ts(PROJECT_START + timedelta(days=30 + i * 20)),
                approved_at=_ts(PROJECT_START + timedelta(days=37 + i * 20)),
            )
            s.add(cor)
            cors.append(cor)

            s.add(CORPCOLink(id=_uid(f"link-{i}"), cor_id=cor.id, pco_id=pco.id))

            co = ChangeOrder(
                id=_uid(f"co-{i}"),
                project_id=project.id,
                co_number=str(i + 1),
                title=title,
                description=pco.description,
                status="executed",
                change_type=change_type,
                requested_by=users[1].id,
                cost_impact=Decimal(str(cost)),
                schedule_impact_days=sched_days,
                cor_id=cor.id,
                approved_date=cor.approved_at,
                executed_date=_ts(PROJECT_START + timedelta(days=40 + i * 20)),
                this_co_amount=Decimal(str(cost)),
            )
            s.add(co)
            cos_approved.append(co)

    await s.flush()
    net_cos = sum(co.cost_impact for co in cos_approved)

    # --- Pay Applications (4 monthly) ---
    contract_sum = Decimal("12500000.00")
    retainage_pct = Decimal("10.00")

    # Progress percentages per SOV item for each pay app period
    # Simplified: assign progress based on CSI division and month
    pay_app_progress = [
        # Month 1: mobilization + earthwork start
        {"01": 0.20, "02": 0.10, "31": 0.05},
        # Month 2: foundations + concrete start
        {"01": 0.35, "02": 0.60, "03": 0.15, "31": 0.10, "07": 0.05},
        # Month 3: structure + steel
        {"01": 0.50, "02": 0.90, "03": 0.40, "05": 0.30, "07": 0.10, "31": 0.15},
        # Month 4: structure cont + envelope start
        {
            "01": 0.65,
            "02": 1.00,
            "03": 0.55,
            "05": 0.50,
            "07": 0.15,
            "08": 0.08,
            "21": 0.05,
            "22": 0.05,
            "23": 0.03,
            "26": 0.03,
            "31": 0.20,
        },
    ]

    prev_cert_total = Decimal("0")
    for app_num in range(1, 5):
        period_end = PROJECT_START + timedelta(days=30 * app_num)
        progress = pay_app_progress[app_num - 1]

        pa = PayApplication(
            id=_uid(f"payapp-{app_num}"),
            project_id=project.id,
            application_number=app_num,
            period_to=period_end,
            contractor_info={
                "company": "Metro Center Development Group",
                "address": "100 Kirk Ave, Roanoke VA 24011",
            },
            architect_info={
                "company": "Wilson Design Associates",
                "address": "310 1st St SW, Roanoke VA 24011",
            },
            original_contract_sum=contract_sum,
            net_change_by_cos=net_cos if app_num >= 3 else Decimal("0"),
            contract_sum_to_date=contract_sum + (net_cos if app_num >= 3 else Decimal("0")),
            retainage_pct=retainage_pct,
            status="certified" if app_num <= 3 else "submitted",
            submitted_by=users[0].id,
            certified_by=users[3].id if app_num <= 3 else None,
            submitted_at=_ts(period_end + timedelta(days=2)),
            certified_at=_ts(period_end + timedelta(days=8)) if app_num <= 3 else None,
            paid_at=_ts(period_end + timedelta(days=25)) if app_num <= 3 else None,
        )

        line_items = []
        total_completed = Decimal("0")
        for sov in sov_items:
            csi_div = sov.csi_code[:2] if sov.csi_code else "01"
            pct_complete = Decimal(str(progress.get(csi_div, 0)))
            this_total = (sov.scheduled_value * pct_complete).quantize(Decimal("0.01"))

            # Calculate previous from prior pay apps
            prev_pct = Decimal("0")
            if app_num > 1:
                prev_progress = pay_app_progress[app_num - 2]
                prev_pct = Decimal(str(prev_progress.get(csi_div, 0)))
            prev_total = (sov.scheduled_value * prev_pct).quantize(Decimal("0.01"))
            this_period = max(Decimal("0"), this_total - prev_total)

            li = PayApplicationLineItem(
                id=_uid(f"pali-{app_num}-{sov.item_number}"),
                pay_application_id=pa.id,
                sov_id=sov.id,
                item_number=sov.item_number,
                description_of_work=sov.description,
                scheduled_value=sov.scheduled_value,
                work_completed_previous=prev_total,
                work_completed_this_period=this_period,
                materials_presently_stored=Decimal("0"),
                total_completed_and_stored=this_total,
                percent_complete=(this_total / sov.scheduled_value * 100).quantize(Decimal("0.01"))
                if sov.scheduled_value > 0
                else Decimal("0"),
                balance_to_finish=sov.scheduled_value - this_total,
                retainage_pct=retainage_pct,
                sort_order=sov.sort_order,
            )
            line_items.append(li)
            total_completed += this_total

        ret_work = (total_completed * retainage_pct / 100).quantize(Decimal("0.01"))
        pa.total_completed_and_stored = total_completed
        pa.retainage_work_completed = ret_work
        pa.retainage_stored_materials = Decimal("0")
        pa.total_retainage = ret_work
        pa.total_earned_less_retainage = total_completed - ret_work
        pa.less_previous_certificates = prev_cert_total
        pa.current_payment_due = total_completed - ret_work - prev_cert_total
        pa.balance_to_finish_including_retainage = (
            pa.contract_sum_to_date - total_completed + ret_work
        )

        s.add(pa)
        for li in line_items:
            s.add(li)

        prev_cert_total = total_completed - ret_work

    await s.flush()

    # --- EVM Snapshots (5 monthly) ---
    bac = float(contract_sum + net_cos)
    for month in range(1, 6):
        snap_date = PROJECT_START + timedelta(days=30 * month)
        planned_pct = month / 14.0  # linear plan over 14 months
        pv = bac * planned_pct
        # SPI=0.88 means EV = 0.88 * PV
        ev = pv * (0.92 - month * 0.01)  # declining SPI
        # CPI=0.94 means AC = EV / 0.94
        ac = ev / (0.96 - month * 0.005)  # slightly worsening CPI
        sv = ev - pv
        cv = ev - ac
        spi = ev / pv if pv > 0 else 1.0
        cpi = ev / ac if ac > 0 else 1.0
        eac = bac / cpi
        etc = eac - ac
        vac = bac - eac
        tcpi = (bac - ev) / (bac - ac) if (bac - ac) > 0 else 1.0
        pct = (ev / bac * 100) if bac > 0 else 0

        snap = EVMSnapshot(
            id=_uid(f"evm-{month}"),
            project_id=project.id,
            snapshot_date=snap_date,
            data_date=snap_date,
            bac=Decimal(str(round(bac, 2))),
            pv=Decimal(str(round(pv, 2))),
            ev=Decimal(str(round(ev, 2))),
            ac=Decimal(str(round(ac, 2))),
            sv=Decimal(str(round(sv, 2))),
            cv=Decimal(str(round(cv, 2))),
            spi=Decimal(str(round(spi, 4))),
            cpi=Decimal(str(round(cpi, 4))),
            eac=Decimal(str(round(eac, 2))),
            etc=Decimal(str(round(etc, 2))),
            vac=Decimal(str(round(vac, 2))),
            tcpi=Decimal(str(round(tcpi, 4))),
            percent_complete=Decimal(str(round(pct, 2))),
        )
        s.add(snap)

    await s.flush()
    logger.info(
        "Created %d SOV items, %d PCOs, %d COs, 4 pay apps, 5 EVM snapshots",
        len(sov_items),
        len(pcos),
        len(cos_approved),
    )


# ---------------------------------------------------------------------------
# 5. RFIs
# ---------------------------------------------------------------------------
async def create_rfis(s: AsyncSession, project, users):
    rfi_defs = [
        # (subject, question, status, priority, days_ago, spec, drawing, answer)
        (
            "Concrete Mix Design — 5000 PSI",
            "Spec calls for 5000 PSI at 28 days. Can we use Type III cement for faster strength gain?",
            "closed",
            "high",
            140,
            "03 30 00",
            "S-201",
            "Type III is acceptable per ACI 301. Submit revised mix design for approval.",
        ),
        (
            "Rebar Spacing — Foundation Walls",
            "Drawing S-201 shows #5@12 OC but spec table calls for #5@10 OC. Which governs?",
            "closed",
            "high",
            135,
            "03 20 00",
            "S-201",
            "Spec table governs. Use #5@10 OC EF.",
        ),
        (
            "Concrete Cure Time — Cold Weather",
            "Can we reduce cure time from 7 to 5 days given the use of insulated blankets?",
            "closed",
            "normal",
            130,
            "03 30 00",
            None,
            "Maintain 7-day cure per ACI 306. Document temperatures per cold weather plan.",
        ),
        (
            "Waterproofing Termination Detail",
            "Detail 5/S-101 does not show termination at grade. Provide detail.",
            "closed",
            "normal",
            125,
            "07 10 00",
            "S-101",
            'See attached SK-001 for termination detail. Lap min 6" above finish grade.',
        ),
        (
            "Structural Steel Connection — Grid C3",
            "Connection detail at C3-C5 intersection unclear. Verify moment connection vs shear tab.",
            "closed",
            "critical",
            120,
            "05 12 00",
            "S-301",
            "Moment connection required per structural engineer. See revised detail 3/S-301.",
        ),
        (
            "Column Base Plate Anchor Bolts",
            'Base plate at grid B2 shows (4) 1" anchors but calculation requires (6). Clarify.',
            "closed",
            "high",
            115,
            "05 12 00",
            "S-201",
            'Use (6) 1" F1554 Gr 36 anchors. Updated detail attached.',
        ),
        (
            "Steel Erection Sequence",
            "Can we erect grid lines A-C before D-F to allow MEP rough-in to start?",
            "closed",
            "normal",
            110,
            "05 12 00",
            None,
            "Approved. Maintain temporary bracing per erection plan until full connection.",
        ),
        (
            "Curtain Wall Anchor Spacing",
            'Spec shows 24" OC max but manufacturer recommends 30" OC. Request variance.',
            "closed",
            "high",
            95,
            "08 44 00",
            "A-301",
            'Maintain 24" OC per spec. No variance granted for wind load requirements.',
        ),
        (
            "Curtain Wall Sealant Color",
            "Spec says 'match adjacent surface' but curtain wall and precast have different colors.",
            "closed",
            "normal",
            90,
            "07 92 00",
            "A-301",
            "Use color to match curtain wall mullion. Submit sample for architect review.",
        ),
        (
            "Curtain Wall Mullion Alignment",
            'Mullion centerlines are 1/4" off from column grid. Acceptable tolerance?',
            "closed",
            "normal",
            85,
            "08 44 00",
            "A-201",
            'Per AAMA CW-DG-1, 1/4" is within tolerance. Document as-built locations.',
        ),
        (
            "HVAC Duct Routing — Basement",
            "Supply duct conflicts with structural beam at grid D3. Request routing change.",
            "closed",
            "high",
            80,
            "23 31 00",
            "M-201",
            "Route duct below beam with transition fitting. Maintain min 7'-6\" clearance.",
        ),
        (
            "Electrical Conduit Penetration — Fire Wall",
            'Need additional 4" penetrations through 2-hr fire wall at basement. Approve firestop detail.',
            "closed",
            "normal",
            75,
            "26 00 00",
            "E-201",
            "Approved. Use 3M CP 25WB+ firestop per UL W-L-7079. Submit firestop log.",
        ),
        (
            "Fire Damper Location — Floor 1",
            "Drawing M-201 shows damper in wall but duct runs through floor. Clarify.",
            "closed",
            "high",
            70,
            "21 13 00",
            "M-201",
            "Install fire damper at floor penetration. See revised coordination drawing.",
        ),
        (
            "Ceiling Height — Lobby",
            "Arch drawings show 11'-0\" ACT but structural clearance is only 10'-6\". Advise.",
            "closed",
            "normal",
            65,
            "09 51 00",
            "A-102",
            "Revise to 10'-6\" ACT. Coordinate with MEP for above-ceiling routing.",
        ),
        (
            "Floor Finish Transition — Carpet to Tile",
            "No detail for transition strip at carpet/tile change in corridor. Provide.",
            "closed",
            "normal",
            60,
            "09 68 00",
            "A-201",
            "Use Schluter RENO-T in brushed nickel. See detail SK-015.",
        ),
        (
            "Egress Width — Stair B",
            'Stair B measures 42" clear width but code requires 44" min for occupant load. Confirm.',
            "open",
            "critical",
            40,
            "01 10 00",
            "A-102",
            None,
        ),
        (
            "ADA Compliance — Restroom Layout",
            "Floor 2 men's room does not meet ADA turning radius. Request revised layout.",
            "open",
            "high",
            35,
            "01 10 00",
            "A-202",
            None,
        ),
        (
            "Fire Rating — Shaft Wall",
            "Shaft wall assembly specified is UL U465 but U419 is required for elevator shaft. Confirm.",
            "open",
            "high",
            30,
            "07 81 00",
            "A-301",
            None,
        ),
        (
            "Electrical Panel Schedule — Floor 3",
            "Panel 3A schedule shows 42 circuits but panel is rated for 30. Clarify.",
            "open",
            "normal",
            25,
            "26 00 00",
            "E-301",
            None,
        ),
        (
            "HVAC Condensate Drain Routing",
            "No routing shown for AHU condensate drain. Confirm connection to nearest floor drain.",
            "open",
            "normal",
            20,
            "23 00 00",
            "M-301",
            None,
        ),
        (
            "Structural Loading — Rooftop Units",
            "RTU weight exceeds design roof live load at grid E5. Verify structural adequacy.",
            "pending",
            "critical",
            15,
            "05 12 00",
            "S-401",
            None,
        ),
        (
            "Parking Lot Drainage",
            "Site civil shows 2% slope but survey indicates 1.2% at NW corner. Request regrading.",
            "pending",
            "normal",
            10,
            "32 10 00",
            None,
            None,
        ),
        (
            "Elevator Cab Finish",
            "Owner requests upgrade from standard laminate to stainless steel cab interior. PCO?",
            "pending",
            "normal",
            5,
            "14 20 00",
            None,
            None,
        ),
        (
            "Switchgear Delivery Delay",
            "Vendor confirms 3-week delay on main switchgear. Impact to schedule?",
            "open",
            "critical",
            45,
            "26 00 00",
            None,
            None,
        ),
        (
            "Concrete Pour Sequence — Floor 4",
            "Can we pour east half of floor 4 deck before west half shoring is complete?",
            "open",
            "high",
            42,
            "03 30 00",
            "S-401",
            None,
        ),
    ]

    rfis = []
    for i, (subj, question, stat, priority, days_ago, spec, dwg, answer) in enumerate(rfi_defs):
        created = TODAY - timedelta(days=days_ago)
        rfi_num = f"RFI-{i + 1:03d}"

        due = created + timedelta(
            days={"critical": 3, "high": 7, "normal": 14, "low": 21}[priority]
        )

        rfi = RFI(
            id=_uid(f"rfi-{i}"),
            project_id=project.id,
            rfi_number=rfi_num,
            subject=subj,
            question=question,
            status=stat,
            priority=priority,
            submitted_by=users[random.randint(1, 4)].id,
            assigned_to=users[3].id,
            due_date=due,
            answer=answer,
            response=answer,
            responded_at=_ts(due - timedelta(days=1)) if answer else None,
            spec_section=spec,
            drawing_reference=dwg,
            ball_in_court=users[3].id if stat == "open" else None,
            cost_impact=i in (0, 4, 7),
            schedule_impact=i in (4, 7, 23),
            distribution_list=[],
        )
        s.add(rfi)
        rfis.append(rfi)

        if answer:
            s.add(
                RfiResponse(
                    id=_uid(f"rfi-resp-{i}"),
                    rfi_id=rfi.id,
                    responder_id=users[3].id,
                    response_text=answer,
                    status="accepted",
                    responded_at=rfi.responded_at,
                )
            )

    await s.flush()

    # AI-resolved RFI logs
    # RFI #3 — concrete cure time: unnecessary (answer in spec)
    s.add(
        RfiResolutionLog(
            id=_uid("rfi-log-0"),
            rfi_id=rfis[2].id,
            project_id=project.id,
            stage_reached=1,
            was_unnecessary=True,
            unnecessary_source="spec",
            unnecessary_reason="Answer found in Specification Section 03 30 00, Part 3.5 — Cold Weather Concreting: 'Maintain minimum curing temperature of 50F for 7 days using insulated blankets or heated enclosures.'",
            similar_rfi_count=0,
            is_safety_related=False,
        )
    )

    # RFI #11 — duct routing: AI drafted with verification
    s.add(
        RfiResolutionLog(
            id=_uid("rfi-log-1"),
            rfi_id=rfis[10].id,
            project_id=project.id,
            stage_reached=3,
            was_unnecessary=False,
            draft_confidence=0.87,
            draft_model="gpt-4o",
            draft_source_count=4,
            is_safety_related=False,
            hallucination_count=0,
            contradiction_count=0,
            completeness_issues=0,
            verification_passed=True,
            human_accepted_draft=True,
            human_edit_distance=23,
            time_to_resolution_hours=0.5,
            traditional_avg_hours=48.0,
        )
    )

    await s.flush()
    logger.info("Created %d RFIs + 2 resolution logs", len(rfis))


# ---------------------------------------------------------------------------
# 6. Submittals
# ---------------------------------------------------------------------------
async def create_submittals(s: AsyncSession, project, users):
    sub_defs = [
        # (title, spec, type, status, days_ago)
        ("Concrete Mix Design — 5000 PSI", "03 30 00", "product_data", "approved", 140),
        ("Rebar Mill Certificates", "03 20 00", "certificate", "approved", 135),
        ("Structural Steel Shop Drawings", "05 12 00", "shop_drawing", "approved", 130),
        ("Anchor Bolt Layout", "05 12 00", "shop_drawing", "approved", 125),
        ("Waterproofing Product Data", "07 10 00", "product_data", "approved", 120),
        ("Roofing Membrane — TPO", "07 54 00", "product_data", "approved", 110),
        ("Curtain Wall Shop Drawings", "08 44 00", "shop_drawing", "approved_as_noted", 100),
        ("Curtain Wall Mock-Up Test Report", "08 44 00", "test_report", "approved", 95),
        ("Fire Damper Schedule", "21 13 00", "schedule", "approved_as_noted", 90),
        ("Sprinkler System Layout", "21 13 00", "shop_drawing", "approved_as_noted", 85),
        ("HVAC Equipment Submittals — AHU", "23 00 00", "product_data", "approved_as_noted", 80),
        ("Ductwork Shop Drawings", "23 31 00", "shop_drawing", "approved_as_noted", 75),
        ("BAS Control Sequences", "23 09 00", "product_data", "revise_and_resubmit", 70),
        ("Plumbing Fixture Schedule", "22 00 00", "schedule", "revise_and_resubmit", 65),
        ("Electrical Switchgear", "26 00 00", "shop_drawing", "revise_and_resubmit", 60),
        ("Lighting Fixture Schedule", "26 51 00", "schedule", "revise_and_resubmit", 55),
        ("Door Hardware Schedule", "08 71 00", "schedule", "pending_review", 45),
        ("Acoustical Ceiling Data", "09 51 00", "product_data", "pending_review", 40),
        ("Paint Color Schedule", "09 91 00", "sample", "pending_review", 35),
        ("Carpet & LVT Samples", "09 68 00", "sample", "pending_review", 30),
        ("Ceramic Tile Samples", "09 30 00", "sample", "pending_review", 25),
        ("Elevator Shop Drawings", "14 20 00", "shop_drawing", "pending_review", 20),
        ("Fire Alarm Devices", "28 31 00", "product_data", "not_submitted", 15),
        ("Low Voltage Backbone", "27 00 00", "shop_drawing", "not_submitted", 10),
        ("Site Lighting Fixtures", "26 56 00", "product_data", "not_submitted", 5),
        ("Landscaping Plan", "32 90 00", "shop_drawing", "not_submitted", 3),
        ("Commissioning Plan", "01 91 00", "other", "closed", 100),
        ("Quality Control Plan", "01 45 00", "other", "closed", 130),
        ("Erosion Control Plan", "31 25 00", "other", "closed", 145),
        ("Safety & Health Plan", "01 35 00", "other", "closed", 148),
    ]

    for i, (title, spec, sub_type, status, days_ago) in enumerate(sub_defs):
        created = TODAY - timedelta(days=days_ago)
        sub = Submittal(
            id=_uid(f"sub-{i}"),
            project_id=project.id,
            submittal_number=f"SUB-{i + 1:03d}",
            title=title,
            spec_section=spec,
            spec_section_name=title.split("—")[0].strip() if "—" in title else title,
            submittal_type=sub_type,
            status=status,
            priority="high" if i < 8 else "normal",
            submitted_by=users[4].id if status != "not_submitted" else None,
            reviewer_id=users[3].id,
            current_reviewer=users[3].id if status == "pending_review" else None,
            ball_in_court=users[3].id
            if status == "pending_review"
            else (users[4].id if status == "revise_and_resubmit" else None),
            revision_number=1 if status == "revise_and_resubmit" else 0,
            date_submitted=created if status != "not_submitted" else None,
            date_returned=created + timedelta(days=14)
            if status in ("approved", "approved_as_noted", "revise_and_resubmit", "closed")
            else None,
        )
        s.add(sub)

        if status in ("approved", "approved_as_noted", "revise_and_resubmit", "closed"):
            s.add(
                SubmittalReview(
                    id=_uid(f"sub-review-{i}"),
                    submittal_id=sub.id,
                    reviewer_id=users[3].id,
                    review_action=status if status != "closed" else "approved",
                    comments=f"Reviewed and {status.replace('_', ' ')}."
                    if status != "closed"
                    else "Accepted.",
                    revision_number=0,
                    reviewed_at=_ts(created + timedelta(days=10)),
                )
            )

    await s.flush()
    logger.info("Created 30 submittals")


# ---------------------------------------------------------------------------
# 7. Daily Logs
# ---------------------------------------------------------------------------
async def create_daily_logs(s: AsyncSession, project, users):
    work_days = _workdays(PROJECT_START, TODAY - timedelta(days=1))
    total_days = len(work_days)

    trade_progression = [
        (0.0, ["laborers", "operators"]),
        (0.05, ["laborers", "operators", "carpenters"]),
        (0.12, ["laborers", "operators", "carpenters", "concrete_finishers", "iron_workers"]),
        (
            0.25,
            [
                "laborers",
                "operators",
                "carpenters",
                "concrete_finishers",
                "iron_workers",
                "structural_steel",
            ],
        ),
        (
            0.40,
            [
                "laborers",
                "operators",
                "carpenters",
                "iron_workers",
                "structural_steel",
                "electricians",
                "plumbers",
            ],
        ),
        (0.55, ["laborers", "carpenters", "electricians", "plumbers", "hvac", "sprinkler_fitters"]),
        (
            0.70,
            ["laborers", "carpenters", "electricians", "plumbers", "hvac", "drywall", "painters"],
        ),
    ]

    narratives_by_phase = {
        "mob": [
            "Site clearing and grubbing in progress.",
            "Erosion control measures installed.",
            "Temporary power and water connected.",
        ],
        "found": [
            "Foundation excavation continued.",
            "Foundation wall forms set on grid lines A-C.",
            "Concrete pour completed for spread footings.",
            "Rebar placement for foundation walls.",
            "Waterproofing applied to foundation walls.",
        ],
        "struct": [
            "Column forms stripped and inspected.",
            "Structural steel erection — grid D-F.",
            "Concrete deck pour — {floor}.",
            "Post-tension stressing completed — {floor}.",
            "Shoring removed after reaching design strength.",
        ],
        "envelope": [
            "Curtain wall anchors installed — {floor}.",
            "Curtain wall panels set — south elevation.",
            "Roof membrane installation continued.",
            "Exterior sealant application — basement level.",
        ],
        "mep": [
            "HVAC ductwork rough-in — {floor}.",
            "Electrical conduit installation — {floor}.",
            "Plumbing rough-in — risers and horizontals.",
            "Fire sprinkler main runs installed.",
        ],
    }

    equipment_options = [
        {
            "equipment_type": "excavator",
            "equipment_id": "CAT-336",
            "hours_used": 8,
            "notes": "Mass excavation",
        },
        {
            "equipment_type": "crane",
            "equipment_id": "LIEB-LTM1100",
            "hours_used": 8,
            "notes": "Material hoisting",
        },
        {
            "equipment_type": "concrete_pump",
            "equipment_id": "PUTZ-52M",
            "hours_used": 6,
            "notes": "Concrete placement",
        },
        {
            "equipment_type": "forklift",
            "equipment_id": "CAT-TH255C",
            "hours_used": 8,
            "notes": "Material handling",
        },
    ]

    logs = []
    for day_idx, d in enumerate(work_days):
        rng = random.Random(d.toordinal())
        progress = day_idx / max(total_days, 1)
        wx = _weather(d)

        # Crew ramp: 15 → 65
        base_crew = int(15 + progress * 50)
        # Reduce crew on bad weather days
        if wx["conditions"] in ("rain", "snow"):
            base_crew = int(base_crew * 0.7)

        # Select phase
        if progress < 0.08:
            phase = "mob"
        elif progress < 0.25:
            phase = "found"
        elif progress < 0.55:
            phase = "struct"
        elif progress < 0.70:
            phase = "envelope"
        else:
            phase = "mep"

        # Select narrative
        narr_list = narratives_by_phase[phase]
        narr = rng.choice(narr_list).format(
            floor=rng.choice(["basement", "1st floor", "2nd floor", "3rd floor"])
        )

        # Select active trades
        active_trades = ["laborers"]
        for threshold, trades in trade_progression:
            if progress >= threshold:
                active_trades = trades
        manpower = []
        remaining = base_crew
        for trade in active_trades:
            if trade == active_trades[-1]:
                count = remaining
            else:
                count = max(1, int(remaining / len(active_trades) * rng.uniform(0.7, 1.3)))
                remaining -= count
            manpower.append({"trade": trade, "headcount": max(1, count), "hours": 8})

        # Equipment
        equip = []
        if progress < 0.20:
            equip.append(equipment_options[0])  # excavator
        if progress > 0.10:
            equip.append(equipment_options[1])  # crane
        if 0.10 < progress < 0.60:
            equip.append(equipment_options[2])  # concrete pump
        equip.append(equipment_options[3])  # forklift always

        weather_delay = Decimal("0")
        if wx["conditions"] == "snow" and wx["precipitation_inches"] > 2:
            weather_delay = Decimal("4")
        elif wx["conditions"] == "rain" and wx["precipitation_inches"] > 0.5:
            weather_delay = Decimal("2")

        log = DailyLog(
            id=_uid(f"dlog-{d.isoformat()}"),
            project_id=project.id,
            log_date=d,
            weather=wx,
            crew_count=base_crew,
            work_hours=Decimal(str(max(0, 8 - float(weather_delay)))),
            work_narrative=narr,
            manpower_by_trade=manpower,
            equipment_entries=equip,
            deliveries=[],
            visitors=[],
            photos=[],
            activities_completed=[],
            delays=[
                {"reason": f"Weather delay — {wx['conditions']}", "hours": float(weather_delay)}
            ]
            if weather_delay > 0
            else [],
            notes=None,
            status="approved",
            safety_topic_discussed=SAFETY_TOPICS[day_idx % len(SAFETY_TOPICS)],
            weather_delay_hours=weather_delay,
            created_by=users[1].id,
            approved_by=users[0].id,
            approved_at=_ts(d + timedelta(days=1)),
        )
        s.add(log)
        logs.append(log)

    await s.flush()
    logger.info("Created %d daily logs", len(logs))


# ---------------------------------------------------------------------------
# 8. Drawings
# ---------------------------------------------------------------------------
async def create_drawings(s: AsyncSession, project, users):
    sets_data = [
        ("Architectural Plans", "A", "Architectural design drawings"),
        ("Structural Plans", "S", "Structural engineering drawings"),
        ("Mechanical Plans", "M", "HVAC and mechanical drawings"),
        ("Electrical Plans", "E", "Electrical power and lighting drawings"),
        ("Plumbing Plans", "P", "Plumbing and fire protection drawings"),
    ]

    sheets = {
        "A": [
            ("A-101", "Floor Plan — Basement"),
            ("A-102", "Floor Plan — 1st Floor"),
            ("A-201", "Floor Plan — 2nd Floor"),
            ("A-202", "Floor Plan — 3rd Floor"),
            ("A-301", "Building Sections"),
        ],
        "S": [
            ("S-101", "Foundation Plan"),
            ("S-201", "Framing Plan — Basement/1st"),
            ("S-301", "Framing Plan — 2nd/3rd"),
            ("S-401", "Framing Plan — 4th/Roof"),
        ],
        "M": [
            ("M-101", "HVAC Plan — Basement"),
            ("M-201", "HVAC Plan — 1st Floor"),
            ("M-301", "HVAC Plan — 2nd Floor"),
            ("M-401", "HVAC Plan — 3rd/4th Floor"),
        ],
        "E": [
            ("E-101", "Electrical Plan — Basement"),
            ("E-201", "Electrical Plan — 1st Floor"),
            ("E-301", "Electrical Plan — 2nd/3rd Floor"),
        ],
        "P": [
            ("P-101", "Plumbing Plan — Basement"),
            ("P-201", "Plumbing Plan — 1st Floor"),
            ("P-301", "Plumbing Risers & Details"),
        ],
    }

    for set_name, disc, desc in sets_data:
        ds = DrawingSet(
            id=_uid(f"dset-{disc}"),
            project_id=project.id,
            name=set_name,
            discipline=disc,
            description=desc,
            created_by=users[0].id,
        )
        s.add(ds)
        await s.flush()

        for sheet_no, title in sheets[disc]:
            drawing = Drawing(
                id=_uid(f"dwg-{sheet_no}"),
                drawing_set_id=ds.id,
                project_id=project.id,
                sheet_number=sheet_no,
                title=title,
                discipline=disc,
                status="active",
            )
            s.add(drawing)
            await s.flush()

            rev = DrawingRevision(
                id=_uid(f"rev-{sheet_no}"),
                drawing_id=drawing.id,
                revision_number=0,
                s3_key=f"drawings/{project.id}/{ds.id}/{drawing.id}/rev_0.pdf",
                original_filename=f"{sheet_no}.pdf",
                file_size_bytes=random.randint(500000, 5000000),
                content_hash=uuid.uuid4().hex,
                status="current",
                uploaded_by=users[0].id,
            )
            s.add(rev)
            await s.flush()

            drawing.current_revision_id = rev.id

    await s.flush()
    logger.info("Created 5 drawing sets, 20 drawings")


# ---------------------------------------------------------------------------
# 9. Meetings
# ---------------------------------------------------------------------------
async def create_meetings(s: AsyncSession, project, users):
    attendees_template = [
        {
            "name": "Sarah Chen",
            "company": "Metro Center Development Group",
            "role": "Project Manager",
        },
        {
            "name": "Mike Rodriguez",
            "company": "Metro Center Development Group",
            "role": "Superintendent",
        },
        {"name": "James Wilson", "company": "Wilson Design Associates", "role": "Architect"},
        {"name": "Tom Harris", "company": "Roanoke Properties LLC", "role": "Owner Representative"},
        {
            "name": "Amy Nguyen",
            "company": "Metro Center Development Group",
            "role": "MEP Coordinator",
        },
    ]

    for month in range(10):
        meeting_date = PROJECT_START + timedelta(days=14 + month * 30)
        if meeting_date > TODAY:
            break

        is_past = (TODAY - meeting_date).days > 14
        topics = [
            {
                "topic": "Schedule Update",
                "discussion": f"Project is {'on track' if month < 3 else 'approximately 2 weeks behind baseline'}. Critical path through {'foundations' if month < 2 else 'structure and MEP'}.",
                "decision": None,
                "action_item": None,
            },
            {
                "topic": "Budget Review",
                "discussion": f"Total billed to date: ${580000 + month * 800000:,.0f}. {'No' if month < 2 else str(min(month, 3))} change orders processed.",
                "decision": None,
                "action_item": None,
            },
            {
                "topic": "RFI/Submittal Status",
                "discussion": f"{min(25, 3 + month * 3)} RFIs issued, {min(month * 2, 15)} closed. {min(30, 5 + month * 3)} submittals in process.",
                "decision": None,
                "action_item": None,
            },
            {
                "topic": "Safety Report",
                "discussion": "Zero recordable incidents. Weekly toolbox talks ongoing.",
                "decision": None,
                "action_item": None,
            },
            {
                "topic": "Upcoming Milestones",
                "discussion": f"Next milestone: {'Foundation completion' if month < 2 else 'Structure topping out' if month < 5 else 'Building dry-in'}.",
                "decision": None,
                "action_item": None,
            },
        ]

        action_items = [
            {
                "title": f"Submit updated {'3-week look-ahead' if month % 2 == 0 else 'CPM schedule'}",
                "responsible_party": "Sarah Chen",
                "due_date": (meeting_date + timedelta(days=7)).isoformat(),
                "status": "completed" if is_past else "pending",
            },
            {
                "title": "Resolve outstanding RFIs with architect",
                "responsible_party": "James Wilson",
                "due_date": (meeting_date + timedelta(days=14)).isoformat(),
                "status": "completed" if is_past else "pending",
            },
        ]
        if month >= 3:
            action_items.append(
                {
                    "title": "Submit recovery schedule for weather delays",
                    "responsible_party": "Sarah Chen",
                    "due_date": (meeting_date + timedelta(days=10)).isoformat(),
                    "status": "completed" if is_past else "pending",
                }
            )

        mm = MeetingMinutes(
            id=_uid(f"meeting-{month}"),
            project_id=project.id,
            meeting_type="progress",
            meeting_date=meeting_date,
            title=f"OAC Progress Meeting #{month + 1}",
            attendees=attendees_template,
            meeting_location="Project Site Trailer",
            start_time=time(10, 0),
            end_time=time(11, 30),
            agenda_items=topics,
            action_items=action_items,
            decisions=[],
            notes=f"Monthly OAC meeting #{month + 1}. All parties present.",
            status="finalized" if is_past else "draft",
        )
        s.add(mm)

    await s.flush()
    logger.info("Created meeting minutes")


# ---------------------------------------------------------------------------
# 10. Punch List
# ---------------------------------------------------------------------------
async def create_punch_list(s: AsyncSession, project, users):
    pl = PunchList(
        id=_uid("punchlist"),
        project_id=project.id,
        name="Pre-Drywall Walkthrough — Basement & Floors 1-2",
        description="Walkthrough conducted to verify MEP rough-in, framing, fire stopping, and waterproofing before drywall close-in.",
        walk_date=TODAY - timedelta(days=10),
        status="open",
        participants=[
            {"name": "Sarah Chen", "role": "PM"},
            {"name": "Mike Rodriguez", "role": "Superintendent"},
            {"name": "Lisa Park", "role": "Safety Manager"},
            {"name": "James Wilson", "role": "Architect"},
        ],
        created_by=users[0].id,
    )
    s.add(pl)

    items_data = [
        # (description, location, category, priority, status, company)
        (
            "Missing firestop at electrical penetration",
            "Basement — Grid B3",
            "fire_stopping",
            "high",
            "open",
            "ABC Electric",
        ),
        (
            "Incomplete firestop at plumbing riser",
            "Basement — Grid C2",
            "fire_stopping",
            "high",
            "open",
            "RPC Plumbing",
        ),
        (
            "Firestop gap at HVAC duct penetration",
            "1st Floor — Grid D4",
            "fire_stopping",
            "high",
            "in_progress",
            "Comfort Air HVAC",
        ),
        (
            "Drywall patch needed at access panel",
            "Basement — Corridor B",
            "drywall",
            "normal",
            "open",
            "Premium Drywall",
        ),
        (
            "Metal stud framing misaligned at doorway",
            "1st Floor — Room 102",
            "framing",
            "normal",
            "in_progress",
            "Premium Drywall",
        ),
        (
            "Missing backing for grab bar",
            "1st Floor — Restroom 104",
            "framing",
            "high",
            "open",
            "Premium Drywall",
        ),
        (
            "HVAC duct not sealed at connection",
            "Basement — Mechanical Room",
            "mep",
            "normal",
            "resolved",
            "Comfort Air HVAC",
        ),
        (
            "Condensate drain missing P-trap",
            "Basement — AHU Location",
            "mep",
            "high",
            "in_progress",
            "RPC Plumbing",
        ),
        (
            "Electrical junction box not covered",
            "Basement — Grid A4",
            "mep",
            "normal",
            "resolved",
            "ABC Electric",
        ),
        (
            "Sprinkler head clearance insufficient",
            "1st Floor — Corridor A",
            "fire_protection",
            "high",
            "open",
            "Valley Fire Protection",
        ),
        (
            "Missing insulation on hot water pipe",
            "Basement — Grid D2",
            "mep",
            "normal",
            "in_progress",
            "RPC Plumbing",
        ),
        (
            "Waterproofing damaged at wall penetration",
            "Basement — Grid A1",
            "waterproofing",
            "high",
            "in_progress",
            "Hydro Shield",
        ),
        (
            "Floor drain grate missing",
            "Basement — Mechanical Room",
            "mep",
            "normal",
            "resolved",
            "RPC Plumbing",
        ),
        (
            "Conduit support spacing exceeds max",
            "1st Floor — Grid C3-C5",
            "mep",
            "normal",
            "open",
            "ABC Electric",
        ),
        (
            "Fire damper installation incomplete",
            "1st Floor — Grid B2",
            "fire_protection",
            "high",
            "in_progress",
            "Valley Fire Protection",
        ),
        (
            "Plumbing vent not properly sloped",
            "2nd Floor — Grid B3",
            "mep",
            "normal",
            "open",
            "RPC Plumbing",
        ),
        (
            "HVAC diffuser size mismatch",
            "1st Floor — Room 105",
            "mep",
            "normal",
            "resolved",
            "Comfort Air HVAC",
        ),
        (
            "Grout voids at CMU wall",
            "Basement — Elevator Shaft",
            "masonry",
            "high",
            "in_progress",
            "Metro Masonry",
        ),
        (
            "Expansion joint sealant missing",
            "Basement — Grid D1",
            "waterproofing",
            "normal",
            "verified",
            "Hydro Shield",
        ),
        (
            "Light fixture box at wrong height",
            "1st Floor — Lobby",
            "mep",
            "normal",
            "open",
            "ABC Electric",
        ),
        (
            "Missing access panel for valve",
            "Basement — Grid C4",
            "mep",
            "normal",
            "in_progress",
            "RPC Plumbing",
        ),
        (
            "Ductwork dented during installation",
            "2nd Floor — Grid A2",
            "mep",
            "normal",
            "resolved",
            "Comfort Air HVAC",
        ),
        (
            "Anchor bolt missing washer",
            "1st Floor — Grid B1",
            "structural",
            "normal",
            "verified",
            "Metro Steel",
        ),
        (
            "Beam flange paint damage",
            "1st Floor — Grid C3",
            "structural",
            "normal",
            "open",
            "Metro Steel",
        ),
        (
            "Concrete spall at column base",
            "Basement — Grid D3",
            "concrete",
            "normal",
            "in_progress",
            "Metro Concrete",
        ),
        (
            "Waterproofing lap too short",
            "Basement — South Wall",
            "waterproofing",
            "high",
            "open",
            "Hydro Shield",
        ),
        (
            "Plumbing clean-out not accessible",
            "1st Floor — Corridor B",
            "mep",
            "normal",
            "verified",
            "RPC Plumbing",
        ),
        (
            "Missing fire caulk at sleeve",
            "2nd Floor — Grid C1",
            "fire_stopping",
            "high",
            "open",
            "Valley Fire Protection",
        ),
        (
            "Electrical panel clearance violation",
            "Basement — Electrical Room",
            "electrical",
            "high",
            "in_progress",
            "ABC Electric",
        ),
        (
            "Sprinkler branch line slope incorrect",
            "2nd Floor — Grid B2",
            "fire_protection",
            "normal",
            "resolved",
            "Valley Fire Protection",
        ),
        (
            "Temperature sensor location incorrect",
            "1st Floor — Room 103",
            "mep",
            "normal",
            "open",
            "BAS Controls",
        ),
        (
            "Missing sleeve at floor penetration",
            "2nd Floor — Grid D3",
            "mep",
            "normal",
            "open",
            "Metro Concrete",
        ),
        (
            "Duct insulation not continuous",
            "1st Floor — Grid A3",
            "mep",
            "normal",
            "resolved",
            "Comfort Air HVAC",
        ),
        (
            "GFI outlet missing at wet location",
            "1st Floor — Break Room",
            "electrical",
            "high",
            "in_progress",
            "ABC Electric",
        ),
        (
            "Low voltage conduit pathway blocked",
            "1st Floor — Grid C2",
            "mep",
            "normal",
            "open",
            "DataComm Systems",
        ),
        (
            "Smoke detector location conflicts with diffuser",
            "2nd Floor — Grid B4",
            "fire_protection",
            "normal",
            "verified",
            "Valley Fire Protection",
        ),
        (
            "Sheathing damage at exterior wall",
            "2nd Floor — South Elevation",
            "framing",
            "normal",
            "in_progress",
            "Premium Drywall",
        ),
        (
            "Missing J-bead at ceiling perimeter",
            "1st Floor — Room 101",
            "drywall",
            "normal",
            "open",
            "Premium Drywall",
        ),
        (
            "Concrete crack — monitor for movement",
            "Basement — SOG Grid C3",
            "concrete",
            "normal",
            "verified",
            "Metro Concrete",
        ),
        (
            "Pipe support missing at elbow",
            "Basement — Grid B4",
            "mep",
            "normal",
            "resolved",
            "RPC Plumbing",
        ),
    ]

    for i, (desc, loc, cat, pri, stat, company) in enumerate(items_data):
        item = PunchListItem(
            id=_uid(f"pli-{i}"),
            project_id=project.id,
            punch_list_id=pl.id,
            item_number=f"PL-{i + 1:03d}",
            description=desc,
            location=loc,
            category=cat,
            priority=pri,
            status=stat,
            assigned_to=users[random.Random(i).randint(1, 5)].id,
            created_by=users[0].id,
            due_date=TODAY + timedelta(days=random.Random(i).randint(3, 21)),
            company=company,
            completed_date=TODAY - timedelta(days=3) if stat in ("resolved", "verified") else None,
            verified_by=users[0].id if stat == "verified" else None,
            date_verified=_ts(TODAY - timedelta(days=1)) if stat == "verified" else None,
        )
        s.add(item)

    await s.flush()
    logger.info("Created punch list with 40 items")


# ---------------------------------------------------------------------------
# 11. Safety — Cameras, Zones, Alerts, Inspections
# ---------------------------------------------------------------------------
async def create_safety(s: AsyncSession, project, users):
    cam1 = Camera(
        id=_uid("cam-1"),
        project_id=project.id,
        name="Tower Crane Area — North",
        stream_url="rtsp://demo.constructai.dev/cam1",
        location_description="Mounted on tower crane mast, 80ft elevation",
        fps_setting=5,
        resolution="1080p",
    )
    cam2 = Camera(
        id=_uid("cam-2"),
        project_id=project.id,
        name="Main Entrance Gate",
        stream_url="rtsp://demo.constructai.dev/cam2",
        location_description="Mounted on entrance gate post, ground level",
        fps_setting=5,
        resolution="1080p",
    )
    s.add(cam1)
    s.add(cam2)
    await s.flush()

    zones = [
        SafetyZone(
            id=_uid("zone-1"),
            camera_id=cam1.id,
            project_id=project.id,
            name="Crane Swing Radius",
            zone_type="crane_swing",
            polygon_points=[
                [37.2710, -79.9414],
                [37.2712, -79.9410],
                [37.2708, -79.9408],
                [37.2706, -79.9412],
            ],
            ppe_requirements=["hard_hat", "safety_vest", "safety_glasses"],
            severity_override="red",
        ),
        SafetyZone(
            id=_uid("zone-2"),
            camera_id=cam2.id,
            project_id=project.id,
            name="Site Entrance — PPE Check",
            zone_type="ppe_required",
            polygon_points=[
                [37.2705, -79.9415],
                [37.2707, -79.9413],
                [37.2705, -79.9411],
                [37.2703, -79.9413],
            ],
            ppe_requirements=["hard_hat", "safety_vest", "steel_toe_boots"],
        ),
        SafetyZone(
            id=_uid("zone-3"),
            camera_id=cam1.id,
            project_id=project.id,
            name="Foundation Excavation Area",
            zone_type="excavation",
            polygon_points=[
                [37.2709, -79.9416],
                [37.2711, -79.9414],
                [37.2709, -79.9412],
                [37.2707, -79.9414],
            ],
            ppe_requirements=["hard_hat", "safety_vest"],
            severity_override="yellow",
        ),
    ]
    for z in zones:
        s.add(z)
    await s.flush()

    # Near-miss alerts
    alert1 = SafetyAlert(
        id=_uid("alert-1"),
        project_id=project.id,
        camera_id=cam1.id,
        zone_id=zones[0].id,
        priority="high",
        alert_type="ppe_violation",
        description="Worker detected without hard hat in crane swing radius zone.",
        detections=[{"class": "no_hard_hat", "confidence": 0.92, "bbox": [120, 80, 200, 280]}],
        confidence=Decimal("0.92"),
        is_acknowledged=True,
        acknowledged_by=users[2].id,
        acknowledged_at=_ts(TODAY - timedelta(days=18)),
        response_notes="Worker verbally warned. Issued replacement hard hat on site. Documented in safety log.",
    )
    alert2 = SafetyAlert(
        id=_uid("alert-2"),
        project_id=project.id,
        camera_id=cam1.id,
        zone_id=zones[2].id,
        priority="medium",
        alert_type="unauthorized_entry",
        description="Unauthorized vehicle detected in excavation safety zone.",
        detections=[{"class": "dump_truck", "confidence": 0.88, "bbox": [50, 100, 400, 350]}],
        confidence=Decimal("0.88"),
        is_acknowledged=True,
        acknowledged_by=users[2].id,
        acknowledged_at=_ts(TODAY - timedelta(days=8)),
        response_notes="Delivery truck entered restricted area without flagman. Driver redirected. Added signage.",
    )
    s.add(alert1)
    s.add(alert2)

    # Inspections
    insp_data = [
        ("daily", "completed", 95, TODAY - timedelta(days=3), TODAY - timedelta(days=3)),
        ("daily", "completed", 92, TODAY - timedelta(days=10), TODAY - timedelta(days=10)),
        ("milestone", "in_progress", None, TODAY - timedelta(days=1), None),
        ("concrete", "in_progress", None, TODAY - timedelta(days=2), None),
        ("rebar", "scheduled", None, TODAY + timedelta(days=3), None),
    ]
    for i, (itype, stat, score, sched, completed) in enumerate(insp_data):
        insp = Inspection(
            id=_uid(f"insp-{i}"),
            project_id=project.id,
            inspection_type=itype,
            status=stat,
            inspector_id=users[2].id,
            location="Project Site" if itype == "daily" else f"Floor {i % 3 + 1}",
            checklist_data={"items_checked": 25, "items_passed": score or 0},
            findings={} if stat != "completed" else {"observations": "All items satisfactory."},
            score=Decimal(str(score)) if score else None,
            scheduled_at=_ts(sched),
            completed_at=_ts(completed) if completed else None,
        )
        s.add(insp)

    # Daily risk score
    s.add(
        DailyRiskScore(
            id=_uid("risk-today"),
            project_id=project.id,
            score_date=TODAY,
            overall_score=35,
            category_scores={
                "fall": 40,
                "struck_by": 30,
                "excavation": 15,
                "electrical": 45,
                "heat": 25,
            },
            top_risks=[
                {
                    "category": "electrical",
                    "description": "Active electrical work on floors 1-2",
                    "score": 45,
                },
                {"category": "fall", "description": "Open deck edges on floor 4", "score": 40},
            ],
            recommended_mitigations=[
                "Verify GFCI protection on all temporary power circuits",
                "Install perimeter cable guardrail on floor 4 deck edges",
                "Conduct electrical safety toolbox talk",
            ],
            weather_factors=_weather(TODAY),
            schedule_factors={"active_phases": ["structure", "MEP_rough"], "high_activity": True},
            project_factors={"percent_complete": 35, "crew_count": 58},
            safety_briefing="Today's focus: electrical safety during MEP rough-in. All portable tools must be double-insulated or connected to GFCI. Lockout/tagout procedures mandatory.",
        )
    )

    await s.flush()
    logger.info("Created 2 cameras, 3 zones, 2 alerts, 5 inspections, 1 risk score")


# ---------------------------------------------------------------------------
# 12. Bid History
# ---------------------------------------------------------------------------
async def create_bids(s: AsyncSession, org, users):
    bid_defs = [
        # (name, type, method, value, status, outcome, score, rec, win_prob, margin)
        # Won bids
        (
            "Roanoke City Hall Renovation",
            "institutional",
            "negotiated",
            6200000,
            "won",
            "won",
            82,
            "STRONG_PURSUE",
            0.65,
            0.084,
        ),
        (
            "Valley View Mall Expansion",
            "commercial",
            "hard_bid",
            9800000,
            "won",
            "won",
            75,
            "PURSUE",
            0.42,
            0.062,
        ),
        (
            "Carilion Medical Office",
            "healthcare",
            "cmar",
            4500000,
            "won",
            "won",
            88,
            "STRONG_PURSUE",
            0.71,
            0.095,
        ),
        (
            "Salem Mixed-Use Development",
            "mixed_use",
            "design_build",
            11200000,
            "won",
            "won",
            79,
            "STRONG_PURSUE",
            0.58,
            0.073,
        ),
        (
            "Patrick Henry High School Gym",
            "institutional",
            "hard_bid",
            3200000,
            "won",
            "won",
            71,
            "PURSUE",
            0.38,
            0.055,
        ),
        (
            "New River Tech Park — Bldg C",
            "commercial",
            "negotiated",
            7600000,
            "won",
            "won",
            77,
            "STRONG_PURSUE",
            0.52,
            0.081,
        ),
        # Lost bids
        (
            "Blacksburg University Dormitory",
            "institutional",
            "hard_bid",
            18500000,
            "lost",
            "lost",
            58,
            "PURSUE",
            0.28,
            None,
        ),
        (
            "Vinton Warehouse Conversion",
            "commercial",
            "hard_bid",
            2800000,
            "lost",
            "lost",
            45,
            "CONDITIONAL",
            0.18,
            None,
        ),
        (
            "Lynchburg Hospital Wing",
            "healthcare",
            "cmar",
            24000000,
            "lost",
            "lost",
            42,
            "CONDITIONAL",
            0.15,
            None,
        ),
        (
            "Botetourt County Courthouse",
            "institutional",
            "hard_bid",
            8900000,
            "lost",
            "lost",
            65,
            "PURSUE",
            0.35,
            None,
        ),
        (
            "Hollins University Library",
            "institutional",
            "hard_bid",
            5400000,
            "lost",
            "lost",
            62,
            "PURSUE",
            0.32,
            None,
        ),
        (
            "Daleville Town Center",
            "commercial",
            "design_build",
            13500000,
            "lost",
            "lost",
            55,
            "PURSUE",
            0.25,
            None,
        ),
        (
            "Smith Mountain Lake Resort",
            "hospitality",
            "negotiated",
            21000000,
            "lost",
            "lost",
            38,
            "CONDITIONAL",
            0.12,
            None,
        ),
        (
            "Cave Spring Retail Plaza",
            "commercial",
            "hard_bid",
            4100000,
            "lost",
            "lost",
            48,
            "CONDITIONAL",
            0.20,
            None,
        ),
        (
            "Montgomery County Fire Station",
            "institutional",
            "hard_bid",
            2200000,
            "lost",
            "lost",
            68,
            "PURSUE",
            0.38,
            None,
        ),
        (
            "Christiansburg Apartments",
            "residential",
            "hard_bid",
            7800000,
            "lost",
            "lost",
            52,
            "CONDITIONAL",
            0.22,
            None,
        ),
        (
            "Radford City Pool Complex",
            "recreational",
            "hard_bid",
            3600000,
            "lost",
            "lost",
            60,
            "PURSUE",
            0.30,
            None,
        ),
        (
            "Roanoke Airport Terminal Exp.",
            "infrastructure",
            "cmar",
            45000000,
            "lost",
            "lost",
            35,
            "NO_BID",
            0.08,
            None,
        ),
        # No-bid
        (
            "Norfolk Naval Base Barracks",
            "military",
            "hard_bid",
            62000000,
            "no_bid",
            "no_bid",
            22,
            "NO_BID",
            0.03,
            None,
        ),
        (
            "Richmond Convention Center",
            "commercial",
            "design_build",
            85000000,
            "no_bid",
            "no_bid",
            18,
            "NO_BID",
            0.02,
            None,
        ),
    ]

    for i, (name, ptype, method, value, status, outcome, score, rec, win_p, margin) in enumerate(
        bid_defs
    ):
        due = TODAY - timedelta(days=random.Random(i).randint(30, 365))
        opp = BidOpportunity(
            id=_uid(f"bid-{i}"),
            org_id=org.id,
            name=name,
            owner_name=f"{name.split()[0]} {'County' if 'County' in name else 'City' if 'City' in name else 'Development LLC'}",
            project_type=ptype,
            delivery_method=method,
            estimated_value=Decimal(str(value)),
            location="Roanoke, VA" if i < 6 else "SW Virginia",
            bid_due_date=due,
            status=status,
            outcome=outcome,
            actual_margin=float(margin) if margin else None,
            metadata_json={},
        )
        s.add(opp)
        await s.flush()

        factors = {
            "historical_win_rate": {
                "score": min(100, score + random.Random(i).randint(-10, 10)),
                "weight": 0.15,
            },
            "owner_relationship": {
                "score": min(100, score + random.Random(i + 1).randint(-15, 15)),
                "weight": 0.12,
            },
            "backlog_capacity": {"score": random.Random(i + 2).randint(50, 90), "weight": 0.12},
            "geographic_familiarity": {
                "score": 85 if "Roanoke" in (opp.location or "") else 45,
                "weight": 0.10,
            },
            "project_size_fit": {"score": 80 if 2000000 < value < 15000000 else 40, "weight": 0.08},
        }

        s.add(
            BidDecision(
                id=_uid(f"bid-dec-{i}"),
                opportunity_id=opp.id,
                decided_by=users[0].id,
                ai_score=score,
                ai_recommendation=rec,
                ai_reasoning=f"{'Strong' if score >= 75 else 'Moderate' if score >= 55 else 'Weak'} fit based on historical performance, geographic proximity, and project size alignment.",
                human_decision=outcome,
                human_notes="Aligned with AI recommendation."
                if outcome != "no_bid"
                else "Outside our geographic/size sweet spot.",
                factor_scores=factors,
                win_probability=win_p,
            )
        )

    # NEW OPPORTUNITY: live demo bid
    new_opp = BidOpportunity(
        id=_uid("bid-new"),
        org_id=org.id,
        name="Roanoke County Elementary School",
        owner_name="Roanoke County Public Schools",
        project_type="education",
        delivery_method="hard_bid",
        estimated_value=Decimal("8000000"),
        location="Roanoke, VA",
        bid_due_date=TODAY + timedelta(days=21),
        description="New 65,000 SF K-5 elementary school with gymnasium, cafeteria, and administrative wing. LEED Silver target.",
        status="evaluating",
    )
    s.add(new_opp)
    await s.flush()

    s.add(
        BidDecision(
            id=_uid("bid-dec-new"),
            opportunity_id=new_opp.id,
            decided_by=users[0].id,
            ai_score=72,
            ai_recommendation="PURSUE",
            ai_reasoning="Strong geographic fit (Roanoke). Project size ($8M) within sweet spot. Education sector experience from Patrick Henry HS gym win. Hard bid increases competition but local knowledge provides advantage. Backlog capacity available. Recommend pursuing with competitive pricing strategy.",
            factor_scores={
                "historical_win_rate": {
                    "score": 68,
                    "weight": 0.15,
                    "reasoning": "32% win rate on hard bids in education sector",
                },
                "owner_relationship": {
                    "score": 55,
                    "weight": 0.12,
                    "reasoning": "No prior work with RCPS but strong public sector references",
                },
                "backlog_capacity": {
                    "score": 82,
                    "weight": 0.12,
                    "reasoning": "Current backlog allows new project start in 4 months",
                },
                "geographic_familiarity": {
                    "score": 92,
                    "weight": 0.10,
                    "reasoning": "Roanoke area — excellent local sub and supplier network",
                },
                "project_size_fit": {
                    "score": 88,
                    "weight": 0.08,
                    "reasoning": "$8M is core sweet spot range ($3M-$15M)",
                },
                "delivery_method_expertise": {
                    "score": 65,
                    "weight": 0.08,
                    "reasoning": "Competitive hard bid — 12% industry average win rate",
                },
                "competition_level": {
                    "score": 58,
                    "weight": 0.08,
                    "reasoning": "Estimated 5-7 bidders for this market/size",
                },
                "margin_potential": {
                    "score": 62,
                    "weight": 0.05,
                    "reasoning": "Education projects historically 5-7% margin",
                },
            },
            win_probability=0.38,
        )
    )

    await s.flush()
    logger.info("Created 20 bid opportunities + 1 new evaluating opportunity")


# ---------------------------------------------------------------------------
# 13. Intelligence Brief
# ---------------------------------------------------------------------------
async def create_intelligence(s: AsyncSession, project, users):
    brief = IntelligenceBrief(
        id=_uid("intel-brief"),
        project_id=project.id,
        generated_by=users[0].id,
        report_date=TODAY - timedelta(days=1),
        overall_health_score=68,
        project_status="YELLOW",
        schedule_health_score=62,
        cost_health_score=72,
        risk_score=65,
        productivity_score=74,
        executive_summary=(
            "Metro Center Office Tower is approximately 35% complete, tracking 12% behind the "
            "baseline schedule (SPI = 0.88) due to accumulated weather delays during foundation work "
            "and an 8-day RFI hold on curtain wall anchors. Cost performance is slightly over budget "
            "(CPI = 0.94) driven by the $45K dewatering change order and overtime to recover schedule. "
            "Two pending PCOs totaling $43K require owner resolution. The critical path runs through "
            "4th floor structure completion to MEP rough-in. Recommend accelerating curtain wall "
            "installation and increasing MEP crew size to recover 1-2 weeks."
        ),
        schedule_intelligence={
            "spi": 0.88,
            "critical_path": "4th Floor Structure → MEP Rough-in → Commissioning",
            "delay_events": [
                {
                    "event": "Weather delay — foundation excavation",
                    "impact_days": 5,
                    "status": "occurred",
                },
                {
                    "event": "RFI hold — curtain wall anchor detail",
                    "impact_days": 8,
                    "status": "occurred",
                },
                {
                    "event": "Material delay — electrical switchgear",
                    "impact_days": 3,
                    "status": "monitoring",
                },
            ],
            "recovery_options": [
                "Add second curtain wall crew (potential 5-day recovery)",
                "Weekend concrete pours for floor 4 deck (potential 3-day recovery)",
                "Accelerate MEP rough-in with additional electricians",
            ],
        },
        cost_intelligence={
            "cpi": 0.94,
            "budget_at_completion": 12595000,
            "estimate_at_completion": 13398936,
            "variance_at_completion": -803936,
            "approved_cos": 95000,
            "pending_cos": 43000,
            "contingency_remaining": 285000,
            "concerns": [
                "CPI trending downward — monitor overtime costs",
                "Pending PCOs ($43K) may erode contingency",
                "Switchgear delay could trigger acceleration costs",
            ],
        },
        risk_intelligence={
            "top_risks": [
                {
                    "risk": "Schedule recovery may require premium time",
                    "probability": "high",
                    "impact": "medium",
                },
                {
                    "risk": "Curtain wall anchor RFI delay cascade",
                    "probability": "medium",
                    "impact": "high",
                },
                {
                    "risk": "Switchgear delay impacts electrical rough-in",
                    "probability": "medium",
                    "impact": "medium",
                },
            ],
        },
        productivity_intelligence={
            "average_crew_size": 52,
            "productivity_trend": "stable",
            "top_performers": [
                "Concrete crew — 105% of planned output",
                "Steel crew — 98% of planned output",
            ],
            "concerns": ["MEP coordination delays reducing plumber productivity to 82%"],
        },
        action_items=[
            {
                "title": "Accelerate curtain wall — add second crew",
                "responsible": "Sarah Chen",
                "due_date": (TODAY + timedelta(days=7)).isoformat(),
                "status": "pending",
                "priority": "high",
            },
            {
                "title": "Resolve pending PCOs with owner",
                "responsible": "James Wilson",
                "due_date": (TODAY + timedelta(days=14)).isoformat(),
                "status": "pending",
                "priority": "high",
            },
            {
                "title": "Increase MEP crew — add 4 electricians",
                "responsible": "Mike Rodriguez",
                "due_date": (TODAY + timedelta(days=5)).isoformat(),
                "status": "pending",
                "priority": "medium",
            },
        ],
        metrics_dashboard={
            "percent_complete": 35,
            "days_elapsed": 152,
            "days_remaining": 268,
            "spi": 0.88,
            "cpi": 0.94,
            "active_rfis": 5,
            "overdue_rfis": 2,
            "open_submittals": 10,
            "safety_incidents": 0,
            "near_misses": 2,
        },
        narrative_report=(
            "## Weekly Intelligence Brief — Metro Center Office Tower\n\n"
            "### Schedule Status\n"
            "The project is currently 12% behind the baseline schedule with an SPI of 0.88. "
            "Two delay events have impacted the critical path: a 5-day weather delay during "
            "foundation excavation in month 1, and an 8-day hold awaiting the curtain wall "
            "anchor detail RFI response. A 3-day material delay on the main electrical "
            "switchgear is being monitored but has not yet impacted the critical path.\n\n"
            "### Cost Status\n"
            "Cost performance shows a CPI of 0.94, indicating approximately 6% cost overrun. "
            "Primary drivers include the $45,000 dewatering change order for the unexpected "
            "high water table and overtime charges incurred during schedule recovery efforts. "
            "Three change orders have been approved totaling $95,000, with two additional PCOs "
            "($43,000) pending owner approval.\n\n"
            "### Recommendations\n"
            "1. Add a second curtain wall installation crew to recover 5 days\n"
            "2. Schedule weekend concrete pours for the 4th floor deck\n"
            "3. Resolve pending PCOs to maintain contingency reserves\n"
            "4. Increase MEP crew size by 4 electricians to prevent downstream delays\n"
        ),
    )
    s.add(brief)
    await s.flush()
    logger.info("Created intelligence brief + risk score")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def generate_demo(db_url: str, clean: bool = False):
    """Generate all demo data."""
    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # Check for existing demo
        result = await session.execute(
            select(Organization).where(Organization.slug == DEMO_ORG_SLUG)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if clean:
                logger.info("Cleaning existing demo data (org_id=%s)...", existing.id)
                # Delete org — CASCADE will remove project and all FK-linked data
                await session.execute(delete(Organization).where(Organization.id == existing.id))
                await session.commit()
                logger.info("Existing demo data deleted.")
            else:
                logger.info(
                    "Demo project already exists (org: %s). Use --clean to recreate.", existing.name
                )
                await engine.dispose()
                return

        logger.info("=" * 60)
        logger.info("Generating Metro Center Office Tower demo project...")
        logger.info("=" * 60)

        org, users = await create_org_and_users(session)
        project = await create_project(session, org, users)
        await create_schedule(session, project, users)
        await create_budget(session, project, users)
        await create_rfis(session, project, users)
        await create_submittals(session, project, users)
        await create_daily_logs(session, project, users)
        await create_drawings(session, project, users)
        await create_meetings(session, project, users)
        await create_punch_list(session, project, users)
        await create_safety(session, project, users)
        await create_bids(session, org, users)
        await create_intelligence(session, project, users)

        await session.commit()
        logger.info("=" * 60)
        logger.info("Demo project generated successfully!")
        logger.info("  Org:     %s (%s)", org.name, org.slug)
        logger.info("  Project: %s", project.name)
        logger.info("  Login:   sarah.chen@metrocenter.demo / DemoPass123!")
        logger.info("=" * 60)

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Generate ConstructAI demo project data")
    parser.add_argument(
        "--db-url", default=DEFAULT_DB_URL, help="Database URL (default: DATABASE_URL env var)"
    )
    parser.add_argument(
        "--clean", action="store_true", help="Delete existing demo data and recreate"
    )
    args = parser.parse_args()

    if not args.db_url:
        logger.error("No database URL. Set DATABASE_URL or use --db-url")
        sys.exit(1)

    asyncio.run(generate_demo(args.db_url, args.clean))


if __name__ == "__main__":
    main()
