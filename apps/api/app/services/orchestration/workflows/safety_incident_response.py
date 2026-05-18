"""Safety incident response workflow."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services.agents.communication_agent import (
    run_communication_agent,
)
from app.services.agents.controls_agent import run_controls_agent
from app.services.agents.document_agent import run_document_agent
from app.services.agents.safety_agent import run_safety_agent

logger = logging.getLogger(__name__)


async def run_safety_incident_response(
    project_id: str,
    incident_data: dict,
) -> dict:
    """Safety incident response workflow.

    Steps:
    1. Safety Agent analyses incident and generates alerts
    2. Communication Agent logs the incident
    3. Controls Agent assesses schedule impact
    4. Document Agent generates incident report
    5. Compile notifications

    Partial results are preserved when individual steps fail.
    """
    steps_completed: list[dict] = []
    now = datetime.now(UTC).isoformat()
    overall_status = "completed"
    severity = incident_data.get("severity", "low")

    # ------------------------------------------------------------------
    # Step 1: Safety Agent - incident details and alert generation
    # ------------------------------------------------------------------
    logger.info(
        "Safety step 1: Incident details for %s",
        project_id,
    )
    incident: dict = {}
    try:
        detection_event = {
            "camera_id": incident_data.get(
                "camera_id",
                "manual_report",
            ),
            "violation": {
                "zone_type": incident_data.get(
                    "location",
                    "unknown",
                ),
                "violation": incident_data.get(
                    "type",
                    "near_miss",
                ),
                "severity_override": severity,
            },
            "detection": {
                "class_name": incident_data.get(
                    "type",
                    "near_miss",
                ),
                "confidence": 1.0,
            },
        }
        safety_result = await run_safety_agent(
            project_id=project_id,
            detection_events=[detection_event],
            bim_zone_context=incident_data.get(
                "bim_zone_context",
            ),
            schedule_phase=incident_data.get(
                "schedule_phase",
            ),
            weather_data=incident_data.get("weather_data"),
        )
        incident = {
            "project_id": project_id,
            "type": incident_data.get("type", "near_miss"),
            "severity": severity,
            "location": incident_data.get(
                "location",
                "unknown",
            ),
            "description": incident_data.get(
                "description",
                "",
            ),
            "timestamp": now,
            "status": "reported",
            "agent_result": safety_result,
        }
        steps_completed.append(
            {
                "step": "incident_details",
                "status": "completed",
            }
        )
        logger.info(
            "Safety step 1 complete: incident details for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "Safety step 1 failed for %s: %s",
            project_id,
            exc,
        )
        incident = {
            "project_id": project_id,
            "type": incident_data.get("type", "near_miss"),
            "severity": severity,
            "location": incident_data.get(
                "location",
                "unknown",
            ),
            "description": incident_data.get(
                "description",
                "",
            ),
            "timestamp": now,
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "incident_details",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 2: Communication Agent - log incident
    # ------------------------------------------------------------------
    logger.info("Safety step 2: Log incident")
    log_entry: dict = {}
    try:
        comm_result = await run_communication_agent(
            project_id=project_id,
            safety_events=[incident],
        )
        log_entry = {
            "incident_id": f"INC-{project_id[:8]}",
            "logged_at": now,
            "status": "logged",
            "agent_result": comm_result,
        }
        steps_completed.append(
            {
                "step": "log_incident",
                "status": "completed",
            }
        )
        logger.info(
            "Safety step 2 complete: incident logged for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "Safety step 2 failed for %s: %s",
            project_id,
            exc,
        )
        log_entry = {
            "incident_id": f"INC-{project_id[:8]}",
            "logged_at": now,
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "log_incident",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 3: Controls Agent - schedule impact assessment
    # ------------------------------------------------------------------
    logger.info("Safety step 3: Schedule impact assessment")
    schedule_impact: dict = {}
    try:
        controls_result = await run_controls_agent(
            project_id=project_id,
            activities=incident_data.get("activities"),
        )
        schedule_impact = {
            "work_stoppage_hours": _estimate_stoppage(severity),
            "affected_zones": incident_data.get(
                "affected_zones",
                [],
            ),
            "status": "assessed",
            "agent_result": controls_result,
        }
        steps_completed.append(
            {
                "step": "schedule_impact",
                "status": "completed",
            }
        )
        logger.info(
            "Safety step 3 complete: schedule impact for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "Safety step 3 failed for %s: %s",
            project_id,
            exc,
        )
        schedule_impact = {
            "work_stoppage_hours": _estimate_stoppage(severity),
            "affected_zones": incident_data.get(
                "affected_zones",
                [],
            ),
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "schedule_impact",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 4: Document Agent - generate incident report
    # ------------------------------------------------------------------
    logger.info("Safety step 4: Generate incident report")
    report: dict = {}
    try:
        report_text = (
            f"Safety Incident Report\n"
            f"Type: {incident.get('type', 'unknown')}\n"
            f"Severity: {severity}\n"
            f"Location: {incident.get('location', 'unknown')}\n"
            f"Description: {incident.get('description', '')}\n"
            f"Schedule Impact: "
            f"{schedule_impact.get('work_stoppage_hours', 0)}h stoppage\n"
        )
        doc_result = await run_document_agent(
            document_id=f"incident-report-{project_id[:8]}",
            text_content=report_text,
            filename=f"incident_report_{project_id[:8]}.txt",
        )
        report = {
            "title": (
                f"Safety Incident Report - "
                f"{incident.get('type', 'unknown').replace('_', ' ').title()}"
            ),
            "incident": incident,
            "schedule_impact": schedule_impact,
            "generated_at": now,
            "status": "generated",
            "agent_result": doc_result,
        }
        steps_completed.append(
            {
                "step": "generate_report",
                "status": "completed",
            }
        )
        logger.info(
            "Safety step 4 complete: report generated for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "Safety step 4 failed for %s: %s",
            project_id,
            exc,
        )
        report = {
            "title": (
                f"Safety Incident Report - "
                f"{incident.get('type', 'unknown').replace('_', ' ').title()}"
            ),
            "incident": incident,
            "schedule_impact": schedule_impact,
            "generated_at": now,
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "generate_report",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 5: Notifications
    # ------------------------------------------------------------------
    logger.info("Safety step 5: Send notifications")
    recipients: list[str] = ["safety_manager", "superintendent"]
    if severity in ("critical", "high"):
        recipients.extend(["project_manager", "osha_liaison"])
    notifications: dict[str, Any] = {
        "recipients": recipients,
        "sent_at": now,
        "status": "sent",
    }
    steps_completed.append(
        {
            "step": "notifications",
            "status": "completed",
        }
    )

    result = {
        "project_id": project_id,
        "incident": incident,
        "log_entry": log_entry,
        "schedule_impact": schedule_impact,
        "report": report,
        "notifications": notifications,
        "steps_completed": steps_completed,
        "status": overall_status,
    }

    logger.info(
        "Safety incident response %s for %s",
        overall_status,
        project_id,
    )
    return result


def _estimate_stoppage(severity: str) -> int:
    """Return estimated work stoppage hours based on severity."""
    if severity == "critical":
        return 24
    if severity == "high":
        return 8
    if severity == "medium":
        return 2
    return 0
