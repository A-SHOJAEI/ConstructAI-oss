"""Procore webhook registration service.

Automatically registers webhooks when a Procore connection is established,
so that ConstructAI receives real-time notifications for supported resources.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.integrations.procore_api import ProcoreAPI

logger = logging.getLogger(__name__)

# Resource types to register webhooks for
WEBHOOK_RESOURCES = [
    "Documents",
    "RFIs",
    "Budget Line Items",
    "Change Orders",
    "Daily Logs",
    "Submittals",
    "Observations",
]


async def register_webhooks(
    org_id: uuid.UUID,
    company_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    """Register webhooks for all supported Procore resource types.

    Called automatically after a Procore connection is established.
    Skips resources that already have a webhook registered to the
    same destination URL.

    Returns a summary dict with registration results.
    """
    if not settings.PROCORE_WEBHOOK_SECRET:
        logger.warning("PROCORE_WEBHOOK_SECRET not configured; skipping webhook registration")
        return {"registered": [], "skipped": [], "errors": []}

    # Build the destination URL for Procore to POST events to
    base_url = settings.PROCORE_REDIRECT_URI.rsplit("/", 2)[0]  # strip /callback path
    destination_url = f"{base_url.rsplit('/integrations', 1)[0]}/webhooks/procore"

    api = ProcoreAPI(org_id=org_id, db=db)

    # Check existing webhooks to avoid duplicates
    try:
        existing_hooks = await api.list_webhooks(company_id)
    except Exception as exc:
        logger.error("Failed to list existing webhooks: %s", exc)
        existing_hooks = []

    existing_namespaces = {
        h.get("namespace") for h in existing_hooks if h.get("destination_url") == destination_url
    }

    registered = []
    skipped = []
    errors = []

    for resource in WEBHOOK_RESOURCES:
        if resource in existing_namespaces:
            logger.info("Webhook already registered for %s; skipping", resource)
            skipped.append(resource)
            continue

        try:
            result = await api.register_webhook(
                company_id=company_id,
                destination_url=destination_url,
                resource_name=resource,
            )
            registered.append(resource)
            logger.info(
                "Registered Procore webhook for %s (hook_id=%s)",
                resource,
                result.get("id"),
            )
        except Exception as exc:
            logger.error("Failed to register webhook for %s: %s", resource, exc)
            errors.append({"resource": resource, "error": str(exc)})

    logger.info(
        "Webhook registration complete: %d registered, %d skipped, %d errors",
        len(registered),
        len(skipped),
        len(errors),
    )

    return {
        "registered": registered,
        "skipped": skipped,
        "errors": errors,
    }


async def unregister_webhooks(
    org_id: uuid.UUID,
    company_id: int,
    db: AsyncSession,
) -> int:
    """Remove all registered webhooks for this organization.

    Called when disconnecting Procore.
    Returns the number of hooks deleted.
    """
    api = ProcoreAPI(org_id=org_id, db=db)

    try:
        hooks = await api.list_webhooks(company_id)
    except Exception as exc:
        logger.error("Failed to list webhooks for cleanup: %s", exc)
        return 0

    # Build expected destination URL
    base_url = settings.PROCORE_REDIRECT_URI.rsplit("/", 2)[0]
    destination_url = f"{base_url.rsplit('/integrations', 1)[0]}/webhooks/procore"

    deleted = 0
    for hook in hooks:
        if hook.get("destination_url") == destination_url:
            try:
                await api.delete_webhook(company_id, hook["id"])
                deleted += 1
            except Exception as exc:
                logger.error("Failed to delete webhook %s: %s", hook["id"], exc)

    logger.info("Deleted %d webhooks for org %s", deleted, org_id)
    return deleted
