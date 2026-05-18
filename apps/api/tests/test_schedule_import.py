"""Tests for P6/MS Project schedule import, calendar support, and CPM with calendars."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from app.models.project import Project
from app.models.scheduling import ScheduleActivity, ScheduleBaseline
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scheduling.cpm_engine import DEFAULT_CALENDAR, WorkCalendar, calculate_cpm

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession, test_org):
    project = Project(
        name="Schedule Import Test Project",
        org_id=test_org.id,
        status="active",
        contract_value=Decimal("1000000.00"),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture(scope="function")
async def baseline_with_calendars(db_session: AsyncSession, test_project):
    baseline = ScheduleBaseline(
        project_id=test_project.id,
        name="Test Baseline",
        version=1,
        baseline_date=date(2026, 3, 2),  # Monday
        calendars=[
            {
                "id": "cal1",
                "name": "5-Day",
                "work_days": [0, 1, 2, 3, 4],
                "holidays": [],
                "hours_per_day": 8.0,
            },
            {
                "id": "cal2",
                "name": "6-Day",
                "work_days": [0, 1, 2, 3, 4, 5],
                "holidays": ["2026-03-10"],
                "hours_per_day": 8.0,
            },
        ],
    )
    db_session.add(baseline)
    await db_session.flush()
    await db_session.refresh(baseline)
    return baseline


# ---------------------------------------------------------------------------
# WorkCalendar Unit Tests
# ---------------------------------------------------------------------------


class TestCalendarSupport:
    def test_work_calendar_5_day(self):
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        mon = date(2026, 3, 2)  # Monday
        assert cal.is_work_day(mon) is True
        assert cal.is_work_day(mon + timedelta(days=1)) is True  # Tue
        assert cal.is_work_day(mon + timedelta(days=2)) is True  # Wed
        assert cal.is_work_day(mon + timedelta(days=3)) is True  # Thu
        assert cal.is_work_day(mon + timedelta(days=4)) is True  # Fri
        assert cal.is_work_day(mon + timedelta(days=5)) is False  # Sat
        assert cal.is_work_day(mon + timedelta(days=6)) is False  # Sun

    def test_work_calendar_6_day(self):
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4, 5])
        mon = date(2026, 3, 2)
        assert cal.is_work_day(mon + timedelta(days=5)) is True  # Sat
        assert cal.is_work_day(mon + timedelta(days=6)) is False  # Sun

    def test_work_calendar_with_holidays(self):
        cal = WorkCalendar(
            work_days=[0, 1, 2, 3, 4],
            holidays={"2026-03-04"},  # Wednesday
        )
        wed = date(2026, 3, 4)
        assert cal.is_work_day(wed) is False  # Holiday
        assert cal.is_work_day(wed - timedelta(days=1)) is True  # Tue

    def test_add_work_days_5day(self):
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        mon = date(2026, 3, 2)  # Monday
        # 5 work days from Monday = next Monday
        result = cal.add_work_days(mon, 5)
        assert result == date(2026, 3, 9)  # Next Monday

    def test_add_work_days_with_holiday(self):
        cal = WorkCalendar(
            work_days=[0, 1, 2, 3, 4],
            holidays={"2026-03-04"},  # Wednesday
        )
        mon = date(2026, 3, 2)
        # 5 work days: Tue, Thu, Fri, Mon(3/9), Tue(3/10) — skips Wed holiday
        result = cal.add_work_days(mon, 5)
        assert result == date(2026, 3, 10)

    def test_add_work_days_zero(self):
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        mon = date(2026, 3, 2)
        assert cal.add_work_days(mon, 0) == mon

    def test_work_days_between(self):
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        mon = date(2026, 3, 2)  # Monday
        next_mon = date(2026, 3, 9)
        assert cal.work_days_between(mon, next_mon) == 5

    def test_work_days_between_with_holiday(self):
        cal = WorkCalendar(
            work_days=[0, 1, 2, 3, 4],
            holidays={"2026-03-04"},
        )
        mon = date(2026, 3, 2)
        next_mon = date(2026, 3, 9)
        assert cal.work_days_between(mon, next_mon) == 4

    @pytest.mark.asyncio
    async def test_cpm_with_calendar_dates(self):
        """CPM with calendars should produce calendar dates that skip weekends."""
        activities = [
            {"id": "A", "name": "Activity A", "duration_days": 3, "predecessors": []},
            {"id": "B", "name": "Activity B", "duration_days": 5, "predecessors": ["A"]},
            {"id": "C", "name": "Activity C", "duration_days": 2, "predecessors": ["B"]},
        ]
        cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        project_start = date(2026, 3, 2)  # Monday

        result = await calculate_cpm(
            activities,
            calendars={"default": cal},
            project_start=project_start,
        )

        assert result["project_duration"] == 10

        act_map = {a["id"]: a for a in result["activities"]}

        # A: starts Mon 3/2, 3 work days → finishes Wed 3/4 (Thu 3/5 after add_work_days)
        assert "start_date" in act_map["A"]
        assert "finish_date" in act_map["A"]

        # B follows A, should skip weekend
        b_start = date.fromisoformat(act_map["B"]["start_date"])
        assert b_start.weekday() < 5  # Should be a weekday

        # C follows B
        c_finish = date.fromisoformat(act_map["C"]["finish_date"])
        assert c_finish.weekday() < 5  # Should be a weekday

    @pytest.mark.asyncio
    async def test_cpm_backward_compat(self):
        """CPM without calendars should return offsets only (no start_date/finish_date)."""
        activities = [
            {"id": "A", "name": "Activity A", "duration_days": 5, "predecessors": []},
            {"id": "B", "name": "Activity B", "duration_days": 3, "predecessors": ["A"]},
        ]
        result = await calculate_cpm(activities)

        assert result["project_duration"] == 8
        act_map = {a["id"]: a for a in result["activities"]}
        assert "start_date" not in act_map["A"]
        assert "finish_date" not in act_map["A"]
        assert act_map["A"]["early_start"] == 0
        assert act_map["A"]["early_finish"] == 5

    @pytest.mark.asyncio
    async def test_cpm_per_activity_calendar(self):
        """Activities can reference different calendars."""
        activities = [
            {
                "id": "A",
                "name": "Activity A",
                "duration_days": 5,
                "predecessors": [],
                "calendar_id": "cal_5day",
            },
            {
                "id": "B",
                "name": "Activity B",
                "duration_days": 5,
                "predecessors": ["A"],
                "calendar_id": "cal_6day",
            },
        ]
        cal_5 = WorkCalendar(work_days=[0, 1, 2, 3, 4])
        cal_6 = WorkCalendar(work_days=[0, 1, 2, 3, 4, 5])
        project_start = date(2026, 3, 2)  # Monday

        result = await calculate_cpm(
            activities,
            calendars={"cal_5day": cal_5, "cal_6day": cal_6},
            project_start=project_start,
        )

        act_map = {a["id"]: a for a in result["activities"]}
        a_finish = date.fromisoformat(act_map["A"]["finish_date"])
        b_finish = date.fromisoformat(act_map["B"]["finish_date"])

        # B uses 6-day calendar so should finish sooner than with 5-day
        assert b_finish > a_finish

    def test_default_calendar_is_5day(self):
        assert DEFAULT_CALENDAR.is_work_day(date(2026, 3, 2))  # Monday
        assert not DEFAULT_CALENDAR.is_work_day(date(2026, 3, 7))  # Saturday


# ---------------------------------------------------------------------------
# Schedule Importer Parsing Tests (mocked MPXJ)
# ---------------------------------------------------------------------------


def _make_mock_project_file(
    tasks=None,
    calendars=None,
    resources=None,
    data_date=None,
):
    """Create a mock MPXJ ProjectFile object."""
    pf = MagicMock()

    # Properties
    props = MagicMock()
    props.getStatusDate.return_value = data_date
    props.getCurrentDate.return_value = data_date
    pf.getProjectProperties.return_value = props

    # Calendars
    if calendars is None:
        cal = MagicMock()
        cal.getUniqueID.return_value = 1
        cal.getName.return_value = "Standard"

        def _is_working(day):
            name = str(day.name()) if hasattr(day, "name") else str(day)
            return name not in ("SATURDAY", "SUNDAY")

        cal.isWorkingDay = _is_working
        cal.getCalendarExceptions.return_value = []
        cal.getMinutesPerDay.return_value = 480
        calendars = [cal]
    pf.getCalendars.return_value = calendars

    # Tasks
    if tasks is None:
        tasks = []
    pf.getTasks.return_value = tasks

    # Resources
    if resources is None:
        resources = []
    pf.getResources.return_value = resources

    return pf


def _make_mock_task(
    uid,
    name,
    duration_days=5,
    predecessors=None,
    parent=None,
    is_summary=False,
    calendar=None,
    wbs="",
    pct_complete=0,
):
    """Create a mock MPXJ Task object."""
    task = MagicMock()
    task.getID.return_value = uid
    task.getUniqueID.return_value = uid
    task.getName.return_value = name
    task.getWBS.return_value = wbs
    task.getPercentageComplete.return_value = pct_complete
    task.getParentTask.return_value = parent

    # Activity ID (P6-specific)
    task.getActivityID.return_value = f"ACT-{uid}"

    # Summary
    child_list = MagicMock()
    child_list.size.return_value = 1 if is_summary else 0
    task.getChildTasks.return_value = child_list
    task.getSummary.return_value = is_summary

    # Duration
    dur = MagicMock()
    dur.getDuration.return_value = duration_days

    # convertUnits should return a Duration-like with getDuration
    converted = MagicMock()
    converted.getDuration.return_value = duration_days
    dur.convertUnits.return_value = converted

    task.getDuration.return_value = dur

    # Dates
    task.getStart.return_value = None
    task.getFinish.return_value = None

    # Calendar
    if calendar:
        task.getEffectiveCalendar.return_value = calendar
    else:
        task.getEffectiveCalendar.return_value = None

    # Predecessors
    if predecessors is None:
        predecessors = []
    pred_rels = []
    for pred_task, rel_type, lag in predecessors:
        rel = MagicMock()
        rel.getTargetTask.return_value = pred_task
        rel_type_obj = MagicMock()
        rel_type_obj.name.return_value = rel_type
        rel.getType.return_value = rel_type_obj
        lag_dur = MagicMock()
        lag_dur.getDuration.return_value = lag
        lag_converted = MagicMock()
        lag_converted.getDuration.return_value = lag
        lag_dur.convertUnits.return_value = lag_converted
        rel.getLag.return_value = lag_dur
        pred_rels.append(rel)
    task.getPredecessors.return_value = pred_rels

    # Resource assignments
    task.getResourceAssignments.return_value = []

    return task


class TestScheduleImporterParsing:
    def test_supported_extensions(self):
        from app.services.scheduling.schedule_importer import SUPPORTED_EXTENSIONS

        expected = {".xer", ".xml", ".pmxml", ".mpp", ".mpx", ".mspdi"}
        assert expected == SUPPORTED_EXTENSIONS

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_creates_baseline_and_activities(
        self, mock_jvm, mock_parse, client, auth_headers, test_project, db_session
    ):
        task_a = _make_mock_task(1, "Foundation")
        task_b = _make_mock_task(2, "Framing", predecessors=[(task_a, "FINISH_START", 0)])

        pf = _make_mock_project_file(tasks=[task_a, task_b])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake-xer-content", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["activities_imported"] == 2
        assert data["relationships_imported"] == 1
        assert data["calendars_imported"] >= 1
        assert data["baseline"]["source_format"] == "p6_xer"

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_preserves_relationships(
        self, mock_jvm, mock_parse, client, auth_headers, test_project
    ):
        task_a = _make_mock_task(1, "Activity A")
        task_b = _make_mock_task(2, "Activity B", predecessors=[(task_a, "START_START", 2)])
        task_c = _make_mock_task(3, "Activity C", predecessors=[(task_b, "FINISH_FINISH", 0)])

        pf = _make_mock_project_file(tasks=[task_a, task_b, task_c])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.mpp", b"fake-mpp", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["relationships_imported"] == 2

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_extracts_calendars(
        self, mock_jvm, mock_parse, client, auth_headers, test_project
    ):
        task_a = _make_mock_task(1, "Activity A")
        pf = _make_mock_project_file(tasks=[task_a])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["calendars_imported"] >= 1
        cals = data["baseline"]["calendars"]
        assert len(cals) >= 1
        cal = cals[0]
        assert "work_days" in cal
        assert "holidays" in cal
        assert "hours_per_day" in cal

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_builds_wbs_paths(
        self, mock_jvm, mock_parse, client, auth_headers, test_project
    ):
        parent = MagicMock()
        parent.getName.return_value = "Phase 1"
        parent.getParentTask.return_value = None

        task_a = _make_mock_task(1, "Foundation", parent=parent)

        pf = _make_mock_project_file(tasks=[task_a])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        # WBS path should be "Phase 1/Foundation"
        activities = resp.json()["baseline"]["activities"]
        assert len(activities) == 1
        assert activities[0]["wbs_path"] == "Phase 1/Foundation"

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_stores_source_metadata(
        self, mock_jvm, mock_parse, client, auth_headers, test_project
    ):
        task_a = _make_mock_task(1, "Activity A")
        pf = _make_mock_project_file(tasks=[task_a])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("hospital_schedule.xer", b"fake", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        bl = resp.json()["baseline"]
        assert bl["source_file"] == "hospital_schedule.xer"
        assert bl["source_format"] == "p6_xer"

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_skips_summary_tasks(
        self, mock_jvm, mock_parse, client, auth_headers, test_project
    ):
        summary = _make_mock_task(1, "Phase 1", is_summary=True)
        leaf = _make_mock_task(2, "Foundation Work")

        pf = _make_mock_project_file(tasks=[summary, leaf])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        # Only the leaf task should be imported
        assert resp.json()["activities_imported"] == 1

    @pytest.mark.asyncio
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._parse_file")
    @patch("app.services.scheduling.schedule_importer.ScheduleImporter._ensure_jvm")
    async def test_import_runs_cpm(self, mock_jvm, mock_parse, client, auth_headers, test_project):
        task_a = _make_mock_task(1, "Activity A", duration_days=5)
        task_b = _make_mock_task(
            2, "Activity B", duration_days=3, predecessors=[(task_a, "FINISH_START", 0)]
        )

        pf = _make_mock_project_file(tasks=[task_a, task_b])
        mock_parse.return_value = pf

        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake", "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        bl = resp.json()["baseline"]
        assert bl["total_duration_days"] == 8
        assert bl["critical_path_length"] == 2


# ---------------------------------------------------------------------------
# Schedule Import API Tests
# ---------------------------------------------------------------------------


class TestScheduleImportAPI:
    @pytest.mark.asyncio
    async def test_import_unsupported_format(self, client, auth_headers, test_project):
        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.txt", b"not a schedule", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "Unsupported" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_import_requires_auth(self, client, test_project):
        resp = await client.post(
            f"/api/v1/scheduling/{test_project.id}/schedule/import",
            files={"file": ("test.xer", b"fake", "application/octet-stream")},
        )
        # Un-authed POST is rejected by CSRFMiddleware with 403 before the
        # auth dependency runs.
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cpm_with_calendars(
        self, client, auth_headers, baseline_with_calendars, db_session
    ):
        """Verify run_cpm endpoint uses baseline calendars."""
        bl = baseline_with_calendars

        act1 = ScheduleActivity(
            project_id=bl.project_id,
            baseline_id=bl.id,
            activity_code="A100",
            name="Foundation",
            duration_days=5,
            predecessors=[],
            calendar_id="cal1",
        )
        act2 = ScheduleActivity(
            project_id=bl.project_id,
            baseline_id=bl.id,
            activity_code="A200",
            name="Framing",
            duration_days=3,
            predecessors=[],  # Will set after flush
            calendar_id="cal1",
        )
        db_session.add(act1)
        await db_session.flush()

        act2.predecessors = [{"predecessor_id": str(act1.id), "type": "FS", "lag": 0}]
        db_session.add(act2)
        await db_session.flush()

        resp = await client.post(
            f"/api/v1/scheduling/baselines/{bl.id}/cpm",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_duration"] == 8

        # Verify activities have start_date/finish_date (since baseline has calendars)
        act_map = {a["id"]: a for a in data["activities"]}
        for act in act_map.values():
            assert "start_date" in act
            assert "finish_date" in act


# ---------------------------------------------------------------------------
# Relationship Type Mapping Tests
# ---------------------------------------------------------------------------


class TestRelationshipMapping:
    @pytest.mark.asyncio
    async def test_fs_relationship(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 5, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 3,
                "relationships": [{"predecessor_id": "A", "type": "FS", "lag": 0}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        assert act_map["B"]["early_start"] == 5

    @pytest.mark.asyncio
    async def test_ss_relationship(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 5, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 3,
                "relationships": [{"predecessor_id": "A", "type": "SS", "lag": 0}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        assert act_map["B"]["early_start"] == 0

    @pytest.mark.asyncio
    async def test_ff_relationship(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 5, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 3,
                "relationships": [{"predecessor_id": "A", "type": "FF", "lag": 0}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        # FF: EF[B] >= EF[A], so EF[B] >= 5, ES[B] = 5 - 3 = 2
        assert act_map["B"]["early_start"] == 2
        assert act_map["B"]["early_finish"] == 5

    @pytest.mark.asyncio
    async def test_sf_relationship(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 5, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 3,
                "relationships": [{"predecessor_id": "A", "type": "SF", "lag": 0}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        # SF: EF[B] >= ES[A] + lag = 0, so ES[B] = max(0, 0-3) = 0
        assert act_map["B"]["early_start"] == 0

    @pytest.mark.asyncio
    async def test_lag_applied(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 5, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 3,
                "relationships": [{"predecessor_id": "A", "type": "FS", "lag": 5}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        # FS+5: ES[B] = EF[A] + 5 = 5 + 5 = 10
        assert act_map["B"]["early_start"] == 10
        assert act_map["B"]["early_finish"] == 13

    @pytest.mark.asyncio
    async def test_negative_lag_lead(self):
        activities = [
            {"id": "A", "name": "A", "duration_days": 10, "relationships": []},
            {
                "id": "B",
                "name": "B",
                "duration_days": 5,
                "relationships": [{"predecessor_id": "A", "type": "FS", "lag": -3}],
            },
        ]
        result = await calculate_cpm(activities)
        act_map = {a["id"]: a for a in result["activities"]}
        # FS-3 (lead): ES[B] = EF[A] - 3 = 10 - 3 = 7
        assert act_map["B"]["early_start"] == 7
        assert act_map["B"]["early_finish"] == 12
