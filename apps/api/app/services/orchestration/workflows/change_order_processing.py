"""Change order impact analysis workflow."""

from __future__ import annotations

import asyncio
import logging

from app.services.agents.controls_agent import run_controls_agent
from app.services.agents.document_agent import run_document_agent
from app.services.agents.estimating_agent import run_estimating_agent
from app.services.agents.procurement_agent import run_procurement_agent
from app.services.agents.scheduling_agent import run_scheduling_agent

logger = logging.getLogger(__name__)


async def run_change_order_processing(
    project_id: str,
    change_order_data: dict,
) -> dict:
    """Change order impact analysis workflow.

    Steps:
    1. Document Agent parses change order scope
    2. Fan-out (parallel):
       a. Estimating Agent -> cost impact
       b. Scheduling Agent -> schedule impact
       c. Controls Agent -> risk exposure
    3. Fan-in -> aggregate results
    4. Procurement Agent evaluates material impact
    5. Generate change order package
    6. Pause for PM approval (human-in-the-loop)

    Partial results are preserved when individual steps fail.
    """
    steps_completed: list[dict] = []
    overall_status = "waiting_human"

    # ------------------------------------------------------------------
    # Step 1: Parse scope via Document Agent
    # ------------------------------------------------------------------
    logger.info(
        "CO step 1: Parse scope for project %s",
        project_id,
    )
    scope: dict = {}
    try:
        scope_result = await run_document_agent(
            document_id=change_order_data.get(
                "document_id",
                f"co-{project_id}",
            ),
            text_content=change_order_data.get(
                "description",
                "",
            ),
            filename=change_order_data.get(
                "filename",
                "change_order.pdf",
            ),
        )
        scope = {
            "agent_result": scope_result,
            "description": change_order_data.get(
                "description",
                "",
            ),
            "type": change_order_data.get(
                "type",
                "scope_change",
            ),
            "status": "parsed",
        }
        steps_completed.append(
            {
                "step": "parse_scope",
                "status": "completed",
            }
        )
        logger.info(
            "CO step 1 complete: scope parsed for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "CO step 1 failed for %s: %s",
            project_id,
            exc,
        )
        scope = {
            "description": change_order_data.get(
                "description",
                "",
            ),
            "type": change_order_data.get(
                "type",
                "scope_change",
            ),
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "parse_scope",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 2: Fan-out impact analysis (parallel)
    # ------------------------------------------------------------------
    logger.info("CO step 2: Fan-out impact analysis")

    async def _run_cost_impact() -> dict:
        logger.info(
            "CO step 2a: Cost impact for %s",
            project_id,
        )
        try:
            result = await run_estimating_agent(
                project_id=project_id,
                estimate_type=change_order_data.get(
                    "estimate_type",
                    "detailed",
                ),
                documents=change_order_data.get("documents"),
            )
            logger.info(
                "CO step 2a complete: cost impact for %s",
                project_id,
            )
            return {
                "result": result,
                "step_status": "completed",
            }
        except Exception as exc:
            logger.exception(
                "CO step 2a failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_status": "failed",
                "error": type(exc).__name__,
            }

    async def _run_schedule_impact() -> dict:
        logger.info(
            "CO step 2b: Schedule impact for %s",
            project_id,
        )
        try:
            result = await run_scheduling_agent(
                project_id=project_id,
                activities=change_order_data.get("activities"),
            )
            logger.info(
                "CO step 2b complete: schedule impact for %s",
                project_id,
            )
            return {
                "result": result,
                "step_status": "completed",
            }
        except Exception as exc:
            logger.exception(
                "CO step 2b failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_status": "failed",
                "error": type(exc).__name__,
            }

    async def _run_risk_exposure() -> dict:
        logger.info(
            "CO step 2c: Risk exposure for %s",
            project_id,
        )
        try:
            result = await run_controls_agent(
                project_id=project_id,
                bac=change_order_data.get(
                    "bac",
                    change_order_data.get(
                        "original_contract",
                        1_000_000,
                    ),
                ),
                pv=change_order_data.get("pv", 500_000),
                ev=change_order_data.get("ev", 450_000),
                ac=change_order_data.get("ac", 480_000),
                activities=change_order_data.get("activities"),
            )
            logger.info(
                "CO step 2c complete: risk exposure for %s",
                project_id,
            )
            return {
                "result": result,
                "step_status": "completed",
            }
        except Exception as exc:
            logger.exception(
                "CO step 2c failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_status": "failed",
                "error": type(exc).__name__,
            }

    cost_out, schedule_out, risk_out = await asyncio.gather(
        _run_cost_impact(),
        _run_schedule_impact(),
        _run_risk_exposure(),
    )

    cost_impact = cost_out["result"]
    schedule_impact = schedule_out["result"]
    risk_exposure = risk_out["result"]

    parallel_failed = any(o["step_status"] == "failed" for o in (cost_out, schedule_out, risk_out))
    steps_completed.append(
        {
            "step": "impact_analysis",
            "status": "completed" if not parallel_failed else "partial",
            "sub_steps": {
                "cost_impact": cost_out["step_status"],
                "schedule_impact": schedule_out["step_status"],
                "risk_exposure": risk_out["step_status"],
            },
        }
    )
    if parallel_failed:
        overall_status = "partial"

    logger.info("CO step 2 complete: fan-in aggregation done")

    # ------------------------------------------------------------------
    # Step 3: Material impact via Procurement Agent
    # ------------------------------------------------------------------
    logger.info("CO step 3: Material impact assessment")
    material_impact: dict = {}
    try:
        material_impact = await run_procurement_agent(
            project_id=project_id,
            materials=change_order_data.get("materials"),
        )
        steps_completed.append(
            {
                "step": "material_impact",
                "status": "completed",
            }
        )
        logger.info(
            "CO step 3 complete: material impact for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "CO step 3 failed for %s: %s",
            project_id,
            exc,
        )
        material_impact = {
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "material_impact",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 4: Generate CO package with computed fields
    # ------------------------------------------------------------------
    logger.info("CO step 4: Generate package")

    # Compute cost impact percentage from input data
    raw_cost = change_order_data.get("cost_impact", 0)
    original_contract = change_order_data.get("original_contract", 0)
    if original_contract > 0 and raw_cost:
        cost_percentage = round(raw_cost / original_contract * 100, 2)
    else:
        cost_percentage = 0.0

    cost_impact_summary = {
        "amount": raw_cost,
        "original_contract": original_contract,
        "percentage": cost_percentage,
        "agent_analysis": cost_impact,
    }

    # Compute risk exposure from input data
    schedule_days = change_order_data.get("schedule_impact_days", 0)
    risk_score = cost_percentage + (schedule_days * 0.5 if schedule_days else 0)
    if risk_score >= 30:
        risk_level = "high"
    elif risk_score >= 10:
        risk_level = "medium"
    else:
        risk_level = "low"

    risk_exposure_summary = {
        "risk_score": round(risk_score, 2),
        "risk_level": risk_level,
        "agent_analysis": risk_exposure,
    }

    package = {
        "project_id": project_id,
        "scope": scope,
        "cost_impact": cost_impact_summary,
        "schedule_impact": schedule_impact,
        "risk_exposure": risk_exposure_summary,
        "material_impact": material_impact,
        "steps_completed": steps_completed,
        "approval_required": True,
        "status": overall_status,
    }

    logger.info(
        "Change order processing %s for %s (awaiting approval)",
        overall_status,
        project_id,
    )
    return package
