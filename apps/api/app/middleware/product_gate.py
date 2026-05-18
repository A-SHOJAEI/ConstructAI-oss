"""Product-gating middleware for subscription-aware route protection."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.billing.stripe_service import is_product_enabled

logger = logging.getLogger(__name__)

# Map of route-prefix keywords to product names.
PRODUCT_ROUTE_MAP: dict[str, str] = {
    "closeout": "closeout_iq",
    "heat": "heatshield",
    "wages": "wageguard",
    "carbon": "carbonlens",
}


def require_product(product_name: str) -> Callable:
    """Return a FastAPI dependency that gates access to *product_name*.

    Usage::

        @router.get("/closeout/requirements")
        async def list_requirements(
            _gate=Depends(require_product("closeout_iq")),
            ...
        ):
            ...

    In development/testing mode (``settings.ENVIRONMENT == "development"``
    or ``settings.TESTING is True``) the gate is always open.
    """

    async def _check_product(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> None:
        # Always allow in dev / test
        if settings.ENVIRONMENT == "development" or settings.TESTING:
            return

        org_id = getattr(request.state, "tenant_id", None)
        if org_id is None:
            raise HTTPException(
                status_code=403,
                detail="Organisation context required to verify product access.",
            )

        enabled = await is_product_enabled(db, org_id, product_name)
        if not enabled:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Product '{product_name}' is not included in your "
                    "subscription. Please upgrade your plan."
                ),
            )

    return _check_product
