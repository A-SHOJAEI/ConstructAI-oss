import logging
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    # SECURITY [S6]: Read rounds from settings to allow tuning per environment
    from app.config import settings as _settings

    rounds = getattr(_settings, "BCRYPT_ROUNDS", 12)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update(
        {
            "exp": expire,
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
    )
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update(
        {
            "exp": expire,
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
    )
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience="constructai-api",
            issuer="constructai",
        )
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        # Fallback to previous key during key rotation
        previous_key = getattr(settings, "JWT_SECRET_KEY_PREVIOUS", None)
        if previous_key:
            try:
                payload = jwt.decode(
                    token,
                    previous_key,
                    algorithms=[settings.JWT_ALGORITHM],
                    audience="constructai-api",
                    issuer="constructai",
                )
                if payload.get("type") != "access":
                    return None
                return payload
            except jwt.PyJWTError:
                pass
        return None


def decode_refresh_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience="constructai-api",
            issuer="constructai",
        )
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.PyJWTError:
        # Fallback to previous key during key rotation
        previous_key = getattr(settings, "JWT_SECRET_KEY_PREVIOUS", None)
        if previous_key:
            try:
                payload = jwt.decode(
                    token,
                    previous_key,
                    algorithms=[settings.JWT_ALGORITHM],
                    audience="constructai-api",
                    issuer="constructai",
                )
                if payload.get("type") != "refresh":
                    return None
                return payload
            except jwt.PyJWTError:
                pass
        return None


# ---------------------------------------------------------------------------
# JWT Key Rotation (Redis-backed)
# ---------------------------------------------------------------------------


async def _get_jwt_key_from_redis(key_name: str) -> str | None:
    """Get a JWT key from Redis. Returns None if unavailable."""
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is None:
            return None
        return await r.get(f"cai:jwt:{key_name}")
    except Exception:
        return None


async def rotate_jwt_key(new_key: str) -> int:
    """Rotate JWT signing key. Stores current as previous in Redis.

    Returns the new key version number.
    Raises RuntimeError if Redis is unavailable.
    """
    from app.services.security.redis_state import _get_redis

    r = await _get_redis()
    if r is None:
        raise RuntimeError("Redis required for JWT key rotation")

    pipe = r.pipeline()
    pipe.set("cai:jwt:previous_key", settings.JWT_SECRET_KEY)
    pipe.set("cai:jwt:current_key", new_key)
    pipe.incr("cai:jwt:version")
    results = await pipe.execute()
    version = results[2]  # incr returns new value

    logger.info("JWT key rotated to version %d", version)
    return version


async def get_jwt_key_version() -> int:
    """Get current JWT key version from Redis."""
    val = await _get_jwt_key_from_redis("version")
    return int(val) if val else 1
