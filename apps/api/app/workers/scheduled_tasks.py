"""Scheduled background tasks for data maintenance.

These async functions are designed to be called from a scheduler
(e.g. APScheduler, or a simple ``asyncio`` periodic loop).  They
do **not** implement the scheduler itself -- they are the *callables*
that a scheduler invokes.

Usage example with APScheduler::

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.workers.scheduled_tasks import (
        refresh_ppi_data,
        refresh_fred_price_data,
        cleanup_reconnect_tokens,
        generate_daily_risk_scores,
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_ppi_data, "interval", hours=24)
    scheduler.add_job(refresh_fred_price_data, "cron", hour=9, minute=0,
                      timezone="US/Eastern")
    scheduler.add_job(cleanup_reconnect_tokens, "interval", minutes=15)
    scheduler.add_job(generate_daily_risk_scores, "cron", hour=5, minute=30,
                      timezone="US/Eastern")
    scheduler.start()
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


async def refresh_ppi_data() -> dict[str, bool]:
    """Refresh BLS Producer Price Index data for all tracked series.

    Iterates over the configured series in
    ``cost_database._BLS_SERIES_MAP`` and fetches the latest PPI value
    for each.  Failures for individual series are logged but do not
    prevent other series from being refreshed.

    Returns:
        Dict mapping series key (e.g. ``"concrete"``) to ``True`` if
        the refresh succeeded, ``False`` otherwise.
    """
    try:
        from app.services.estimating.cost_database import (
            _BLS_SERIES_MAP,
            fetch_bls_ppi,
        )
    except ImportError:
        logger.error("Cannot import cost_database; PPI refresh skipped.")
        return {}

    results: dict[str, bool] = {}
    for category, series_id in _BLS_SERIES_MAP.items():
        try:
            data = await fetch_bls_ppi(series_id)
            if data and data.get("latest_value") is not None:
                results[category] = True
                logger.info(
                    "PPI refresh OK: %s (series %s) = %s",
                    category,
                    series_id,
                    data["latest_value"],
                )
            else:
                results[category] = False
                logger.warning(
                    "PPI refresh returned empty data for %s (series %s)",
                    category,
                    series_id,
                )
        except Exception as exc:
            results[category] = False
            logger.error(
                "PPI refresh failed for %s (series %s): %s",
                category,
                series_id,
                exc,
            )
    return results


async def cleanup_reconnect_tokens() -> None:
    """Clean up expired WebSocket reconnect tokens.

    Calls ``ws_manager.cleanup_expired_tokens()`` to remove stale
    entries from the in-memory reconnect-token store.
    """
    try:
        from app.services.realtime.websocket_server import ws_manager
    except ImportError:
        logger.error("Cannot import ws_manager; token cleanup skipped.")
        return

    try:
        ws_manager.cleanup_expired_tokens()
        logger.debug("WebSocket reconnect token cleanup completed.")
    except Exception as exc:
        logger.error("WebSocket token cleanup failed: %s", exc)


async def compute_daily_evm_snapshots(db_session) -> list[dict]:
    """Auto-compute EVM snapshots for all active projects.

    Queries active projects, retrieves their latest cost/schedule data,
    computes earned value metrics, and stores the resulting snapshots.

    Args:
        db_session: An async SQLAlchemy ``AsyncSession`` instance.

    Returns:
        List of dicts describing what was computed, one per project.
        Each dict has ``project_id``, ``success`` (bool), and optionally
        ``error`` or ``snapshot`` fields.
    """
    try:
        from sqlalchemy import and_, func, select

        from app.models.evm import ChangeOrder, EVMSnapshot
        from app.models.pay_application import PayApplication
        from app.models.project import Project
        from app.models.scheduling import ScheduleBaseline
        from app.services.controls.evm_engine import compute_evm_snapshot
    except ImportError as exc:
        logger.error("Cannot import dependencies for EVM snapshots: %s", exc)
        return []

    results: list[dict] = []

    try:
        # Fetch active projects
        stmt = select(Project).where(Project.status.in_(["active", "in_progress"]))
        query_result = await db_session.execute(stmt)
        projects = query_result.scalars().all()

        if not projects:
            logger.info("No active projects found for EVM snapshot computation.")
            return results

        today = date.today()

        for project in projects:
            project_id = project.id
            try:
                # Skip if a snapshot for today already exists
                dup_stmt = (
                    select(EVMSnapshot.id)
                    .where(
                        and_(
                            EVMSnapshot.project_id == project_id,
                            EVMSnapshot.snapshot_date == today,
                        )
                    )
                    .limit(1)
                )
                dup_result = await db_session.execute(dup_stmt)
                if dup_result.scalar_one_or_none() is not None:
                    results.append(
                        {
                            "project_id": str(project_id),
                            "success": True,
                            "skipped": "snapshot_already_exists",
                        }
                    )
                    continue

                # Fetch the most recent EVM snapshot for baseline BAC and
                # fallback values when source data is not yet available.
                latest_stmt = (
                    select(EVMSnapshot)
                    .where(EVMSnapshot.project_id == project_id)
                    .order_by(EVMSnapshot.snapshot_date.desc())
                    .limit(1)
                )
                latest_result = await db_session.execute(latest_stmt)
                latest_snapshot = latest_result.scalar_one_or_none()

                if latest_snapshot is None:
                    logger.info(
                        "No existing EVM data for project %s; skipping.",
                        project_id,
                    )
                    results.append(
                        {
                            "project_id": str(project_id),
                            "success": False,
                            "error": "No baseline EVM data available",
                        }
                    )
                    continue

                bac = latest_snapshot.bac

                # ----------------------------------------------------------
                # Aggregate AC (Actual Cost) from certified/paid pay apps
                # NOTE: AC = actual money spent by the contractor. We use the
                # *certified* pay-app amount as a proxy for actual cost.  In a
                # more mature deployment, AC should come from invoices or cost
                # ledger entries rather than pay applications.
                # ----------------------------------------------------------
                ac_query = select(
                    func.coalesce(
                        func.sum(PayApplication.total_completed_and_stored),
                        Decimal("0"),
                    )
                ).where(
                    PayApplication.project_id == project_id,
                    PayApplication.period_to <= today,
                    PayApplication.status.in_(["certified", "paid"]),
                )
                ac_result = await db_session.execute(ac_query)
                ac_from_pay_apps = ac_result.scalar() or Decimal("0")

                # Add approved change order costs to AC
                co_cost_query = select(
                    func.coalesce(
                        func.sum(ChangeOrder.cost_impact),
                        Decimal("0"),
                    )
                ).where(
                    ChangeOrder.project_id == project_id,
                    ChangeOrder.status.in_(["approved", "executed"]),
                )
                co_cost_result = await db_session.execute(co_cost_query)
                co_cost = co_cost_result.scalar() or Decimal("0")

                # AC includes change order impacts (cost overruns/savings)
                ac_from_pay_apps = ac_from_pay_apps + co_cost

                # Use pay-app-based AC if available, otherwise fall back
                ac = ac_from_pay_apps if ac_from_pay_apps > 0 else latest_snapshot.ac

                # ----------------------------------------------------------
                # Aggregate EV (Earned Value) from SOV completed work
                # ----------------------------------------------------------
                ev_query = select(
                    func.coalesce(
                        func.sum(PayApplication.total_completed_and_stored),
                        Decimal("0"),
                    )
                ).where(
                    PayApplication.project_id == project_id,
                    PayApplication.period_to <= today,
                    PayApplication.status.in_(["submitted", "reviewed", "certified", "paid"]),
                )
                ev_result = await db_session.execute(ev_query)
                ev_from_pay_apps = ev_result.scalar() or Decimal("0")

                # Use pay-app EV if available, otherwise fall back
                ev = ev_from_pay_apps if ev_from_pay_apps > 0 else latest_snapshot.ev

                # ----------------------------------------------------------
                # Compute PV (Planned Value) from schedule baseline
                # ----------------------------------------------------------
                # PV = BAC * (elapsed_duration / total_duration) for linear
                # approximation when no detailed PV curve exists.
                pv = latest_snapshot.pv  # default: previous snapshot

                baseline_stmt = (
                    select(ScheduleBaseline)
                    .where(ScheduleBaseline.project_id == project_id)
                    .order_by(ScheduleBaseline.created_at.desc())
                    .limit(1)
                )
                baseline_result = await db_session.execute(baseline_stmt)
                baseline = baseline_result.scalar_one_or_none()

                if baseline and baseline.total_duration_days and project.start_date:
                    elapsed_days = (today - project.start_date).days
                    total_days = baseline.total_duration_days
                    if total_days > 0 and elapsed_days >= 0:
                        # Linear PV approximation — capped at BAC
                        pv_ratio = min(Decimal(elapsed_days) / Decimal(total_days), Decimal("1"))
                        pv = (bac * pv_ratio).quantize(Decimal("0.01"))

                # Adjust BAC for approved change orders
                adjusted_bac = bac + co_cost

                # Compute derived EVM metrics
                metrics = await compute_evm_snapshot(
                    bac=adjusted_bac,
                    pv=pv,
                    ev=ev,
                    ac=ac,
                )

                # Compute percent complete from EV/BAC
                percent_complete = (
                    (ev / adjusted_bac * 100) if adjusted_bac > 0 else Decimal("0")
                ).quantize(Decimal("0.01"))

                new_snapshot = EVMSnapshot(
                    project_id=project_id,
                    snapshot_date=today,
                    bac=adjusted_bac,
                    pv=pv,
                    ev=ev,
                    ac=ac,
                    sv=Decimal(str(metrics["sv"]))
                    if metrics.get("sv") is not None
                    else Decimal("0"),
                    cv=Decimal(str(metrics["cv"]))
                    if metrics.get("cv") is not None
                    else Decimal("0"),
                    spi=Decimal(str(metrics["spi"]))
                    if metrics.get("spi") is not None
                    else Decimal("0"),
                    cpi=Decimal(str(metrics["cpi"]))
                    if metrics.get("cpi") is not None
                    else Decimal("0"),
                    eac=Decimal(str(metrics["eac"]))
                    if metrics.get("eac") is not None
                    else Decimal("0"),
                    etc=Decimal(str(metrics["etc"]))
                    if metrics.get("etc") is not None
                    else Decimal("0"),
                    vac=Decimal(str(metrics["vac"]))
                    if metrics.get("vac") is not None
                    else Decimal("0"),
                    tcpi=Decimal(str(metrics["tcpi"]))
                    if metrics.get("tcpi") is not None
                    else Decimal("0"),
                    percent_complete=percent_complete,
                    data_date=today,
                    metadata_={
                        "data_source": "aggregated",
                        "ac_from_pay_apps": float(ac_from_pay_apps),
                        "ev_from_pay_apps": float(ev_from_pay_apps),
                        "co_cost_adjustment": float(co_cost),
                        "has_schedule_baseline": baseline is not None,
                    },
                )
                db_session.add(new_snapshot)

                results.append(
                    {
                        "project_id": str(project_id),
                        "success": True,
                        "snapshot": {
                            "spi": float(metrics["spi"])
                            if metrics.get("spi") is not None
                            else None,
                            "cpi": float(metrics["cpi"])
                            if metrics.get("cpi") is not None
                            else None,
                            "eac": float(metrics["eac"])
                            if metrics.get("eac") is not None
                            else None,
                        },
                    }
                )
                logger.info(
                    "EVM snapshot computed for project %s: SPI=%s, CPI=%s",
                    project_id,
                    metrics["spi"],
                    metrics["cpi"],
                )

            except Exception as exc:
                logger.error(
                    "EVM snapshot failed for project %s: %s",
                    project_id,
                    exc,
                )
                results.append(
                    {
                        "project_id": str(project_id),
                        "success": False,
                        "error": str(exc),
                    }
                )

        # Commit all new snapshots in one transaction
        await db_session.commit()

    except Exception as exc:
        logger.error("EVM snapshot batch job failed: %s", exc)
        await db_session.rollback()

    return results


async def generate_all_weekly_briefs() -> list[dict]:
    """Generate intelligence briefs for all active projects.

    Iterates over active projects and runs the weekly brief agent for each.
    Per-project failures are logged but do not prevent other projects from
    being processed.

    Returns:
        List of dicts with ``project_id``, ``success``, and optionally ``error``.
    """
    results: list[dict] = []

    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models.project import Project
        from app.services.agents.weekly_brief_agent import generate_weekly_brief
    except ImportError as exc:
        logger.error("Cannot import dependencies for weekly briefs: %s", exc)
        return results

    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as db:
            stmt = select(Project).where(Project.status.in_(["active", "in_progress"]))
            query_result = await db.execute(stmt)
            projects = query_result.scalars().all()

            if not projects:
                logger.info("No active projects for weekly brief generation.")
                return results

            for project in projects:
                try:
                    project_data = {
                        "name": project.name,
                        "project_number": project.project_number,
                        "type": project.type or "commercial",
                        "address": project.address or "",
                        "contract_value": str(project.contract_value or 0),
                        "start_date": (
                            project.start_date.isoformat() if project.start_date else None
                        ),
                        "end_date": (project.end_date.isoformat() if project.end_date else None),
                    }

                    org_id = str(project.org_id) if project.org_id else None
                    brief_result = await generate_weekly_brief(
                        project_id=str(project.id),
                        project_data=project_data,
                        org_id=org_id,
                    )

                    # Save to DB
                    from app.models.evm import IntelligenceBrief

                    brief = IntelligenceBrief(
                        project_id=project.id,
                        report_date=date.today(),
                        overall_health_score=brief_result.get("overall_health_score", 50),
                        project_status=brief_result.get("project_status", "YELLOW"),
                        schedule_health_score=brief_result.get("schedule_health_score", 50),
                        cost_health_score=brief_result.get("cost_health_score", 50),
                        risk_score=brief_result.get("risk_score", 50),
                        productivity_score=brief_result.get("productivity_score", 50),
                        executive_summary=brief_result.get("executive_summary", ""),
                        schedule_intelligence=brief_result.get("schedule_intelligence", {}),
                        cost_intelligence=brief_result.get("cost_intelligence", {}),
                        risk_intelligence=brief_result.get("risk_intelligence", {}),
                        productivity_intelligence=brief_result.get("productivity_intelligence", {}),
                        action_items=brief_result.get("action_items", []),
                        metrics_dashboard=brief_result.get("metrics_dashboard", {}),
                        narrative_report=brief_result.get("narrative_report", ""),
                        guardrails_result=brief_result.get("guardrails_result", {}),
                    )
                    db.add(brief)
                    await db.commit()

                    results.append(
                        {
                            "project_id": str(project.id),
                            "success": True,
                        }
                    )
                    logger.info("Weekly brief generated for project %s", project.id)

                except Exception as exc:
                    await db.rollback()
                    logger.error("Weekly brief failed for project %s: %s", project.id, exc)
                    results.append(
                        {
                            "project_id": str(project.id),
                            "success": False,
                            "error": str(exc),
                        }
                    )

        await engine.dispose()

    except Exception as exc:
        logger.error("Weekly brief batch job failed: %s", exc)

    return results


async def refresh_fred_price_data(db_session=None) -> dict[str, bool]:
    """Refresh FRED price data for all tracked construction material series.

    Scheduled to run daily at 9:00 AM ET.  Fetches the latest 36 months
    of data for each series in ``FRED_SERIES_MAP`` and optionally
    persists to the ``fred_price_history`` table.

    Args:
        db_session: Optional async SQLAlchemy session for persistence.

    Returns:
        Dict mapping series_id to True if refresh succeeded.
    """
    try:
        from app.services.procurement.price_forecaster import refresh_fred_data
    except ImportError:
        logger.error("Cannot import price_forecaster; FRED refresh skipped.")
        return {}

    try:
        results = await refresh_fred_data(db_session=db_session)
        succeeded = sum(1 for v in results.values() if v)
        logger.info(
            "FRED scheduled refresh complete: %d/%d series OK",
            succeeded,
            len(results),
        )
        return results
    except Exception as exc:
        logger.error("FRED scheduled refresh failed: %s", exc)
        return {}


async def generate_daily_risk_scores() -> list[dict]:
    """Generate predictive safety risk scores for all active projects.

    Scheduled to run daily at 5:30 AM project-local-time.  Iterates
    over active projects, computes risk scores using OSHA enforcement
    data + weather + schedule, stores results, and generates safety
    briefings.

    Returns:
        List of dicts with ``project_id``, ``success``, ``overall_score``.
    """
    results: list[dict] = []

    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models.productivity import DailyLog
        from app.models.project import Project
        from app.models.scheduling import ScheduleActivity
        from app.services.safety.predictive_risk import (
            PredictiveRiskEngine,
            store_risk_score,
        )
    except ImportError as exc:
        logger.error("Cannot import dependencies for risk scores: %s", exc)
        return results

    try:
        engine_db = create_async_engine(settings.DATABASE_URL)
        async_session = async_sessionmaker(engine_db, expire_on_commit=False)

        risk_engine = PredictiveRiskEngine()
        today = date.today()

        async with async_session() as db:
            # Fetch active projects
            stmt = select(Project).where(Project.status.in_(["active", "in_progress"]))
            query_result = await db.execute(stmt)
            projects = query_result.scalars().all()

            if not projects:
                logger.info("No active projects for risk score generation.")
                return results

            for project in projects:
                try:
                    project_dict = {
                        "name": project.name,
                        "type": project.type or "commercial",
                        "address": project.address or "",
                        "start_date": (
                            project.start_date.isoformat() if project.start_date else None
                        ),
                        "naics_code": (project.metadata_ or {}).get("naics_code", ""),
                    }

                    # Get today's activities
                    act_stmt = select(ScheduleActivity).where(
                        ScheduleActivity.project_id == project.id,
                        ScheduleActivity.start_date <= today,
                        ScheduleActivity.finish_date >= today,
                    )
                    act_result = await db.execute(act_stmt)
                    activities = [
                        {"name": a.name, "activity_code": a.activity_code}
                        for a in act_result.scalars().all()
                    ]

                    # Get daily log
                    log_stmt = (
                        select(DailyLog)
                        .where(
                            DailyLog.project_id == project.id,
                            DailyLog.log_date == today,
                        )
                        .limit(1)
                    )
                    log_result = await db.execute(log_stmt)
                    log = log_result.scalar_one_or_none()
                    daily_log = (
                        {"crew_count": log.crew_count, "manpower_by_trade": log.manpower_by_trade}
                        if log
                        else None
                    )

                    # Try to get weather
                    weather = None
                    try:
                        lat = float((project.metadata_ or {}).get("latitude", 0))
                        lon = float((project.metadata_ or {}).get("longitude", 0))
                        if lat != 0 and lon != 0:
                            from app.services.scheduling.weather_service import get_weather_forecast

                            weather = await get_weather_forecast(
                                lat,
                                lon,
                                today.isoformat(),
                                today.isoformat(),
                            )
                    except Exception as weather_exc:
                        logger.warning(
                            "Weather fetch failed for project %s: %s",
                            project.id,
                            weather_exc,
                        )

                    # Compute risk score
                    risk = await risk_engine.calculate_daily_risk_score(
                        db=db,
                        project_id=str(project.id),
                        project=project_dict,
                        weather=weather,
                        today_activities=activities,
                        daily_log=daily_log,
                    )

                    # Generate briefing
                    briefing = await risk_engine.generate_safety_briefing(
                        risk_result=risk,
                        project=project_dict,
                        weather=weather,
                        today_activities=activities,
                    )

                    # Store
                    await store_risk_score(db, risk)
                    await db.flush()

                    # Update with briefing
                    from app.models.osha import DailyRiskScore

                    score_stmt = (
                        select(DailyRiskScore)
                        .where(
                            DailyRiskScore.project_id == project.id,
                            DailyRiskScore.score_date == today,
                        )
                        .order_by(DailyRiskScore.created_at.desc())
                        .limit(1)
                    )
                    score_result = await db.execute(score_stmt)
                    record = score_result.scalar_one_or_none()
                    if record:
                        record.safety_briefing = briefing

                    results.append(
                        {
                            "project_id": str(project.id),
                            "success": True,
                            "overall_score": risk.overall_score,
                        }
                    )
                    logger.info(
                        "Risk score generated for project %s: %d/100",
                        project.id,
                        risk.overall_score,
                    )

                except Exception as exc:
                    logger.error("Risk score failed for project %s: %s", project.id, exc)
                    results.append(
                        {
                            "project_id": str(project.id),
                            "success": False,
                            "error": str(exc),
                        }
                    )

            await db.commit()

        await engine_db.dispose()

    except Exception as exc:
        logger.error("Risk score batch job failed: %s", exc)

    return results


async def purge_old_audit_logs(db_session=None) -> int:
    """Delete audit logs respecting OSHA retention requirements. Runs monthly.

    Retention policy (per OSHA 29 CFR 1904 and construction industry
    best practice):
    - Safety-related entries (action contains "safety" or "alert"):
      retained for 7 years (2555 days).
    - All other entries: retained for 2 years (730 days).

    Args:
        db_session: Optional async SQLAlchemy session. If not provided,
                    creates a new engine/session from settings.

    Returns:
        Number of rows deleted.
    """
    try:
        from sqlalchemy import and_, delete, or_

        from app.models.audit import AuditLog
    except ImportError as exc:
        logger.error("Cannot import dependencies for audit purge: %s", exc)
        return 0

    cutoff_general = datetime.now(UTC) - timedelta(days=730)  # 2 years
    # OSHA 29 CFR 1904 requires safety/injury records to be retained
    # for at least 5 years; we use 7 years as a conservative margin.
    cutoff_safety = datetime.now(UTC) - timedelta(days=2555)  # 7 years

    async def _execute_purge(db) -> int:
        # Safety-related entries: only purge if older than 7 years
        safety_filter = or_(
            AuditLog.action.ilike("%safety%"),
            AuditLog.action.ilike("%alert%"),
        )
        result_safety = await db.execute(
            delete(AuditLog).where(and_(safety_filter, AuditLog.timestamp < cutoff_safety))
        )
        # Non-safety entries: purge if older than 2 years
        result_general = await db.execute(
            delete(AuditLog).where(and_(~safety_filter, AuditLog.timestamp < cutoff_general))
        )
        total = result_safety.rowcount + result_general.rowcount
        logger.info(
            "Purged %d audit log entries (%d safety >7yr, %d general >2yr)",
            total,
            result_safety.rowcount,
            result_general.rowcount,
        )
        return total

    try:
        if db_session:
            return await _execute_purge(db_session)

        # No session provided -- create our own
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings

        engine = create_async_engine(settings.DATABASE_URL)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as db:
            deleted = await _execute_purge(db)
            await db.commit()
        await engine.dispose()
        return deleted

    except Exception as exc:
        logger.error("Audit log purge failed: %s", exc)
        return 0
