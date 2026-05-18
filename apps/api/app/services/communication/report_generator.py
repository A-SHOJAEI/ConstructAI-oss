"""Daily report generation with template rendering."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


async def generate_daily_report(
    project_id: str,
    report_date: date,
    daily_log: dict | None = None,
    evm_snapshot: dict | None = None,
    safety_events: list[dict] | None = None,
    weather: dict | None = None,
) -> dict:
    """Generate a daily construction report.

    Aggregates data from multiple sources into a structured
    report with markdown and optional HTML/PDF rendering.

    Returns dict with content_markdown, sections, etc.
    """
    sections: dict[str, dict[str, Any]] = {}

    # Header section
    sections["header"] = {
        "project_id": project_id,
        "report_date": report_date.isoformat(),
        "generated_by": "ConstructAI",
    }

    # Weather section
    sections["weather"] = weather or {
        "conditions": "Not available",
        "temperature_f": None,
        "precipitation": None,
    }

    # Workforce section
    if daily_log:
        sections["workforce"] = {
            "crew_count": daily_log.get("crew_count", 0),
            "work_hours": str(daily_log.get("work_hours", 0)),
            "activities": daily_log.get("activities_completed", []),
            "delays": daily_log.get("delays", []),
        }
    else:
        sections["workforce"] = {
            "crew_count": 0,
            "work_hours": "0",
            "activities": [],
            "delays": [],
        }

    # Progress section (EVM)
    if evm_snapshot:
        sections["progress"] = {
            "percent_complete": str(evm_snapshot.get("percent_complete", 0)),
            "spi": str(evm_snapshot.get("spi", 0)),
            "cpi": str(evm_snapshot.get("cpi", 0)),
            "status": _progress_status(evm_snapshot),
        }
    else:
        sections["progress"] = {
            "percent_complete": "N/A",
            "spi": "N/A",
            "cpi": "N/A",
            "status": "No EVM data available",
        }

    # Safety section
    sections["safety"] = {
        "incidents": len(safety_events or []),
        "events": safety_events or [],
    }

    # Build markdown
    md = _build_markdown(sections, report_date)

    logger.info(
        "Daily report generated for project %s, date %s",
        project_id,
        report_date,
    )

    return {
        "content_markdown": md,
        "sections": sections,
        "status": "draft",
    }


def _progress_status(evm: dict) -> str:
    """Determine progress status from EVM data."""
    spi = float(evm.get("spi", 1))
    cpi = float(evm.get("cpi", 1))
    if spi >= 0.95 and cpi >= 0.95:
        return "On Track"
    if spi < 0.9 or cpi < 0.9:
        return "At Risk"
    return "Monitor"


def _build_markdown(
    sections: dict,
    report_date: date,
) -> str:
    """Build markdown content from sections."""
    lines = [
        f"# Daily Construction Report - {report_date}",
        "",
    ]

    # Weather
    w = sections.get("weather", {})
    lines.append("## Weather")
    lines.append(f"- Conditions: {w.get('conditions', 'N/A')}")
    if w.get("temperature_f"):
        lines.append(f"- Temperature: {w['temperature_f']}F")
    lines.append("")

    # Workforce
    wf = sections.get("workforce", {})
    lines.append("## Workforce")
    lines.append(f"- Crew Count: {wf.get('crew_count', 0)}")
    lines.append(f"- Work Hours: {wf.get('work_hours', 0)}")
    activities = wf.get("activities", [])
    if activities:
        lines.append("### Activities Completed")
        for act in activities:
            desc = act if isinstance(act, str) else act.get("description", str(act))
            lines.append(f"- {desc}")
    lines.append("")

    # Progress
    prog = sections.get("progress", {})
    lines.append("## Project Progress")
    lines.append(f"- Completion: {prog.get('percent_complete', 'N/A')}")
    lines.append(f"- SPI: {prog.get('spi', 'N/A')}")
    lines.append(f"- CPI: {prog.get('cpi', 'N/A')}")
    lines.append(f"- Status: {prog.get('status', 'N/A')}")
    lines.append("")

    # Safety
    s = sections.get("safety", {})
    lines.append("## Safety")
    lines.append(f"- Incidents: {s.get('incidents', 0)}")
    lines.append("")

    return "\n".join(lines)
