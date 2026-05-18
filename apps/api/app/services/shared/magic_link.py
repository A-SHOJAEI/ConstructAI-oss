"""Magic link token generation, validation, and revocation."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.magic_link import MagicLinkToken

logger = logging.getLogger(__name__)


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of *raw_token*."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def generate_magic_link_token(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    purpose: str,
    *,
    entity_id: uuid.UUID | None = None,
    recipient_email: str | None = None,
    recipient_name: str | None = None,
    expires_in_days: int = 7,
    max_uses: int = 1,
    metadata: dict | None = None,
) -> tuple[str, MagicLinkToken]:
    """Generate a magic link token and persist its hash.

    Returns:
        A ``(raw_token, db_record)`` tuple.  The raw token is the value
        that should be sent to the recipient (e.g. embedded in a URL).
        Only the SHA-256 hash is stored in the database.
    """
    raw_token = secrets.token_hex(32)  # 64 hex chars
    token_hash = _hash_token(raw_token)

    record = MagicLinkToken(
        token_hash=token_hash,
        project_id=project_id,
        organization_id=org_id,
        purpose=purpose,
        entity_id=entity_id,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
        max_uses=max_uses,
        metadata_=metadata or {},
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)

    logger.info(
        "Generated magic link token for project %s (purpose=%s, recipient=%s)",
        project_id,
        purpose,
        recipient_email,
    )
    return raw_token, record


async def validate_magic_link_token(
    db: AsyncSession,
    raw_token: str,
) -> MagicLinkToken:
    """Validate a raw token and increment its use counter.

    Raises:
        HTTPException(404): Token not found, expired, or usage exhausted.
    """
    token_hash = _hash_token(raw_token)
    result = await db.execute(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))
    record = result.scalars().first()

    if record is None:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    now = datetime.now(UTC)
    if record.expires_at.tzinfo is None:
        # Defensive: compare aware vs naive safely
        expires_at = record.expires_at.replace(tzinfo=UTC)
    else:
        expires_at = record.expires_at

    if now > expires_at:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if record.use_count >= record.max_uses:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    record.use_count += 1
    record.used_at = now
    await db.flush()
    await db.refresh(record)
    return record


async def revoke_magic_link_token(
    db: AsyncSession,
    token_id: uuid.UUID,
) -> None:
    """Revoke a token by setting its expiry to now."""
    record = await db.get(MagicLinkToken, token_id)
    if record is not None:
        record.expires_at = datetime.now(UTC)
        await db.flush()
