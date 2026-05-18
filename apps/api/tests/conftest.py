import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

# Set test-specific env vars BEFORE loading .env so they aren't overwritten
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters-long")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("ENVIRONMENT", "development")
# Auth cookies default to Secure=True (HTTPS-only). The httpx test client uses
# http://test, so secure cookies are dropped — leaving the refresh endpoint
# without the refresh_token cookie. Disable for tests so the cookie-based
# refresh flow works end-to-end.
os.environ.setdefault("COOKIE_SECURE", "false")
# Rate limiter defaults to "redis" so production scales across processes.
# Tests run function-scoped apps but share one Redis container, so a Redis
# backend would leak counts across tests and trip 429s in unrelated tests
# (e.g. account-lockout vs reset-password). Use the in-memory backend so
# each test's create_app() builds a fresh dict — production cap (10/min)
# stays unchanged.
os.environ.setdefault("RATE_LIMIT_BACKEND", "memory")
# SAFETY: refuse to run if DATABASE_URL points at anything that doesn't have
# "_test" in its DB name. The fixtures DROP SCHEMA public CASCADE at teardown,
# so accidentally pointing them at the production DB destroys all demo data
# (we hit this once during the spark-2 build — never again).
_TEST_DB_DEFAULT = "postgresql+asyncpg://constructai:constructai@localhost:5530/constructai_test"
os.environ["DATABASE_URL"] = os.environ.get("PYTEST_DATABASE_URL", _TEST_DB_DEFAULT)
if "_test" not in os.environ["DATABASE_URL"]:
    raise RuntimeError(
        f"Refusing to run tests against non-_test database: {os.environ['DATABASE_URL']!r}. "
        "Set PYTEST_DATABASE_URL to a URL whose database name contains '_test', "
        "or run tests against the constructai_test DB."
)

# Load .env file so API keys (BLS, FRED, etc.) are available in tests
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[1] / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path, override=False)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.organization import Organization
from app.models.user import User
from app.utils.security import create_access_token, hash_password

# Use test database URL — guaranteed to contain "_test" by the safety check above.
TEST_DATABASE_URL = os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Ensure required extensions exist
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Add pgvector embedding column (not represented in ORM metadata)
        await conn.execute(
            text("ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS embedding vector(1024)")
        )
    yield engine
    async with engine.begin() as conn:
        # Use CASCADE drop to handle unnamed FK constraints
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO constructai"))
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def override_get_db():
        # NOTE: don't rollback on plain HTTPException — those are normal
        # HTTP responses (401/403/404/etc.). Rolling back wipes uncommitted
        # fixture state (test_user, test_org), causing follow-up requests
        # in the same test to see an empty DB and return spurious 401s.
        from fastapi import HTTPException

        try:
            yield db_session
        except HTTPException:
            raise
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def test_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="Test Organization",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        type="gc",
    )
    db_session.add(org)
    await db_session.flush()
    await db_session.refresh(org)
    return org


@pytest_asyncio.fixture(scope="function")
async def test_user(db_session: AsyncSession, test_org: Organization) -> User:
    user = User(
        email=f"testuser-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("TestPassword123!"),
        full_name="Test User",
        org_id=test_org.id,
        role="org_admin",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture(scope="function")
async def auth_headers(test_user: User) -> dict:
    token = create_access_token(data={"sub": str(test_user.id), "org_id": str(test_user.org_id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limiter_backend(request):
    """Reset the rate-limiter middleware's in-memory backend between tests.

    Each test calls ``create_app()`` which builds a new middleware
    instance, but the test client mounts that app under a single
    ``testclient`` host — so without a per-test reset, intra-test counts
    accumulate as expected, but the test author can't rely on a clean
    slate when a class makes many ``/auth/*`` calls in sequence. Clearing
    the dict between tests preserves the production cap while keeping
    tests deterministic, instead of bumping the cap to a meaningless
    number that would mask real 429 regressions.
    """
    yield
    # Find the live RateLimiter instance on the test client's app, if any.
    # The middleware stack is a linked list via ``app`` attribute; walk it
    # until we find ours.
    from app.middleware.rate_limiter import MemoryRateLimiterBackend, RateLimiter

    client = request.node.funcargs.get("client")
    if client is None:
        return
    asgi = getattr(client, "_transport", None)
    asgi_app = getattr(asgi, "app", None)
    cursor = asgi_app
    while cursor is not None:
        if isinstance(cursor, RateLimiter):
            backend = cursor._backend
            if isinstance(backend, MemoryRateLimiterBackend):
                backend._requests.clear()
            break
        cursor = getattr(cursor, "app", None)


@pytest.fixture(autouse=True)
def _guard_external_apis(monkeypatch):
    """Prevent accidental real API calls in tests by clearing external API keys.

    This fixture runs automatically for every test. It removes known external
    service API keys from the environment so that any code path that reads
    them at runtime will get an empty value, causing the call to fail fast
    rather than hitting a real (and potentially billable) external API.
    """
    for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "FRED_API_KEY",
        "BLS_API_KEY",
        "OPENWEATHERMAP_API_KEY",
        "PROCORE_CLIENT_ID",
        "PROCORE_CLIENT_SECRET",
        "COHERE_API_KEY",
        "VOYAGE_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_state_per_test():
    """Reset cached Redis client and in-memory fallback state between tests.

    pytest-asyncio creates a fresh event loop per test (default
    function-scoped loop). The Redis client cached at module level was
    created in a previous loop and its connection pool is bound to that
    loop, so subsequent tests get ConnectionError / runtime errors that
    the auth flow interprets as fail-closed (returning 401).

    The in-memory fallback dicts (``_memory_blacklist``,
    ``_memory_failed_attempts``) also persist for the lifetime of the
    process — leftover entries from earlier tests would surface as
    "blacklisted token" or "account locked" responses against fresh
    state. Reset both so each test starts cleanly.
    """
    from app.services.security import redis_state

    yield

    client = redis_state._redis_client
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass
    redis_state._redis_client = None
    redis_state._redis_available = None
    redis_state._memory_blacklist.clear()
    redis_state._memory_failed_attempts.clear()
