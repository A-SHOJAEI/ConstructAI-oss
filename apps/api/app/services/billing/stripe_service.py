"""Billing / Stripe service stubs.

Real Stripe integration will be added in a later phase.  For now these
helpers query the local ``BillingSubscription`` table and fall back to
permissive defaults so that development and free-tier organisations are
never blocked.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingSubscription, ProductUsageEvent

logger = logging.getLogger(__name__)

# Products available to every org when no subscription record exists
# (free tier / local development).
_DEFAULT_PRODUCTS: list[str] = [
    "sitescribe",
    "rfi_copilot",
    "closeout_iq",
    "heatshield",
    "wageguard",
    "carbonlens",
]


async def get_subscription(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> BillingSubscription | None:
    """Return the subscription for *org_id*, or ``None``."""
    result = await db.execute(
        select(BillingSubscription).where(BillingSubscription.organization_id == org_id)
    )
    return result.scalars().first()


async def get_enabled_products(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[str]:
    """Return the list of enabled products for *org_id*.

    If no subscription exists (free tier / dev mode), all products are
    returned so that nothing is blocked during development.
    """
    sub = await get_subscription(db, org_id)
    if sub is None:
        return list(_DEFAULT_PRODUCTS)
    return list(sub.products_enabled)


async def record_usage(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    product: str,
    event_type: str,
    quantity: int = 1,
    metadata: dict | None = None,
) -> None:
    """Persist a metered usage event."""
    event = ProductUsageEvent(
        organization_id=org_id,
        project_id=project_id,
        product=product,
        event_type=event_type,
        quantity=quantity,
        metadata_=metadata or {},
    )
    db.add(event)
    await db.flush()


async def is_product_enabled(
    db: AsyncSession,
    org_id: uuid.UUID,
    product: str,
) -> bool:
    """Check whether *product* is enabled for *org_id*."""
    enabled = await get_enabled_products(db, org_id)
    return product in enabled
