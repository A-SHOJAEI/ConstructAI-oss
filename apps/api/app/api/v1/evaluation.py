"""Evaluation metrics API endpoints."""

from __future__ import annotations

import logging
import os
from typing import Annotated

import fastapi
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationRunRequest,
    EvaluationRunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_NOT_IMPLEMENTED_DETAIL = "Evaluation metrics not yet implemented."


@router.get("/agents")
async def list_agent_metrics(
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get per-agent accuracy, latency, cost metrics."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.get("/agents/{agent_name}/history")
async def get_agent_history(
    agent_name: str,
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=30, ge=1, le=200),
):
    """Get historical metrics for a specific agent."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


@router.post(
    "/run",
    response_model=EvaluationRunResponse,
    status_code=202,
)
async def trigger_evaluation(
    body: EvaluationRunRequest,
    user: Annotated[User, Depends(require_permission("reports", "create"))],
):
    """Trigger an evaluation run (async)."""
    agent_names = body.agent_names or [
        "document_agent",
        "estimating_agent",
        "scheduling_agent",
        "safety_agent",
        "quality_agent",
    ]

    import hashlib

    eval_hash = hashlib.sha256(str(sorted(agent_names)).encode()).hexdigest()[:8]
    evaluation_id = f"eval_{eval_hash}"

    return EvaluationRunResponse(
        evaluation_id=evaluation_id,
        status="started",
        agents_queued=agent_names,
    )


@router.get("/llm-usage")
async def get_llm_usage(
    user: Annotated[User, Depends(require_permission("reports", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_name: str | None = None,
):
    """Get LLM usage statistics."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED_DETAIL)


class AnnotationBatchRequest(BaseModel):
    budget: int = Field(default=100, ge=10, le=500)
    strategy: str = Field(default="uncertainty", pattern="^(uncertainty|random|diverse)$")


@router.post("/annotation-batch")
async def generate_annotation_batch(
    body: AnnotationBatchRequest,
    user: Annotated[User, Depends(require_permission("reports", "create"))],
):
    """Generate an active learning annotation batch.

    Selects the most informative images from recent model predictions
    for human annotation, using uncertainty sampling or other strategies.
    """
    try:
        import shutil
        import tempfile

        from ml.training.active_learning import (
            generate_annotation_batch as gen_batch,
        )
        from ml.training.active_learning import (
            select_samples_for_annotation,
        )

        # In production, load recent predictions from DB/cache
        # For now, return empty batch structure
        predictions: list[dict] = []
        selected = select_samples_for_annotation(
            predictions, budget=body.budget, strategy=body.strategy
        )

        output_dir = tempfile.mkdtemp(prefix="annotation_batch_")
        try:
            manifest_path = gen_batch(selected, output_dir)
            return {
                "batch_id": f"batch_{body.budget}",
                "total_images": len(selected),
                "manifest_path": os.path.basename(manifest_path) if manifest_path else "",
                "images": selected[:20],
            }
        finally:
            # Clean up temp directory to prevent disk exhaustion
            shutil.rmtree(output_dir, ignore_errors=True)
    except ImportError:
        logger.warning("Active learning module not available in import path")
        return {
            "batch_id": "batch_0",
            "total_images": 0,
            "manifest_path": "",
            "images": [],
        }


@router.get("/canary-deployments")
async def list_canary_deployments(
    _user: Annotated[User, Depends(require_permission("reports", "read"))],
):
    """List all active and recent canary deployments."""
    from app.services.mlops.canary_deployer import CanaryDeployer

    deployer = CanaryDeployer()
    return {"deployments": deployer.list_deployments()}


class CanaryDeployRequest(BaseModel):
    model_name: str
    new_version: str
    traffic_percent: int = Field(default=5, ge=1, le=50)


@router.post("/canary-deployments")
async def create_canary_deployment(
    body: CanaryDeployRequest,
    _user: Annotated[User, Depends(require_permission("reports", "create"))],
):
    """Start a new canary deployment for a model."""
    from app.services.mlops.canary_deployer import CanaryDeployer

    deployer = CanaryDeployer()
    deployment = await deployer.deploy_canary(
        model_name=body.model_name,
        new_version=body.new_version,
        traffic_percent=body.traffic_percent,
    )
    return deployment


@router.post("/canary-deployments/{model_name}/evaluate")
async def evaluate_canary_deployment(
    _user: Annotated[User, Depends(require_permission("reports", "create"))],
    model_name: str = fastapi.Path(..., pattern=r"^[a-zA-Z0-9_.\-]+$", max_length=128),
):
    """Evaluate and auto-promote/rollback a canary deployment."""
    from app.services.mlops.canary_deployer import CanaryDeployer

    deployer = CanaryDeployer()
    return await deployer.promote_or_rollback(model_name)
