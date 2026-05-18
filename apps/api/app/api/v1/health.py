from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": settings.APP_VERSION}


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Check readiness of the application.

    SECURITY [L-07]: In production/staging, only return aggregate status
    without exposing individual component states to prevent information
    disclosure to unauthenticated callers.
    """
    components = {}
    overall = "healthy"

    try:
        await db.execute(text("SELECT 1"))
        components["database"] = "healthy"
    except Exception:
        components["database"] = "unhealthy"
        overall = "unhealthy"

    # SECURITY [L-07]: Redact component details in non-development environments.
    if settings.ENVIRONMENT == "development":
        payload = {"status": overall, "components": components}
    else:
        payload = {"status": overall}

    if overall != "healthy":
        return JSONResponse(content=payload, status_code=503)
    return payload
