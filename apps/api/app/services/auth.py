import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as _auth_settings
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

# SECURITY [P1-5]: Redis-only refresh token JTI tracking, fail closed.
# No in-memory fallback — if Redis is unavailable, refresh is denied.
# TTL derived from config to stay in sync with token expiry.
_REFRESH_JTI_TTL = getattr(_auth_settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7) * 24 * 3600


async def _check_and_mark_refresh_jti(jti: str) -> bool:
    """Check if refresh JTI was already used. Redis-only, fail closed.

    Returns True if the JTI was already used (reject) or if Redis is
    unavailable (fail closed — treat as replay to deny refresh).
    Returns False only when the JTI is freshly marked in Redis.
    """
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is None:
            logger.critical("SECURITY: Redis unavailable for refresh JTI check — rejecting token")
            return True  # Fail closed
        was_set = await r.set(f"cai:refresh_jti:{jti}", "1", nx=True, ex=_REFRESH_JTI_TTL)
        return not was_set  # True if already existed (replay)
    except Exception:
        logger.critical("SECURITY: Redis error during refresh JTI check — rejecting token")
        return True  # Fail closed


def _validate_password_complexity(password: str) -> None:
    """Enforce password complexity requirements at the service layer.

    Raises ``ValueError`` if the password does not meet all of:
    - Minimum 12 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special character
    """
    errors: list[str] = []
    if len(password) > 128:
        errors.append("Password must not exceed 128 characters")
    if len(password) < 12:
        errors.append("Password must be at least 12 characters long")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Password must contain at least one special character")
    if errors:
        raise ValueError("; ".join(errors))


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    org_id: str,
) -> User:
    email = email.lower().strip()
    _validate_password_complexity(password)
    hashed = hash_password(password)
    user = User(
        email=email,
        hashed_password=hashed,
        full_name=full_name,
        org_id=org_id,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> TokenResponse | None:
    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        return None
    token_data = {
        "sub": str(user.id),
        "org_id": str(user.org_id),
        "token_version": user.token_version or 0,
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def refresh_tokens(
    db: AsyncSession,
    *,
    refresh_token: str,
) -> TokenResponse | None:
    payload = decode_refresh_token(refresh_token)
    if payload is None:
        return None

    # SECURITY [H-01]: Check if refresh token was blacklisted (e.g. on logout)
    old_jti = payload.get("jti")
    if old_jti:
        from app.services.security.redis_state import is_token_blacklisted

        if await is_token_blacklisted(old_jti):
            return None

    # RT6-AUTH-09: Atomic check-and-set to prevent TOCTOU race on refresh JTI
    if old_jti and await _check_and_mark_refresh_jti(old_jti):
        return None

    user_id = payload.get("sub")
    if user_id is None:
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    # AUTH-10: Reject refresh tokens issued before a password change
    token_version = payload.get("token_version", 0)
    if token_version != (user.token_version or 0):
        return None

    token_data = {
        "sub": str(user.id),
        "org_id": str(user.org_id),
        "token_version": user.token_version or 0,
    }
    new_access_token = create_access_token(data=token_data)
    new_refresh_token = create_refresh_token(data=token_data)
    return TokenResponse(access_token=new_access_token, refresh_token=new_refresh_token)
