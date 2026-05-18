"""Autodesk Construction Cloud (ACC) integration endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


class AutodeskConfig(BaseModel):
    client_id: str = Field(max_length=255)
    client_secret: str = Field(max_length=255)
    account_id: str = Field(max_length=255)
    callback_url: str = ""


class AutodeskProject(BaseModel):
    id: str
    name: str
    status: str
    start_date: str | None = None
    end_date: str | None = None


class AutodeskSyncResult(BaseModel):
    synced_documents: int = 0
    synced_issues: int = 0
    synced_rfis: int = 0
    errors: list[str] = Field(default_factory=list)


@router.get("/projects")
async def list_autodesk_projects(
    user: Annotated[User, Depends(require_permission("integrations", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    account_id: str = Query(..., description="Autodesk account/hub ID"),
):
    """List projects from Autodesk Construction Cloud."""
    if settings.ENVIRONMENT in ("production", "staging"):
        raise HTTPException(status_code=501, detail="Autodesk integration not yet implemented")

    # In production, call Autodesk Data Management API
    # GET https://developer.api.autodesk.com/project/v1/hubs/{hub_id}/projects
    logger.info("Listing Autodesk projects for account %s", account_id)
    return {
        "projects": [],
        "message": "Configure Autodesk credentials in Settings > Integrations",
    }


@router.post("/sync/{project_id}")
async def sync_autodesk_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("integrations", "create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    autodesk_project_id: str = Query(..., description="Autodesk project ID", max_length=255),
):
    """Sync documents and issues from an Autodesk project."""
    if settings.ENVIRONMENT in ("production", "staging"):
        raise HTTPException(status_code=501, detail="Autodesk integration not yet implemented")

    await verify_project_access(project_id, user, db)

    logger.info(
        "Syncing Autodesk project to ConstructAI project %s",
        project_id,
    )

    # In production:
    # 1. Fetch documents from ACC Document Management API
    # 2. Download and ingest new/updated documents
    # 3. Fetch issues from ACC Issues API
    # 4. Map to ConstructAI punch list items
    # 5. Fetch RFIs if available

    return AutodeskSyncResult(
        synced_documents=0,
        synced_issues=0,
        synced_rfis=0,
        errors=["Autodesk integration not yet configured. Set API credentials in Settings."],
    )


def _verify_autodesk_signature(body: bytes, signature: str) -> bool:
    """Verify Autodesk's HMAC-SHA256 webhook signature.

    Autodesk signs each webhook payload with the shared secret using
    HMAC-SHA256 and sends the hex digest in X-Autodesk-Signature.
    """
    secret = settings.AUTODESK_WEBHOOK_SECRET
    if not secret:
        logger.warning("AUTODESK_WEBHOOK_SECRET not configured; rejecting webhook")
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


@router.post("/webhooks")
async def autodesk_webhook(request: Request):
    """Handle Autodesk webhook events (document updates, issue changes).

    Verifies HMAC-SHA256 signature from X-Autodesk-Signature header
    against AUTODESK_WEBHOOK_SECRET before processing.
    """
    # Read raw body for signature verification
    body = await request.body()

    # Verify HMAC signature
    signature = request.headers.get("X-Autodesk-Signature", "")
    if not _verify_autodesk_signature(body, signature):
        logger.warning("Autodesk webhook signature verification failed")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # Parse the JSON payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.error("Invalid JSON in Autodesk webhook payload")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    event_type = payload.get("hook", {}).get("event", "unknown")
    logger.info("Received Autodesk webhook: %s", event_type)

    # In production:
    # - dm.version.added: New document version uploaded
    # - dm.version.modified: Document metadata changed
    # - issues.issue.created: New issue created
    # - issues.issue.updated: Issue status changed

    return {"status": "received", "event": event_type}
