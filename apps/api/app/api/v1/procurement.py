"""Procurement API endpoints for price forecasting, contract risk, and vendor scoring."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.procurement import (
    ContractRiskRequest,
    ContractRiskResponse,
    EvaluateBidsRequest,
    EvaluateBidsResponse,
    ForecastRequest,
    ForecastResponse,
    VendorScoreRequest,
    VendorScoreResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/price-forecast", response_model=ForecastResponse)
async def forecast_prices_endpoint(
    request: ForecastRequest,
    current_user: User = Depends(require_permission("procurement", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate material price forecast.

    Fetches historical price data for the specified material category and
    series, then runs a forecasting model to predict future prices.
    """
    try:
        from app.services.procurement.price_forecaster import forecast_prices

        forecast_result = await forecast_prices(
            material_category=request.material_category,
            series_id=request.series_id,
            horizon_months=request.horizon_months,
        )
    except ImportError:
        logger.warning("Price forecaster module not available; returning placeholder")
        forecast_result = {
            "forecasts": [],
            "model_used": "unavailable",
            "rmse": 0.0,
            "trend": "stable",
        }

    return ForecastResponse(
        forecasts=forecast_result.get("forecasts", []),
        model_used=forecast_result.get("model_used", "unknown"),
        rmse=forecast_result.get("rmse", 0.0),
        trend=forecast_result.get("trend", "stable"),
    )


@router.post("/contract-risk", response_model=ContractRiskResponse)
async def assess_contract_risk(
    request: ContractRiskRequest,
    current_user: User = Depends(require_permission("procurement", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Analyze contract for risk factors.

    Scans contract text using NLP and domain rules to identify risky clauses,
    unfavorable terms, and potential liabilities.
    """
    if not request.contract_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contract text cannot be empty.",
        )

    try:
        from app.services.procurement.contract_risk import score_contract_risk

        risk_result = await score_contract_risk(
            contract_text=request.contract_text,
            project_type=request.project_type,
        )
    except ImportError:
        logger.warning("Contract risk module not available; returning placeholder")
        risk_result = {
            "overall_risk_score": 0.0,
            "risk_items": [],
            "recommendations": [
                "Contract risk analysis module not yet available.",
            ],
            "model_used": "unavailable",
        }

    return ContractRiskResponse(
        overall_risk_score=risk_result.get("overall_risk_score", 0.0),
        risk_items=risk_result.get("risk_items", []),
        recommendations=risk_result.get("recommendations", []),
        model_used=risk_result.get("model_used", "unknown"),
    )


@router.post("/vendor-score", response_model=VendorScoreResponse)
async def score_vendor_endpoint(
    request: VendorScoreRequest,
    current_user: User = Depends(require_permission("procurement", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Score a vendor based on evaluation criteria.

    Evaluates a vendor against weighted criteria such as price competitiveness,
    quality track record, delivery reliability, and safety record.
    """
    try:
        from app.services.procurement.vendor_manager import score_vendor

        # Service expects vendor_data dict; the API request carries the same
        # payload in `request.criteria` / `vendor_id` — pass as vendor_data.
        vendor_result = await score_vendor(
            vendor_data={"vendor_id": request.vendor_id, **(request.criteria or {})},
        )
    except ImportError:
        logger.warning("Vendor manager module not available; returning placeholder")
        vendor_result = {
            "vendor_id": request.vendor_id,
            "overall_score": 0.0,
            "criteria_scores": {},
            "recommendation": "Vendor scoring module not yet available.",
        }

    return VendorScoreResponse(
        vendor_id=vendor_result.get("vendor_id", request.vendor_id),
        overall_score=vendor_result.get("overall_score", 0.0),
        criteria_scores=vendor_result.get("criteria_scores", {}),
        recommendation=vendor_result.get("recommendation", ""),
    )


@router.post("/evaluate-bids", response_model=EvaluateBidsResponse)
async def evaluate_bids_endpoint(
    request: EvaluateBidsRequest,
    current_user: User = Depends(require_permission("procurement", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate and rank competitive bids.

    Accepts a set of bid submissions with evaluation criteria and returns
    a ranked list of bids with scores and recommendations.
    """
    await verify_project_access(request.project_id, current_user, db)

    bids = [bid.model_dump() for bid in request.bids]

    try:
        from app.services.procurement.vendor_manager import evaluate_bid

        eval_result = await evaluate_bid(
            bids=bids,
            evaluation_criteria=request.criteria,
        )
    except ImportError:
        logger.warning("Vendor manager module not available; returning placeholder")
        eval_result = {
            "ranked_bids": [],
            "recommendation": "Bid evaluation module not yet available.",
            "evaluation_criteria": request.criteria,
        }

    return EvaluateBidsResponse(
        ranked_bids=eval_result.get("ranked_bids", []),
        recommendation=eval_result.get("recommendation", ""),
        evaluation_criteria=eval_result.get("evaluation_criteria", request.criteria),
    )
