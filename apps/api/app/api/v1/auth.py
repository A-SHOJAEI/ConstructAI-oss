import base64
import hashlib
import logging
import time
import uuid
from collections import defaultdict  # used for resend rate limiter
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import blacklist_token, get_current_user, security
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MfaLoginRequest,
    MfaSetupResponse,
    MfaSetupVerifyResponse,
    MfaVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenResponse,
)
from app.schemas.user import UserResponse
from app.services.auth import authenticate_user, refresh_tokens, register_user
from app.services.cache import CacheService
from app.services.observability.audit_logger import AuditAction, audit_log
from app.services.security.mfa import (
    generate_backup_codes,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_totp_uri,
    verify_backup_code,
    verify_totp,
)
from app.services.security.redis_state import (
    clear_failed_attempts,
    is_locked_out,
    record_failed_attempt,
)
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Account lockout: Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

# Verification email resend rate limiting — Redis-backed with in-memory fallback
_verification_resend_tracker: dict[str, list[float]] = defaultdict(list)
_RESEND_LIMIT = 3
_RESEND_WINDOW = 3600  # 1 hour
_RESEND_REDIS_KEY_PREFIX = "cai:verify_resend:"


async def _check_verification_resend_limit(email: str) -> bool:
    """Check if email verification resend is within rate limit. Redis-backed.

    Returns True if the request is within the limit, False if rate-limited.
    """
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is not None:
            key = f"{_RESEND_REDIS_KEY_PREFIX}{email}"
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, _RESEND_WINDOW)  # 1 hour window
            return count <= _RESEND_LIMIT
    except Exception:
        logger.warning(
            "SECURITY [SEC-05]: Redis unavailable for verification resend limit, "
            "using in-memory fallback"
        )
    # In-memory fallback for dev
    now = time.monotonic()
    cutoff = now - _RESEND_WINDOW
    _verification_resend_tracker[email] = [
        t for t in _verification_resend_tracker[email] if t > cutoff
    ]
    _verification_resend_tracker[email].append(now)
    return len(_verification_resend_tracker[email]) <= _RESEND_LIMIT


# SECURITY [M-04]: Rate limiting for email verification attempts (in-memory).
# Limits to 5 verification attempts per IP per 15 minutes to prevent abuse.
_verify_email_tracker: dict[str, list[float]] = defaultdict(list)
_VERIFY_EMAIL_LIMIT = 5
_VERIFY_EMAIL_WINDOW = 900  # 15 minutes

# Roles that require MFA to be set up
_MFA_REQUIRED_ROLES = {"org_admin", "project_admin"}

# SECURITY [H-03]: Per-MFA-token attempt counter — Redis-backed with in-memory fallback.
# The in-memory dict is only used when Redis is unavailable, which logs a warning.
# In production, Redis is required (see H-02) so the in-memory fallback should
# never be active; it exists solely for graceful degradation in development.
_mfa_attempt_tracker: dict[str, int] = {}
_mfa_attempt_timestamps: dict[str, float] = {}
_MFA_MAX_ATTEMPTS = 5
_MFA_ATTEMPT_KEY_PREFIX = "cai:mfa_attempts:"
_MFA_ATTEMPT_TTL = 300  # 5 minutes (matches MFA challenge token lifetime)


def _mfa_fernet() -> Fernet:
    """Derive a Fernet key from ENCRYPTION_KEY for MFA secret encryption."""
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.ENCRYPTION_KEY.encode()).digest())
    return Fernet(key)


async def _get_mfa_attempts(jti: str) -> int:
    """Get the current MFA attempt count for a given JTI (Redis-backed)."""
    # Prune stale in-memory entries and guard against unbounded growth
    # SECURITY [S2]: Fail closed instead of clearing — prevents bypass
    if len(_mfa_attempt_tracker) > 10_000:
        logger.warning("SECURITY: MFA attempt tracker full — failing closed")
        return _MFA_MAX_ATTEMPTS
    else:
        _prune_now = time.time()
        _stale_keys = [
            k for k, ts in _mfa_attempt_timestamps.items() if _prune_now - ts > _MFA_ATTEMPT_TTL
        ]
        for _k in _stale_keys:
            _mfa_attempt_tracker.pop(_k, None)
            _mfa_attempt_timestamps.pop(_k, None)

    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is not None:
            val = await r.get(f"{_MFA_ATTEMPT_KEY_PREFIX}{jti}")
            return int(val) if val else 0
    except Exception:
        logger.warning(
            "SECURITY [H-03]: Redis unavailable for MFA attempt read, using in-memory fallback"
        )
    return _mfa_attempt_tracker.get(jti, 0)


async def _increment_mfa_attempts(jti: str) -> int:
    """Increment and return the MFA attempt count for a given JTI (Redis-backed).

    SECURITY [H-1]: INCR and EXPIRE are pipelined atomically so an attacker
    can't exploit the gap between the two calls to prevent the TTL from
    being set (and thereby keep the counter indefinitely past the lockout
    window).
    """
    # SECURITY [S2]: Fail closed when tracker is full — do not record
    if len(_mfa_attempt_tracker) > 10_000:
        logger.warning("SECURITY: MFA attempt tracker full — refusing to record")
        return _MFA_MAX_ATTEMPTS
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is not None:
            key = f"{_MFA_ATTEMPT_KEY_PREFIX}{jti}"
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, _MFA_ATTEMPT_TTL)
            results = await pipe.execute()
            # pipe.execute() returns results in order: [incr_result, expire_result]
            return int(results[0])
    except Exception:
        logger.warning(
            "SECURITY [H-03]: Redis unavailable for MFA attempt write, using in-memory fallback"
        )
    _mfa_attempt_tracker[jti] = _mfa_attempt_tracker.get(jti, 0) + 1
    _mfa_attempt_timestamps.setdefault(jti, time.time())
    return _mfa_attempt_tracker[jti]


async def _clear_mfa_attempts(jti: str) -> None:
    """Clear MFA attempt count for a given JTI (Redis-backed)."""
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is not None:
            await r.delete(f"{_MFA_ATTEMPT_KEY_PREFIX}{jti}")
            _mfa_attempt_tracker.pop(jti, None)
            _mfa_attempt_timestamps.pop(jti, None)
            return
    except Exception:
        logger.warning(
            "SECURITY [H-03]: Redis unavailable for MFA attempt clear, using in-memory fallback"
        )
    _mfa_attempt_tracker.pop(jti, None)
    _mfa_attempt_timestamps.pop(jti, None)


def _create_email_verification_token(user_id: str, email: str) -> str:
    """Create a time-limited JWT for email verification (24 hour expiry)."""
    expire = datetime.now(UTC) + timedelta(hours=24)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expire,
        "type": "email_verification",
        "jti": str(uuid.uuid4()),
        "iss": "constructai",
        "aud": "constructai-api",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _create_mfa_challenge_token(user_id: str, org_id: str) -> str:
    """Create a short-lived JWT for MFA verification (5 minute expiry)."""
    expire = datetime.now(UTC) + timedelta(minutes=5)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "exp": expire,
        "type": "mfa_challenge",
        "jti": str(uuid.uuid4()),
        "iss": "constructai",
        "aud": "constructai-api",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _send_verification_email(email: str, token: str) -> None:
    """Send email verification link via the configured email service."""
    from app.services.email.service import send_verification_email

    send_verification_email(email, token)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set httpOnly auth cookies on the response."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        path=settings.COOKIE_PATH,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        path="/api/v1/auth",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Clear httpOnly auth cookies."""
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        path=settings.COOKIE_PATH,
    )
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN or None,
        path="/api/v1/auth",
    )


class VerifyEmailRequest(BaseModel):
    token: str


# ---------------------------------------------------------------------------
# Registration + Email Verification
# ---------------------------------------------------------------------------


@router.post("/register", status_code=status.HTTP_200_OK)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Always return a generic message to prevent user enumeration
    _generic_response = {"detail": "If this email is valid, you will receive a verification email."}

    existing = await db.execute(select(User).where(User.email == request.email))
    if existing.scalar_one_or_none() is not None:
        # Email already registered — return generic message (no 409)
        return _generic_response

    org = await db.get(Organization, request.org_id)
    if org is None:
        # Invalid org — return generic message to avoid information leakage
        return _generic_response

    user = await register_user(
        db,
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        org_id=str(request.org_id),
    )

    # Generate and send email verification token
    token = _create_email_verification_token(str(user.id), user.email)
    _send_verification_email(user.email, token)

    audit_log(
        AuditAction.REGISTER,
        user_id=user.id,
        org_id=user.org_id,
        details={"email": user.email},
    )
    return _generic_response


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    request: VerifyEmailRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verify user email address using the verification token."""
    # Prune stale entries and guard against unbounded growth
    if len(_verify_email_tracker) > 10_000:
        _verify_email_tracker.clear()
    else:
        _prune_now = time.monotonic()
        _stale_keys = [
            k
            for k, v in _verify_email_tracker.items()
            if not v or v[-1] < _prune_now - _VERIFY_EMAIL_WINDOW
        ]
        for _k in _stale_keys:
            del _verify_email_tracker[_k]

    # SECURITY [M-04]: Rate limit verification attempts by IP to prevent brute-force
    client_ip = raw_request.client.host if raw_request.client else "unknown"
    now = time.monotonic()
    cutoff = now - _VERIFY_EMAIL_WINDOW
    _verify_email_tracker[client_ip] = [t for t in _verify_email_tracker[client_ip] if t > cutoff]
    _verify_email_tracker[client_ip].append(now)
    if len(_verify_email_tracker[client_ip]) > _VERIFY_EMAIL_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification attempts. Try again later.",
        )

    try:
        payload = jwt.decode(
            request.token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="constructai",
            audience="constructai-api",
        )
        if payload.get("type") != "email_verification":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification token",
            )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        ) from exc

    # RT6-AUTH-01: Prevent token reuse by checking JTI against used_reset_tokens table
    from app.api.v1.password_reset import _is_token_used, _mark_token_used

    if await _is_token_used(db, request.token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has already been used",
        )

    # SECURITY [L-02]: Do not reveal whether a user exists. Return the same
    # generic success message for missing users as for successful verification,
    # preventing email enumeration via the verify-email endpoint.
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return {"detail": "Email verified successfully"}

    # RT6-AUTH-01: Validate that the email claim matches the user's current email
    token_email = payload.get("email", "")
    if token_email.lower().strip() != user.email.lower().strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token does not match current email address",
        )

    user.email_verified = True
    db.add(user)
    await db.flush()

    # Mark the token as used to prevent reuse
    await _mark_token_used(db, request.token)

    audit_log(
        AuditAction.EMAIL_VERIFIED,
        user_id=user.id,
        org_id=user.org_id,
    )
    return {"detail": "Email verified successfully"}


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification(
    request: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    """Resend email verification token. Rate limited to 3 per hour."""
    email = request.email.lower().strip()

    # SEC-05: Redis-backed rate limiting BEFORE database lookup to prevent enumeration bypass
    if not await _check_verification_resend_limit(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification requests. Try again later.",
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        # Don't reveal whether email exists
        return {"detail": "If the email is registered, a verification link has been sent."}

    if user.email_verified:
        # Return the same generic message to prevent account state enumeration
        return {"detail": "If the email is registered, a verification link has been sent."}

    token = _create_email_verification_token(str(user.id), user.email)
    _send_verification_email(user.email, token)

    return {"detail": "If the email is registered, a verification link has been sent."}


# ---------------------------------------------------------------------------
# Login + MFA
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    # Normalize email for consistent lockout/lookup keying
    normalized_email = request.email.lower().strip()

    # Check account lockout before attempting authentication
    if await is_locked_out(normalized_email):
        audit_log(
            AuditAction.LOGIN_LOCKED,
            details={"email": normalized_email},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked. Try again later.",
        )

    tokens = await authenticate_user(db, email=normalized_email, password=request.password)
    if tokens is None:
        await record_failed_attempt(normalized_email)
        audit_log(
            AuditAction.LOGIN_FAILED,
            details={"email": normalized_email},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Check if user has MFA enabled — require 2nd step
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()
    if user and user.mfa_enabled:
        mfa_token = _create_mfa_challenge_token(str(user.id), str(user.org_id))
        await clear_failed_attempts(normalized_email)
        audit_log(
            AuditAction.LOGIN_SUCCESS,
            details={"email": normalized_email, "mfa_required": True},
        )
        return TokenResponse(
            access_token="",
            refresh_token="",
            mfa_required=True,
            mfa_token=mfa_token,
        )

    # SEC-05: Check if MFA is required for this role but not yet set up.
    # Do NOT grant full access — return a limited response indicating MFA setup
    # is required. The user must set up MFA before gaining full access.
    if user and user.role in _MFA_REQUIRED_ROLES and not user.mfa_enabled:
        await clear_failed_attempts(normalized_email)
        audit_log(
            AuditAction.LOGIN_SUCCESS,
            details={"email": normalized_email, "mfa_setup_required": True},
        )
        # Issue a limited-scope token for MFA setup only (reuse MFA challenge token)
        mfa_setup_token = _create_mfa_challenge_token(str(user.id), str(user.org_id))
        return TokenResponse(
            access_token="",
            refresh_token="",
            mfa_required=True,
            mfa_token=mfa_setup_token,
            mfa_setup_required=True,
        )

    # Successful login - clear any recorded failures
    # IMPORTANT: use normalized_email to match how attempts are recorded
    await clear_failed_attempts(normalized_email)
    audit_log(
        AuditAction.LOGIN_SUCCESS,
        details={"email": normalized_email},
    )
    _set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    # Return empty token fields — auth relies exclusively on httpOnly cookies.
    # Returning actual tokens in JSON bodies would expose them to XSS theft.
    return TokenResponse(mfa_required=False)


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(
    request: MfaLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Verify TOTP code during login (2nd step of MFA flow)."""
    try:
        payload = jwt.decode(
            request.mfa_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="constructai",
            audience="constructai-api",
        )
        if payload.get("type") != "mfa_challenge":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid MFA token",
            )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired MFA token",
        ) from exc

    # RT6-AUTH-04: Check if MFA token JTI is blacklisted (exhausted attempts or already used)
    mfa_jti = payload.get("jti")
    if mfa_jti:
        from app.dependencies import is_token_blacklisted

        if await is_token_blacklisted(mfa_jti):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA token has already been used or too many failed attempts",
            )

        # SECURITY [H-03]: Check per-token attempt count (Redis-backed)
        if await _get_mfa_attempts(mfa_jti) >= _MFA_MAX_ATTEMPTS:
            await blacklist_token(mfa_jti)
            await _clear_mfa_attempts(mfa_jti)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed MFA attempts. Request a new login.",
            )

    user_id = payload.get("sub")
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # SECURITY [M-03]: Reject if MFA secret exists but hasn't been verified yet
    if not user.mfa_secret or getattr(user, "mfa_pending", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not configured")

    # Try TOTP first, then backup codes
    mfa_valid = False
    if verify_totp(user.mfa_secret, request.code):
        mfa_valid = True
    elif user.mfa_backup_codes:
        user_salt = getattr(user, "mfa_backup_salt", "") or ""
        idx = verify_backup_code(request.code, user.mfa_backup_codes, user_salt)
        if idx is not None:
            mfa_valid = True
            # Consume the backup code
            codes = list(user.mfa_backup_codes)
            codes.pop(idx)
            user.mfa_backup_codes = codes
            db.add(user)
            await db.flush()

    if not mfa_valid:
        # SECURITY [H-03]: Increment attempt counter for this MFA token (Redis-backed)
        if mfa_jti:
            attempts = await _increment_mfa_attempts(mfa_jti)
            if attempts >= _MFA_MAX_ATTEMPTS:
                await blacklist_token(mfa_jti)
                await _clear_mfa_attempts(mfa_jti)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed MFA attempts. Request a new login.",
                )
        audit_log(
            AuditAction.LOGIN_FAILED,
            user_id=user.id,
            details={"reason": "invalid_mfa_code"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    # Blacklist MFA token after successful verification to prevent replay
    if mfa_jti:
        await blacklist_token(mfa_jti)
        await _clear_mfa_attempts(mfa_jti)

    # Issue real tokens
    token_data = {
        "sub": str(user.id),
        "org_id": str(user.org_id),
        "token_version": getattr(user, "token_version", 0),
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    audit_log(
        AuditAction.LOGIN_SUCCESS,
        user_id=user.id,
        org_id=user.org_id,
        details={"mfa_verified": True},
    )
    _set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse()


# ---------------------------------------------------------------------------
# MFA Setup
# ---------------------------------------------------------------------------


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TOTP secret and QR code for MFA setup."""
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled. Disable it first to reconfigure.",
        )

    secret = generate_totp_secret()
    uri = get_totp_uri(secret, current_user.email)
    qr_code = generate_qr_code_data_uri(uri)

    # SECURITY [P1-7]: Store secret in Redis with short TTL instead of persisting
    # to the DB before the user proves possession via verify-setup.
    # SECURITY [S1]: Encrypt the secret before storing in Redis.
    fernet = _mfa_fernet()
    encrypted_secret = fernet.encrypt(secret.encode()).decode()
    cache = CacheService()
    await cache.set(f"cai:mfa_setup:{current_user.id}", encrypted_secret, ttl=300)

    return MfaSetupResponse(qr_code=qr_code, secret=secret, provisioning_uri=uri)


@router.post("/mfa/verify-setup", response_model=MfaSetupVerifyResponse)
async def mfa_verify_setup(
    request: MfaVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm MFA setup by verifying the first TOTP code. Returns backup codes."""
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled",
        )

    # SECURITY [P1-7]: Read the pending MFA secret from Redis cache
    # SECURITY [S1]: Decrypt the secret after retrieval from Redis.
    cache = CacheService()
    encrypted_secret = await cache.get(f"cai:mfa_setup:{current_user.id}")
    if encrypted_secret is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA setup expired. Please call /auth/mfa/setup again.",
        )
    fernet = _mfa_fernet()
    secret = fernet.decrypt(encrypted_secret.encode()).decode()

    if not verify_totp(secret, request.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code. Check your authenticator app and try again.",
        )

    # RT6-AUTH-16: Generate backup codes with per-user salt
    plaintext_codes, hashed_codes, salt = generate_backup_codes()

    # Persist the verified secret and enable MFA
    current_user.mfa_secret = secret
    current_user.mfa_enabled = True
    current_user.mfa_backup_codes = hashed_codes
    current_user.mfa_backup_salt = salt
    current_user.mfa_enforced_at = datetime.now(UTC)
    db.add(current_user)
    await db.flush()

    # Clean up the temporary cache entry
    await cache.delete(f"cai:mfa_setup:{current_user.id}")

    audit_log(
        AuditAction.RESOURCE_UPDATED,
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="mfa",
        details={"action": "mfa_enabled"},
    )

    return MfaSetupVerifyResponse(backup_codes=plaintext_codes)


@router.delete("/mfa/disable", status_code=status.HTTP_200_OK)
async def mfa_disable(
    request: MfaVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable MFA. Requires a valid TOTP code or backup code."""
    if not current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled",
        )

    # Verify with TOTP or backup code
    valid = False
    if current_user.mfa_secret and verify_totp(current_user.mfa_secret, request.code):
        valid = True
    elif current_user.mfa_backup_codes:
        cu_salt = getattr(current_user, "mfa_backup_salt", "") or ""
        idx = verify_backup_code(request.code, current_user.mfa_backup_codes, cu_salt)
        if idx is not None:
            valid = True

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid code. Provide a valid TOTP or backup code.",
        )

    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_backup_codes = None
    current_user.mfa_enforced_at = None
    db.add(current_user)
    await db.flush()

    audit_log(
        AuditAction.RESOURCE_UPDATED,
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="mfa",
        details={"action": "mfa_disabled"},
    )
    return {"detail": "MFA disabled successfully"}


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshRequest,
    raw_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    # Support refresh via body OR httpOnly cookie
    refresh_tok = request.refresh_token
    if not refresh_tok:
        refresh_tok = raw_request.cookies.get("refresh_token", "")
    if not refresh_tok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )
    tokens = await refresh_tokens(db, refresh_token=refresh_tok)
    if tokens is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    # SECURITY [H-04]: Blacklist the old refresh token to prevent reuse.
    # The old token is single-use; after rotation, it must be invalidated.
    old_payload = decode_refresh_token(refresh_tok)
    if old_payload and "jti" in old_payload:
        await blacklist_token(
            old_payload["jti"],
            ttl_seconds=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

    _set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    return TokenResponse()


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    raw_request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    current_user: User = Depends(get_current_user),
):
    """Log out the current user.

    Blacklists the token's JTI so that it is rejected on subsequent
    requests for the remainder of its lifetime.
    """
    # Get token from Bearer header or cookie
    token = credentials.credentials if credentials else None
    if not token:
        token = raw_request.cookies.get("access_token")

    if token:
        payload = decode_access_token(token)
        if payload and "jti" in payload:
            await blacklist_token(payload["jti"])

    # SECURITY [H-01]: Also blacklist the refresh token JTI.
    # The refresh token (7-day lifetime) must be invalidated on logout to
    # prevent it from being used to mint new access tokens after logout.
    refresh_tok = raw_request.cookies.get("refresh_token")
    if refresh_tok:
        refresh_payload = decode_refresh_token(refresh_tok)
        if refresh_payload and "jti" in refresh_payload:
            refresh_ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
            await blacklist_token(refresh_payload["jti"], ttl_seconds=refresh_ttl)

    _clear_auth_cookies(response)

    audit_log(
        AuditAction.LOGOUT,
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    return {"detail": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
