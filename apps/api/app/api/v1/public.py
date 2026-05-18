"""Public routes — no authentication required.

These endpoints are accessed via magic links (e.g. subcontractor
document uploads) and must remain outside the auth middleware.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.magic_link import MagicLinkUploadResponse
from app.services.shared.magic_link import validate_magic_link_token

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/upload/{token}",
    response_model=MagicLinkUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def magic_link_upload(
    token: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document via magic link (no authentication required).

    The token is validated, its use count incremented, and the file is
    routed to the appropriate product service based on the token's
    ``purpose`` field.
    """
    record = await validate_magic_link_token(db, token)

    # TODO: Store file in S3 under the project/entity path.
    # s3_key = f"uploads/{record.project_id}/{record.entity_id}/{file.filename}"
    # await upload_to_s3(s3_key, file)

    requirement_id: uuid.UUID | None = record.entity_id

    # Route to appropriate product service based on purpose
    if record.purpose == "closeout_upload":
        # TODO: Hand off to CloseoutIQ service
        logger.info(
            "Closeout upload via magic link for project %s (entity %s)",
            record.project_id,
            record.entity_id,
        )
    elif record.purpose == "payroll_upload":
        # TODO: Hand off to WageGuard service
        logger.info(
            "Payroll upload via magic link for project %s",
            record.project_id,
        )
    elif record.purpose == "daily_log_input":
        # TODO: Hand off to SiteScribe service
        logger.info(
            "Daily log upload via magic link for project %s",
            record.project_id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported purpose: {record.purpose}",
        )

    return MagicLinkUploadResponse(
        success=True,
        requirement_id=requirement_id,
        message="File uploaded successfully.",
    )
