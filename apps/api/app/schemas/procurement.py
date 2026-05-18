from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PriceForecastCreate(BaseModel):
    material_category: str
    series_id: str
    observation_date: date
    price_index: Decimal
    forecast_value: Decimal | None = None
    forecast_lower: Decimal | None = None
    forecast_upper: Decimal | None = None
    model_used: str | None = None


class PriceForecastResponse(BaseModel):
    id: uuid.UUID
    material_category: str
    series_id: str
    observation_date: date
    price_index: Decimal
    forecast_value: Decimal | None = None
    forecast_lower: Decimal | None = None
    forecast_upper: Decimal | None = None
    model_used: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ForecastRequest(BaseModel):
    material_category: str
    series_id: str
    horizon_months: int = 6


class ForecastResponse(BaseModel):
    forecasts: list[PriceForecastResponse]
    model_used: str
    rmse: float
    trend: Literal["rising", "falling", "stable"]


class RiskItem(BaseModel):
    clause: str
    risk_type: str
    severity: Literal["high", "medium", "low"]
    explanation: str


class ContractRiskRequest(BaseModel):
    contract_text: str
    project_type: str


class ContractRiskResponse(BaseModel):
    overall_risk_score: float
    risk_items: list[RiskItem]
    recommendations: list[str]
    model_used: str


class VendorScoreRequest(BaseModel):
    vendor_id: str
    criteria: dict


class VendorScoreResponse(BaseModel):
    vendor_id: str
    overall_score: float
    criteria_scores: dict
    recommendation: str


class BidItem(BaseModel):
    vendor_name: str
    bid_amount: float
    details: dict = Field(default_factory=dict)


class EvaluateBidsRequest(BaseModel):
    project_id: uuid.UUID
    bids: list[BidItem] = Field(min_length=1)
    criteria: dict = Field(default_factory=dict)


class EvaluateBidsResponse(BaseModel):
    ranked_bids: list[dict]
    recommendation: str
    evaluation_criteria: dict
