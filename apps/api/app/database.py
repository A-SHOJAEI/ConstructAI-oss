from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# SECURITY: SQL echo is deliberately disabled (never use echo=True or echo=settings.DEBUG)
# because query logging can leak sensitive data (passwords, PII, tokens) into log files.
# Production deployments MUST use ?sslmode=require in DATABASE_URL to encrypt connections.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_recycle=1800,  # Recycle connections after 30 minutes
    pool_pre_ping=True,  # Verify connections before use
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session as a FastAPI dependency.

    **Transaction lifecycle** -- This dependency manages the full
    commit / rollback cycle automatically:

    * On success (no exception): the session is **committed**.
    * On failure (any exception): the session is **rolled back** and
      the exception re-raised so FastAPI returns an appropriate error.
    * In all cases the session is **closed** in the ``finally`` block.

    **IMPORTANT -- never call ``db.commit()`` explicitly in endpoint
    handlers or service functions.**  Doing so breaks the atomicity
    guarantee because a later failure would leave the database in a
    partially-committed state that this dependency can no longer roll
    back.

    If you need to persist intermediate state (e.g. to obtain a
    generated primary key before the final commit), use
    ``await db.flush()`` instead.  ``flush()`` sends the SQL to the
    database but keeps it inside the current transaction.

    For optional / best-effort operations that should not abort the
    whole request on failure, wrap them in a **savepoint**::

        async with db.begin_nested():  # SAVEPOINT
            db.add(optional_record)
            await db.flush()
        # If the above fails, only the savepoint is rolled back;
        # the outer transaction continues normally.

    Following these rules ensures every API request is a single atomic
    database transaction -- either everything succeeds or nothing is
    persisted.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
