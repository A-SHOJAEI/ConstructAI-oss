"""Password reset endpoints (forgot + reset)."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.database import get_db
from app.models.base import Base
from app.models.user import User
from app.schemas.auth import _validate_password_complexity
from app.services.email.service import send_password_reset_email
from app.services.observability.audit_logger import AuditAction, audit_log
from app.utils.security import hash_password

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SECURITY [M-04]: Rate limiting for password reset requests.
# In-memory tracker with Redis fallback. Limits to 3 requests per email
# per 15 minutes to prevent email bombing.
# ---------------------------------------------------------------------------
_reset_request_tracker: dict[str, list[float]] = defaultdict(list)
_RESET_REQUEST_LIMIT = 3
_RESET_REQUEST_WINDOW = 900  # 15 minutes
_TRACKER_SOFT_CAP = 10_000


def _trim_tracker(tracker: dict[str, list[float]], window: int, label: str) -> None:
    """Bound the in-memory rate-limit tracker without losing active lockouts.

    When the dict exceeds the soft cap, first drop keys whose timestamps are
    all outside the current window (genuinely stale). If that isn't enough,
    evict the 10% with the oldest most-recent activity — this preserves
    currently-active lockouts instead of wiping the whole dict (which would
    let an attacker bypass the rate limit by overflowing the tracker).
    """
    if len(tracker) <= _TRACKER_SOFT_CAP:
        return
    now = time.monotonic()
    cutoff = now - window
    stale = [k for k, timestamps in tracker.items() if not any(t > cutoff for t in timestamps)]
    for k in stale:
        del tracker[k]
    if len(tracker) <= _TRACKER_SOFT_CAP:
        return
    # Still too large after removing stale entries. Evict the 10% of keys
    # whose most-recent timestamp is oldest — these are the least-active
    # lockouts. Active throttles stay intact.
    overflow = len(tracker) - _TRACKER_SOFT_CAP
    evict_count = max(overflow, len(tracker) // 10)
    evict_keys = sorted(
        tracker.keys(),
        key=lambda k: max(tracker[k]) if tracker[k] else 0.0,
    )[:evict_count]
    for k in evict_keys:
        del tracker[k]
    logger.warning(
        "%s exceeded %d entries after stale-pruning; evicted %d least-active keys",
        label,
        _TRACKER_SOFT_CAP,
        evict_count,
    )


def _check_reset_rate_limit(email: str) -> None:
    """Raise 429 if the email has exceeded the password reset rate limit."""
    _trim_tracker(_reset_request_tracker, _RESET_REQUEST_WINDOW, "Password reset tracker")

    now = time.monotonic()
    cutoff = now - _RESET_REQUEST_WINDOW
    _reset_request_tracker[email] = [t for t in _reset_request_tracker[email] if t > cutoff]
    _reset_request_tracker[email].append(now)
    if len(_reset_request_tracker[email]) > _RESET_REQUEST_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many password reset requests. Try again later.",
        )


# ---------------------------------------------------------------------------
# SECURITY [S5]: Per-IP rate limiting for password reset attempts.
# Prevents brute-forcing of reset tokens. Separate from the per-email
# rate limit on forgot-password requests.
# ---------------------------------------------------------------------------
_reset_attempt_tracker: dict[str, list[float]] = defaultdict(list)
_RESET_ATTEMPT_LIMIT = 10  # 10 attempts per IP per 15 minutes
_RESET_ATTEMPT_WINDOW = 900


def _check_reset_attempt_rate_limit(ip: str) -> None:
    """Raise 429 if the IP has exceeded the password reset attempt limit."""
    _trim_tracker(_reset_attempt_tracker, _RESET_ATTEMPT_WINDOW, "Reset attempt tracker")

    now = time.monotonic()
    cutoff = now - _RESET_ATTEMPT_WINDOW
    _reset_attempt_tracker[ip] = [t for t in _reset_attempt_tracker[ip] if t > cutoff]
    _reset_attempt_tracker[ip].append(now)
    if len(_reset_attempt_tracker[ip]) > _RESET_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reset attempts. Try again later.",
        )


router = APIRouter()


class UsedResetToken(Base):
    """Persists used password-reset token hashes so they survive restarts."""

    __tablename__ = "used_reset_tokens"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))


async def _is_token_used(db: AsyncSession, token: str) -> bool:
    """Check if a reset token has already been used."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(select(UsedResetToken).where(UsedResetToken.token_hash == token_hash))
    return result.scalar_one_or_none() is not None


async def _mark_token_used(db: AsyncSession, token: str) -> None:
    """Mark a reset token as used."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(UsedResetToken(token_hash=token_hash))
    await db.flush()


_RESET_TOKEN_EXPIRE_HOURS = 1


# ── Request / Response schemas ─────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    email: str = Field(max_length=255)


class ForgotPasswordResponse(BaseModel):
    detail: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_password_complexity(v)


class ResetPasswordResponse(BaseModel):
    detail: str


# ── Helpers ────────────────────────────────────────────────────────────


def _create_reset_token(user_id: str, email: str, token_version: int = 0) -> str:
    """Create a time-limited JWT for password reset (1 hour expiry)."""
    import uuid as _uuid

    expire = datetime.now(UTC) + timedelta(hours=_RESET_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "type": "password_reset",
        "token_version": token_version,
        # SECURITY [L-03]: Include JTI so reset tokens can be blacklisted
        # after use, preventing replay attacks.
        "jti": str(_uuid.uuid4()),
        "iss": "constructai",
        "aud": "constructai-api",
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _decode_reset_token(token: str) -> dict | None:
    """Decode and validate a password-reset JWT.

    Returns the payload dict on success, or ``None`` if the token is
    invalid, expired, or is not of type ``password_reset``.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="constructai",
            audience="constructai-api",
        )
        if payload.get("type") != "password_reset":
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ── Endpoints ──────────────────────────────────────────────────────────


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> ForgotPasswordResponse:
    """Generate a password-reset token and log it.

    Always returns 200 regardless of whether the email exists to avoid
    user enumeration.  When a matching user is found the reset token is
    logged (actual email delivery is deferred to a future integration).
    """
    # RT6-AUTH-05: Normalize email for consistent lookup
    email = request.email.lower().strip()

    # SECURITY [M-04]: Rate limit BEFORE database lookup to prevent enumeration bypass
    _check_reset_rate_limit(email)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        # RT6-AUTH-12: Bind reset token to current token_version
        token = _create_reset_token(
            user_id=str(user.id),
            email=user.email,
            token_version=user.token_version or 0,
        )
        send_password_reset_email(user.email, token)

    # Always return the same response to prevent email enumeration.
    return ForgotPasswordResponse(
        detail="If that email is registered you will receive a reset link."
    )


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
)
async def reset_password(
    request: ResetPasswordRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordResponse:
    """Validate a reset token and update the user's password."""
    # SECURITY [S5]: Rate limit reset attempts by IP to prevent token brute-forcing
    client_ip = raw_request.client.host if raw_request.client else "unknown"
    _check_reset_attempt_rate_limit(client_ip)

    # Check if token was already used (DB-backed, survives restarts)
    if await _is_token_used(db, request.token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used.",
        )

    payload = _decode_reset_token(request.token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token payload.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    # RT6-AUTH-12: Verify token_version matches to ensure token was not invalidated
    token_tv = payload.get("token_version", 0)
    if token_tv != (user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has been invalidated by a subsequent password change.",
        )

    # Update the password hash and invalidate all existing sessions.
    # L-3: atomic SQL increment instead of read-modify-write on the ORM
    # object — two concurrent resets can no longer collide on the same
    # starting value and each set token_version to +1.
    user.hashed_password = hash_password(request.new_password)
    from sqlalchemy import update as _update

    from app.models.user import User as _User

    await db.execute(
        _update(_User)
        .where(_User.id == user.id)
        .values(
            hashed_password=user.hashed_password,
            token_version=_User.token_version + 1,
        )
    )
    await db.refresh(user)

    # Mark the token as used to prevent reuse (DB-backed)
    await _mark_token_used(db, request.token)

    audit_log(
        AuditAction.PASSWORD_RESET_COMPLETE,
        user_id=user.id,
        org_id=user.org_id,
    )

    return ResetPasswordResponse(detail="Password has been reset successfully.")
