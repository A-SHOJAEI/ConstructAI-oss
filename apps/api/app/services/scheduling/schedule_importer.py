"""P6 / MS Project schedule importer using MPXJ via JPype.

Supports:
  - Primavera P6: .xer, .pmxml
  - Microsoft Project: .mpp, .mpx, .mspdi, .xml

Usage::

    importer = ScheduleImporter()
    result = await importer.import_file(db, project_id, upload_file, user_id)
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".xer", ".xml", ".pmxml", ".mpp", ".mpx", ".mspdi"}

_FORMAT_MAP = {
    ".xer": "p6_xer",
    ".pmxml": "p6_xml",
    ".xml": "msp_xml",
    ".mpp": "msp_mpp",
    ".mpx": "msp_mpx",
    ".mspdi": "msp_xml",
}

# MPXJ RelationType name → our short code
_RELATION_TYPE_MAP = {
    "FINISH_START": "FS",
    "START_START": "SS",
    "FINISH_FINISH": "FF",
    "START_FINISH": "SF",
}

# MPXJ Day enum name → Python weekday (0=Mon..6=Sun)
_DAY_INDEX = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


class ScheduleImporter:
    """Import P6/MSP schedules via MPXJ (Java) through JPype."""

    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    _jvm_started: bool = False

    # ------------------------------------------------------------------
    # JVM lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def _ensure_jvm(cls) -> None:
        """Start the JVM once with MPXJ JARs on the classpath."""
        if cls._jvm_started:
            return

        import jpype

        if jpype.isJVMStarted():
            cls._jvm_started = True
            return

        jar_dir = Path(__file__).parent / "lib"
        jars = list(jar_dir.glob("*.jar"))
        if not jars:
            raise RuntimeError(
                f"No MPXJ JAR files found in {jar_dir}. "
                "Run: python -m app.services.scheduling.download_mpxj"
            )
        classpath = os.pathsep.join(str(j) for j in jars)
        jpype.startJVM(classpath=[classpath])
        cls._jvm_started = True
        logger.info("JVM started with %d JAR(s) on classpath", len(jars))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def import_file(
        self,
        db: AsyncSession,
        project_id,
        upload_file,
        user_id,
    ) -> dict:
        """Parse an uploaded schedule file and persist as a new baseline.

        Returns a dict matching ``ScheduleImportResponse``.
        """
        from sqlalchemy import select

        from app.models.scheduling import ScheduleActivity, ScheduleBaseline
        from app.services.scheduling.cpm_engine import WorkCalendar, calculate_cpm

        filename = upload_file.filename or "schedule"
        ext = Path(filename).suffix.lower()
        source_format = _FORMAT_MAP.get(ext, "unknown")

        # Save upload to temp file (MPXJ needs a file path)
        content = await upload_file.read()
        # MPXJ needs a closed file path on disk — can't use `with` here
        # because we must close (not exit) before handing off to MPXJ.
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)  # noqa: SIM115
        try:
            tmp.write(content)
            tmp.close()

            # Parse with MPXJ
            project_file = self._parse_file(tmp.name)
        finally:
            os.unlink(tmp.name)

        warnings: list[str] = []

        # Extract data
        calendars = self._extract_calendars(project_file)
        calendar_map = {c["id"]: c for c in calendars}
        activities_raw = self._extract_activities(project_file, calendar_map)
        relationships = self._extract_relationships(project_file, activities_raw)
        self._extract_resources(project_file)

        # Determine data date
        data_date = self._get_data_date(project_file)

        # Merge relationships into activities
        rel_count = 0
        rel_by_activity: dict[str, list] = {}
        for rel in relationships:
            rel_by_activity.setdefault(rel["successor_id"], []).append(
                {
                    "predecessor_id": rel["predecessor_id"],
                    "type": rel["type"],
                    "lag": rel["lag"],
                }
            )
            rel_count += 1

        for act in activities_raw:
            act["relationships"] = rel_by_activity.get(act["original_id"], [])
            # Map predecessor original_ids for CPM (will be remapped after DB insert)

        # Determine next baseline version
        version_query = (
            select(ScheduleBaseline.version)
            .where(ScheduleBaseline.project_id == project_id)
            .order_by(ScheduleBaseline.version.desc())
            .limit(1)
        )
        result = await db.execute(version_query)
        latest_version = result.scalar()
        next_version = (latest_version or 0) + 1

        baseline = ScheduleBaseline(
            project_id=project_id,
            name=f"Import v{next_version} — {filename}",
            version=next_version,
            baseline_date=data_date or date.today(),
            source_file=filename,
            source_format=source_format,
            calendars=calendars,
            data_date=data_date,
            created_by=user_id,
        )
        db.add(baseline)
        await db.flush()

        # Create activity records
        original_to_uuid: dict[str, str] = {}
        db_activities: list[ScheduleActivity] = []

        for act in activities_raw:
            db_act = ScheduleActivity(
                project_id=project_id,
                baseline_id=baseline.id,
                activity_code=act.get("activity_code", act["original_id"]),
                name=act["name"],
                duration_days=act["duration_days"],
                start_date=act.get("start_date"),
                finish_date=act.get("finish_date"),
                predecessors=[],  # Will be set after ID mapping
                resource_assignments=act.get("resource_assignments", []),
                wbs_code=act.get("wbs_code"),
                calendar_id=act.get("calendar_id"),
                original_id=act["original_id"],
                wbs_path=act.get("wbs_path"),
                status=act.get("status", "not_started"),
                pct_complete=act.get("pct_complete", 0),
            )
            db.add(db_act)
            await db.flush()
            original_to_uuid[act["original_id"]] = str(db_act.id)
            db_activities.append(db_act)

        # Now set predecessors with mapped UUIDs
        for i, act in enumerate(activities_raw):
            rels = act.get("relationships", [])
            mapped_rels = []
            for rel in rels:
                pred_uuid = original_to_uuid.get(rel["predecessor_id"])
                if pred_uuid:
                    mapped_rels.append(
                        {
                            "predecessor_id": pred_uuid,
                            "type": rel["type"],
                            "lag": rel["lag"],
                        }
                    )
                else:
                    warnings.append(
                        f"Activity '{act['name']}': predecessor "
                        f"'{rel['predecessor_id']}' not found in import"
                    )
            db_activities[i].predecessors = mapped_rels
        await db.flush()

        # Run CPM
        cpm_calendars: dict[str, WorkCalendar] = {}
        for cal in calendars:
            cpm_calendars[cal["id"]] = WorkCalendar(
                work_days=cal["work_days"],
                holidays=set(cal.get("holidays", [])),
            )

        activity_dicts = [
            {
                "id": str(db_act.id),
                "name": db_act.name,
                "duration_days": db_act.duration_days,
                "relationships": db_act.predecessors,
                "calendar_id": db_act.calendar_id,
            }
            for db_act in db_activities
        ]

        try:
            cpm_result = await calculate_cpm(
                activity_dicts,
                calendars=cpm_calendars if cpm_calendars else None,
                project_start=baseline.baseline_date,
            )

            baseline.total_duration_days = cpm_result["project_duration"]
            baseline.critical_path_length = cpm_result["critical_path_length"]

            enriched_map = {a["id"]: a for a in cpm_result["activities"]}
            for db_act in db_activities:
                enriched = enriched_map.get(str(db_act.id))
                if enriched:
                    if "start_date" in enriched:
                        db_act.early_start = date.fromisoformat(enriched["start_date"])
                        db_act.early_finish = date.fromisoformat(enriched["finish_date"])
                    db_act.total_float = enriched.get("total_float")
                    db_act.free_float = enriched.get("free_float")
                    db_act.is_critical = enriched.get("is_critical", False)
            await db.flush()
        except Exception:
            logger.exception("CPM calculation failed after import")
            warnings.append("CPM calculation failed; activities imported without scheduling data")

        await db.refresh(baseline)

        from app.schemas.scheduling import ScheduleBaselineResponse

        return {
            "baseline": ScheduleBaselineResponse.model_validate(baseline),
            "activities_imported": len(db_activities),
            "relationships_imported": rel_count,
            "calendars_imported": len(calendars),
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # MPXJ parsing
    # ------------------------------------------------------------------

    def _parse_file(self, file_path: str):
        """Use MPXJ UniversalProjectReader to parse any supported format."""
        self._ensure_jvm()
        from net.sf.mpxj.reader import UniversalProjectReader  # type: ignore[import]

        reader = UniversalProjectReader()
        project_file = reader.read(file_path)
        if project_file is None:
            raise ValueError(f"MPXJ could not parse file: {file_path}")
        return project_file

    def _get_data_date(self, project_file) -> date | None:
        """Extract the project status / data date."""
        try:
            props = project_file.getProjectProperties()
            dd = props.getStatusDate()
            if dd is None:
                dd = props.getCurrentDate()
            if dd is not None:
                from java.util import Calendar  # type: ignore[import]

                cal = Calendar.getInstance()
                cal.setTime(dd)
                return date(
                    cal.get(Calendar.YEAR),
                    cal.get(Calendar.MONTH) + 1,
                    cal.get(Calendar.DAY_OF_MONTH),
                )
        except Exception:
            logger.debug("Could not extract data date", exc_info=True)
        return None

    def _extract_calendars(self, project_file) -> list[dict]:
        """Extract calendar definitions from the MPXJ project."""
        try:
            from net.sf.mpxj import Day  # type: ignore[import]
        except ImportError:
            Day = None

        calendars: list[dict] = []
        for cal in project_file.getCalendars():
            cal_id = str(cal.getUniqueID() or cal.getName() or len(calendars))
            name = str(cal.getName() or f"Calendar {cal_id}")

            work_days: list[int] = []
            for day_name, idx in _DAY_INDEX.items():
                try:
                    if Day is not None:
                        day_enum = getattr(Day, day_name)
                    else:
                        # No JVM: create a simple object with a name method
                        day_enum = type("Day", (), {"name": lambda self, _n=day_name: _n})()
                    if cal.isWorkingDay(day_enum):
                        work_days.append(idx)
                except Exception as e:
                    logger.debug(f"Could not determine work days from calendar: {e}")

            # If no work days detected, default to Mon-Fri
            if not work_days:
                work_days = [0, 1, 2, 3, 4]

            # Extract calendar exceptions (holidays)
            holidays: list[str] = []
            try:
                for exc in cal.getCalendarExceptions():
                    from_date = exc.getFromDate()
                    to_date = exc.getToDate()
                    if from_date is not None and not exc.getWorking():
                        from java.util import Calendar as JCal  # type: ignore[import]

                        jc = JCal.getInstance()
                        jc.setTime(from_date)
                        start = date(
                            jc.get(JCal.YEAR),
                            jc.get(JCal.MONTH) + 1,
                            jc.get(JCal.DAY_OF_MONTH),
                        )
                        if to_date is not None:
                            jc.setTime(to_date)
                            end = date(
                                jc.get(JCal.YEAR),
                                jc.get(JCal.MONTH) + 1,
                                jc.get(JCal.DAY_OF_MONTH),
                            )
                        else:
                            end = start
                        current = start
                        while current <= end:
                            holidays.append(current.isoformat())
                            current += timedelta(days=1)
            except Exception:
                logger.debug("Could not extract holidays for calendar %s", name, exc_info=True)

            # Estimate hours per day from calendar hours
            hours_per_day = 8.0
            try:
                mins = cal.getMinutesPerDay()
                if mins and int(str(mins)) > 0:
                    hours_per_day = int(str(mins)) / 60.0
            except Exception as e:
                logger.debug(f"Could not extract minutes per day: {e}")

            calendars.append(
                {
                    "id": cal_id,
                    "name": name,
                    "work_days": work_days,
                    "holidays": holidays,
                    "hours_per_day": hours_per_day,
                }
            )

        return calendars

    def _extract_activities(self, project_file, calendar_map: dict) -> list[dict]:
        """Extract leaf tasks (activities) from the MPXJ project."""
        activities: list[dict] = []

        for task in project_file.getTasks():
            # Skip the virtual root task (ID 0)
            if task.getID() is None or int(str(task.getID())) == 0:
                continue

            # Skip summary tasks — they become WBS containers
            child_tasks = task.getChildTasks()
            if child_tasks is not None and child_tasks.size() > 0:
                continue

            uid = str(task.getUniqueID() or task.getID())
            name = str(task.getName() or f"Activity {uid}")

            # Duration in days
            duration_days = 0
            dur = task.getDuration()
            if dur is not None:
                try:
                    from net.sf.mpxj import TimeUnit  # type: ignore[import]

                    dur_val = dur.convertUnits(TimeUnit.DAYS, project_file.getProjectProperties())
                    duration_days = max(0, round(float(str(dur_val.getDuration()))))
                except Exception:
                    try:
                        duration_days = max(0, round(float(str(dur.getDuration()))))
                    except Exception as e:
                        logger.debug(f"Duration parsing fallback failed: {e}")

            # Dates
            start_date = self._java_date_to_python(task.getStart())
            finish_date = self._java_date_to_python(task.getFinish())

            # Calendar
            cal_id = None
            task_cal = task.getEffectiveCalendar()
            if task_cal is not None:
                cal_id = str(task_cal.getUniqueID() or task_cal.getName() or "")
                if cal_id not in calendar_map:
                    cal_id = None

            # WBS
            wbs_code = str(task.getWBS() or "")
            wbs_path = self._build_wbs_path(task)

            # Status
            pct = 0.0
            try:
                pct_val = task.getPercentageComplete()
                if pct_val is not None:
                    pct = float(str(pct_val))
            except Exception as e:
                logger.debug(f"Could not parse percentage complete: {e}")

            if pct >= 100:
                status = "complete"
            elif pct > 0:
                status = "in_progress"
            else:
                status = "not_started"

            # Resource assignments
            resource_assignments: list[dict] = []
            try:
                for ra in task.getResourceAssignments():
                    res = ra.getResource()
                    if res is not None:
                        resource_assignments.append(
                            {
                                "resource_name": str(res.getName() or ""),
                                "units": float(str(ra.getUnits() or 100)) / 100.0,
                            }
                        )
            except Exception as e:
                logger.warning(f"Resource assignment parsing failed: {e}")

            activities.append(
                {
                    "original_id": uid,
                    "activity_code": str(task.getActivityID() or task.getID() or uid),
                    "name": name,
                    "duration_days": duration_days,
                    "start_date": start_date,
                    "finish_date": finish_date,
                    "calendar_id": cal_id,
                    "wbs_code": wbs_code if wbs_code else None,
                    "wbs_path": wbs_path if wbs_path else None,
                    "status": status,
                    "pct_complete": pct,
                    "resource_assignments": resource_assignments,
                }
            )

        return activities

    def _extract_relationships(self, project_file, activities_raw: list[dict]) -> list[dict]:
        """Extract predecessor relationships from the MPXJ project."""
        activity_ids = {a["original_id"] for a in activities_raw}
        relationships: list[dict] = []

        for task in project_file.getTasks():
            if task.getID() is None:
                continue

            uid = str(task.getUniqueID() or task.getID())
            if uid not in activity_ids:
                continue

            preds = task.getPredecessors()
            if preds is None:
                continue

            for rel in preds:
                pred_task = rel.getTargetTask()
                if pred_task is None:
                    continue

                pred_uid = str(pred_task.getUniqueID() or pred_task.getID())
                if pred_uid not in activity_ids:
                    continue

                # Map relationship type
                rel_type_obj = rel.getType()
                rel_type = "FS"
                if rel_type_obj is not None:
                    rel_type = _RELATION_TYPE_MAP.get(str(rel_type_obj.name()), "FS")

                # Lag in days
                lag = 0
                lag_dur = rel.getLag()
                if lag_dur is not None:
                    try:
                        from net.sf.mpxj import TimeUnit  # type: ignore[import]

                        lag_converted = lag_dur.convertUnits(
                            TimeUnit.DAYS, project_file.getProjectProperties()
                        )
                        lag = round(float(str(lag_converted.getDuration())))
                    except Exception:
                        try:
                            lag = round(float(str(lag_dur.getDuration())))
                        except Exception as e:
                            logger.warning(
                                f"Lag duration parsing failed - may affect CPM results: {e}"
                            )

                relationships.append(
                    {
                        "predecessor_id": pred_uid,
                        "successor_id": uid,
                        "type": rel_type,
                        "lag": lag,
                    }
                )

        return relationships

    def _extract_resources(self, project_file) -> list[dict]:
        """Extract resource definitions from the MPXJ project."""
        resources: list[dict] = []
        try:
            for res in project_file.getResources():
                if res.getID() is None or int(str(res.getID())) == 0:
                    continue
                resources.append(
                    {
                        "id": str(res.getUniqueID() or res.getID()),
                        "name": str(res.getName() or ""),
                        "type": str(res.getType() or "work"),
                    }
                )
        except Exception:
            logger.debug("Could not extract resources", exc_info=True)
        return resources

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _java_date_to_python(java_date) -> date | None:
        """Convert a Java Date to a Python date."""
        if java_date is None:
            return None
        try:
            from java.util import Calendar as JCal  # type: ignore[import]

            jc = JCal.getInstance()
            jc.setTime(java_date)
            return date(
                jc.get(JCal.YEAR),
                jc.get(JCal.MONTH) + 1,
                jc.get(JCal.DAY_OF_MONTH),
            )
        except Exception:
            return None

    @staticmethod
    def _build_wbs_path(task) -> str:
        """Walk up the task hierarchy to build a WBS path string."""
        parts: list[str] = []
        current = task
        max_depth = 100
        while current is not None and max_depth > 0:
            name = current.getName()
            if name:
                parts.append(str(name))
            current = current.getParentTask()
            max_depth -= 1
        parts.reverse()
        return "/".join(parts) if len(parts) > 1 else ""
