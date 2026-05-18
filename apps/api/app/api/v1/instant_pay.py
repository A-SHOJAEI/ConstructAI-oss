"""Instant Pay API endpoints: automated billing, payments, and lien waivers."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.instant_pay import (
    AutoGeneratePayAppRequest,
    GenerateLienWaiverRequest,
    LienWaiverPackageResponse,
    PaymentConfigCreate,
    PaymentConfigResponse,
    PaymentStatusItem,
    PaymentStatusResponse,
    PaymentTransactionResponse,
    SubmitPaymentRequest,
)
from app.schemas.pay_application import PayApplicationResponse
from app.services.controls.instant_pay_service import (
    auto_generate_pay_app_from_progress,
    configure_payment_integration,
    generate_lien_waiver_package,
    get_payment_status,
    handle_payment_webhook,
    submit_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter()
webhook_router = APIRouter()

# ---------------------------------------------------------------------------
# Auto-generate pay app from progress
# ---------------------------------------------------------------------------


@router.post(
    "/auto-generate",
    response_model=PayApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def auto_generate_pay_application(
    request: AutoGeneratePayAppRequest,
    current_user: User = Depends(require_permission("pay_applications", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate a pay application from a progress snapshot.

    Maps AI-detected activity progress to Schedule of Values line items
    and creates a fully computed pay application (G702/G703).
    """
    await verify_project_access(request.project_id, current_user, db)

    try:
        pay_app = await auto_generate_pay_app_from_progress(
            db=db,
            project_id=request.project_id,
            snapshot_id=request.snapshot_id,
            period_to=request.period_to,
            submitted_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return pay_app


# ---------------------------------------------------------------------------
# Submit payment
# ---------------------------------------------------------------------------


@router.post(
    "/submit-payment",
    response_model=PaymentTransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_payment_for_pay_app(
    request: SubmitPaymentRequest,
    current_user: User = Depends(require_permission("pay_applications", "approve")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a payment for a certified pay application.

    Creates a PaymentTransaction with computed retainage and net amounts.
    The pay application must be in 'certified' status.
    """
    from app.models.pay_application import PayApplication

    # Look up pay app for project access check
    pay_app = await db.get(PayApplication, request.pay_application_id)
    if pay_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay application not found",
        )
    await verify_project_access(pay_app.project_id, current_user, db)

    try:
        transaction = await submit_payment(
            db=db,
            pay_application_id=request.pay_application_id,
            payment_method=request.payment_method,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return transaction


# ---------------------------------------------------------------------------
# Webhook (CSRF-exempt — authenticated by HMAC signature)
# ---------------------------------------------------------------------------


@webhook_router.post(
    "/{project_id}",
    response_model=PaymentTransactionResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_payment_webhook(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
):
    """Receive payment processor webhook.

    Validates HMAC-SHA256 signature from the X-Webhook-Signature header.
    Updates transaction status and auto-generates lien waivers on payment.

    This endpoint is CSRF-exempt — authentication is via HMAC signature
    verification using the project's configured webhook secret.

    BE-13: Raw body is read and HMAC-verified BEFORE JSON parsing to prevent
    processing unauthenticated payloads.
    """
    # Load webhook secret for this project
    from sqlalchemy import select as sa_select

    from app.models.instant_pay import PaymentIntegrationConfig

    result = await db.execute(
        sa_select(PaymentIntegrationConfig).where(
            PaymentIntegrationConfig.project_id == project_id,
            PaymentIntegrationConfig.is_active == True,  # noqa: E712
        )
    )
    config = result.scalars().first()
    if config is None or not config.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment integration not configured for this project",
        )

    # BE-13: Read raw body and verify HMAC signature BEFORE parsing JSON
    raw_body = await request.body()

    expected_sig = hmac.new(
        config.webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, x_webhook_signature):
        logger.warning("SECURITY: Invalid webhook signature for project %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Parse body only after signature is verified
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    try:
        transaction = await handle_payment_webhook(
            db=db,
            project_id=project_id,
            payload=payload,
            signature=x_webhook_signature,
            webhook_secret=config.webhook_secret,
        )
    except ValueError as e:
        error_msg = str(e)
        if "signature" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_msg,
        )

    return transaction


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/instant-pay/status",
    response_model=PaymentStatusResponse,
)
async def get_project_payment_status(
    project_id: uuid.UUID,
    pay_application_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get payment transaction status with waterfall timing.

    Optionally filter by pay_application_id.
    """
    await verify_project_access(project_id, current_user, db)

    transactions = await get_payment_status(
        db=db,
        project_id=project_id,
        pay_application_id=pay_application_id,
    )

    return PaymentStatusResponse(data=cast(list[PaymentStatusItem], transactions))


# ---------------------------------------------------------------------------
# Lien waivers
# ---------------------------------------------------------------------------


@router.post(
    "/lien-waivers",
    response_model=LienWaiverPackageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_lien_waiver(
    request: GenerateLienWaiverRequest,
    current_user: User = Depends(require_permission("pay_applications", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a lien waiver package for a pay application.

    Creates waiver items for each billing line item and bundles them
    into a LienWaiverPackage. Also creates individual LienWaiver records.
    """
    from app.models.pay_application import PayApplication

    pay_app = await db.get(PayApplication, request.pay_application_id)
    if pay_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay application not found",
        )
    await verify_project_access(pay_app.project_id, current_user, db)

    try:
        package = await generate_lien_waiver_package(
            db=db,
            pay_application_id=request.pay_application_id,
            package_type=request.package_type,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return package


# ---------------------------------------------------------------------------
# Payment integration config
# ---------------------------------------------------------------------------


@router.put(
    "/{project_id}/instant-pay/config",
    response_model=PaymentConfigResponse,
)
async def update_payment_config(
    project_id: uuid.UUID,
    request: PaymentConfigCreate,
    current_user: User = Depends(require_permission("pay_applications", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Create or update payment integration configuration for a project.

    Configures the payment processor, retainage percentage, payment terms,
    and automation settings.
    """
    await verify_project_access(project_id, current_user, db)

    try:
        config = await configure_payment_integration(
            db=db,
            project_id=project_id,
            config_data=request.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return config
