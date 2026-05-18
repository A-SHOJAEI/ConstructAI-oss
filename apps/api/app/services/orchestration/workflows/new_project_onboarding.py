"""Full project onboarding workflow."""

from __future__ import annotations

import asyncio
import logging

from app.services.agents.document_agent import run_document_agent
from app.services.agents.estimating_agent import run_estimating_agent
from app.services.agents.logistics_agent import run_logistics_agent
from app.services.agents.procurement_agent import run_procurement_agent
from app.services.agents.scheduling_agent import run_scheduling_agent

logger = logging.getLogger(__name__)


async def run_onboarding(
    project_id: str,
    document_ids: list[str] | None = None,
    input_data: dict | None = None,
) -> dict:
    """Full project onboarding workflow.

    Steps:
    1. Document Agent classifies and indexes uploaded documents
    2-4. (Parallel) Estimating, Scheduling, and Logistics agents
    5. Procurement Agent sets up material monitoring
    6. Compile into project brief

    All agents are called via their service functions.
    Partial results are preserved when later steps fail.
    """
    steps_completed: list[dict] = []
    results: dict = {}
    input_data = input_data or {}
    overall_status = "completed"

    # ------------------------------------------------------------------
    # Step 1: Document classification (sequential - needed by later steps)
    # ------------------------------------------------------------------
    logger.info(
        "Onboarding step 1: Document classification for %s",
        project_id,
    )
    try:
        doc_ids = document_ids or []
        doc_results_list: list[dict] = []
        for doc_id in doc_ids:
            doc_result = await run_document_agent(
                document_id=doc_id,
                text_content=input_data.get(
                    "text_content",
                    "",
                ),
                filename=input_data.get(
                    "filename",
                    f"document_{doc_id}",
                ),
            )
            doc_results_list.append(doc_result)

        doc_result_summary = {
            "classified_documents": len(doc_results_list),
            "document_results": doc_results_list,
            "status": "completed",
        }
        results["documents"] = doc_result_summary
        steps_completed.append(
            {
                "step": "document_classification",
                "status": "completed",
            }
        )
        logger.info(
            "Onboarding step 1 complete: classified %d documents",
            len(doc_results_list),
        )
    except Exception as exc:
        logger.exception(
            "Onboarding step 1 failed for %s: %s",
            project_id,
            exc,
        )
        results["documents"] = {
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "document_classification",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Steps 2-4: Parallel - Estimating, Scheduling, Logistics
    # ------------------------------------------------------------------
    logger.info(
        "Onboarding steps 2-4: Parallel agents for %s",
        project_id,
    )

    async def _run_estimating() -> dict:
        logger.info(
            "Onboarding step 2: Cost estimation for %s",
            project_id,
        )
        try:
            result = await run_estimating_agent(
                project_id=project_id,
                estimate_type=input_data.get(
                    "estimate_type",
                    "conceptual",
                ),
                documents=input_data.get("documents"),
            )
            logger.info(
                "Onboarding step 2 complete: estimation for %s",
                project_id,
            )
            return {
                "result": result,
                "step_record": {
                    "step": "cost_estimation",
                    "status": "completed",
                },
            }
        except Exception as exc:
            logger.exception(
                "Onboarding step 2 failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_record": {
                    "step": "cost_estimation",
                    "status": "failed",
                    "error": type(exc).__name__,
                },
            }

    async def _run_scheduling() -> dict:
        logger.info(
            "Onboarding step 3: Schedule analysis for %s",
            project_id,
        )
        try:
            result = await run_scheduling_agent(
                project_id=project_id,
                activities=input_data.get("activities"),
            )
            logger.info(
                "Onboarding step 3 complete: scheduling for %s",
                project_id,
            )
            return {
                "result": result,
                "step_record": {
                    "step": "schedule_analysis",
                    "status": "completed",
                },
            }
        except Exception as exc:
            logger.exception(
                "Onboarding step 3 failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_record": {
                    "step": "schedule_analysis",
                    "status": "failed",
                    "error": type(exc).__name__,
                },
            }

    async def _run_logistics() -> dict:
        logger.info(
            "Onboarding step 4: Site layout for %s",
            project_id,
        )
        try:
            result = await run_logistics_agent(
                project_id=project_id,
                site_data=input_data.get("site_data"),
            )
            logger.info(
                "Onboarding step 4 complete: site layout for %s",
                project_id,
            )
            return {
                "result": result,
                "step_record": {
                    "step": "site_layout",
                    "status": "completed",
                },
            }
        except Exception as exc:
            logger.exception(
                "Onboarding step 4 failed for %s: %s",
                project_id,
                exc,
            )
            return {
                "result": {
                    "status": "failed",
                    "error": type(exc).__name__,
                },
                "step_record": {
                    "step": "site_layout",
                    "status": "failed",
                    "error": type(exc).__name__,
                },
            }

    estimate_out, schedule_out, layout_out = await asyncio.gather(
        _run_estimating(),
        _run_scheduling(),
        _run_logistics(),
    )

    results["estimate"] = estimate_out["result"]
    steps_completed.append(estimate_out["step_record"])

    results["schedule"] = schedule_out["result"]
    steps_completed.append(schedule_out["step_record"])

    results["site_layout"] = layout_out["result"]
    steps_completed.append(layout_out["step_record"])

    if any(
        s["status"] == "failed"
        for s in (
            estimate_out["step_record"],
            schedule_out["step_record"],
            layout_out["step_record"],
        )
    ):
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Step 5: Procurement setup (sequential - depends on prior results)
    # ------------------------------------------------------------------
    logger.info(
        "Onboarding step 5: Procurement setup for %s",
        project_id,
    )
    try:
        procurement_result = await run_procurement_agent(
            project_id=project_id,
            materials=input_data.get("materials"),
        )
        results["procurement"] = procurement_result
        steps_completed.append(
            {
                "step": "procurement_setup",
                "status": "completed",
            }
        )
        logger.info(
            "Onboarding step 5 complete: procurement for %s",
            project_id,
        )
    except Exception as exc:
        logger.exception(
            "Onboarding step 5 failed for %s: %s",
            project_id,
            exc,
        )
        results["procurement"] = {
            "status": "failed",
            "error": type(exc).__name__,
        }
        steps_completed.append(
            {
                "step": "procurement_setup",
                "status": "failed",
                "error": type(exc).__name__,
            }
        )
        overall_status = "partial"

    # ------------------------------------------------------------------
    # Compile project brief
    # ------------------------------------------------------------------
    brief = {
        "project_id": project_id,
        "steps_completed": steps_completed,
        "results": results,
        "status": overall_status,
    }

    logger.info(
        "Onboarding %s for project %s: %d steps (%s)",
        overall_status,
        project_id,
        len(steps_completed),
        ", ".join(s["step"] for s in steps_completed),
    )
    return brief
