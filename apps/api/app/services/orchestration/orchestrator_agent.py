"""Orchestrator service for managing workflow execution."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.orchestration.workflows.change_order_processing import (
    run_change_order_processing,
)
from app.services.orchestration.workflows.new_project_onboarding import (
    run_onboarding,
)
from app.services.orchestration.workflows.safety_incident_response import (
    run_safety_incident_response,
)

logger = logging.getLogger(__name__)

WORKFLOW_RUNNERS = {
    "new_project_onboarding": run_onboarding,
    "change_order_processing": run_change_order_processing,
    "safety_incident_response": run_safety_incident_response,
}


async def execute_workflow(
    workflow_type: str,
    project_id: str,
    input_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    """Execute a workflow by type.

    When a ``db`` session is provided, all database mutations made during
    the workflow are flushed inside a savepoint so that partial failures
    can be rolled back without discarding the outer transaction.

    Returns workflow result dict.
    """
    runner = WORKFLOW_RUNNERS.get(workflow_type)
    if not runner:
        return {
            "status": "failed",
            "error": f"Unknown workflow type: {workflow_type}",
        }

    logger.info(
        "Executing workflow %s for project %s",
        workflow_type,
        project_id,
    )

    try:
        if db is not None:
            # Use a savepoint so that on failure we can roll back only
            # the work done inside this workflow, not the whole session.
            async with db.begin_nested():
                result = await _dispatch_workflow(
                    runner,
                    workflow_type,
                    project_id,
                    input_data,
                )
                await db.flush()
        else:
            result = await _dispatch_workflow(
                runner,
                workflow_type,
                project_id,
                input_data,
            )

        return result

    except Exception as e:
        logger.exception(
            "Workflow %s failed: %s",
            workflow_type,
            str(e),
        )
        if db is not None:
            await db.rollback()
        return {
            "status": "failed",
            "error": "An internal error occurred while executing the workflow.",
        }


async def _dispatch_workflow(
    runner,
    workflow_type: str,
    project_id: str,
    input_data: dict,
) -> dict:
    """Dispatch to the correct workflow runner."""
    if workflow_type == "new_project_onboarding":
        return await runner(
            project_id=project_id,
            document_ids=input_data.get(
                "document_ids",
                [],
            ),
            input_data=input_data,
        )
    elif workflow_type == "change_order_processing":
        return await runner(
            project_id=project_id,
            change_order_data=input_data,
        )
    elif workflow_type == "safety_incident_response":
        return await runner(
            project_id=project_id,
            incident_data=input_data,
        )
    else:
        return {"status": "failed", "error": "Unknown"}
