"""Schemas for Instant Pay feature."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

TRANSACTION_TYPES = {"owner_to_gc", "gc_to_sub", "retainage_release"}
TRANSACTION_STATUSES = {
    "pending",
    "submitted",
    "approved",
    "processing",
    "paid",
    "failed",
    "cancelled",
}
LIEN_WAIVER_PACKAGE_TYPES = {"conditional", "unconditional"}


# ---------------------------------------------------------------------------
# Auto-generate pay app
# ---------------------------------------------------------------------------


class AutoGeneratePayAppRequest(BaseModel):
    project_id: uuid.UUID
    snapshot_id: uuid.UUID
    period_to: date


# ---------------------------------------------------------------------------
# Payment submission
# ---------------------------------------------------------------------------


class SubmitPaymentRequest(BaseModel):
    pay_application_id: uuid.UUID
    payment_method: str | None = Field(
        default=None,
        description="Payment method: ach, wire, check",
    )


class PaymentTransactionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    pay_application_id: uuid.UUID
    transaction_type: str
    amount: Decimal
    currency: str
    status: str
    processor_name: str | None = None
    processor_transaction_id: str | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    paid_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    retainage_pct: Decimal | None = None
    retainage_amount: Decimal | None = None
    net_amount: Decimal
    payment_method: str | None = None
    payer_info: dict
    payee_info: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class WebhookPayload(BaseModel):
    """Webhook payload from payment processor."""

    transaction_id: str
    event_type: str
    status: str | None = None
    pay_application_id: str | None = None
    failure_reason: str | None = None
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lien waiver package
# ---------------------------------------------------------------------------


class GenerateLienWaiverRequest(BaseModel):
    pay_application_id: uuid.UUID
    package_type: str = "conditional"

    @field_validator("package_type")
    @classmethod
    def validate_package_type(cls, v: str) -> str:
        if v not in LIEN_WAIVER_PACKAGE_TYPES:
            raise ValueError(f"package_type must be one of {sorted(LIEN_WAIVER_PACKAGE_TYPES)}")
        return v


class LienWaiverPackageResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    pay_application_id: uuid.UUID
    payment_transaction_id: uuid.UUID | None = None
    package_type: str
    waiver_items: list | dict
    total_amount: Decimal
    status: str
    document_url: str | None = None
    generated_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


class PaymentStatusItem(BaseModel):
    id: str
    pay_application_id: str
    transaction_type: str
    amount: float
    net_amount: float
    retainage_amount: float | None = None
    currency: str = "USD"
    status: str
    payment_method: str | None = None
    processor_name: str | None = None
    processor_transaction_id: str | None = None
    submitted_at: str | None = None
    approved_at: str | None = None
    paid_at: str | None = None
    failed_at: str | None = None
    failure_reason: str | None = None
    waterfall: dict = Field(default_factory=dict)
    created_at: str | None = None


class PaymentStatusResponse(BaseModel):
    data: list[PaymentStatusItem]


# ---------------------------------------------------------------------------
# Payment integration config
# ---------------------------------------------------------------------------


class PaymentConfigCreate(BaseModel):
    processor_name: str
    webhook_secret: str | None = None
    config: dict = Field(default_factory=dict)
    retainage_pct: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    payment_terms_days: int = Field(default=30, ge=1, le=180)
    auto_generate_pay_apps: bool = False
    auto_generate_lien_waivers: bool = True
    is_active: bool = True


class PaymentConfigResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    processor_name: str
    config: dict
    retainage_pct: Decimal
    payment_terms_days: int
    auto_generate_pay_apps: bool
    auto_generate_lien_waivers: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
