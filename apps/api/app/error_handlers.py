"""Global exception handlers for structured error responses."""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": request_id,
        },
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    errors = []
    for err in exc.errors():
        errors.append(
            {
                "field": " -> ".join(str(loc) for loc in err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation error",
            "errors": errors,
            "request_id": request_id,
        },
    )


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Return 422 for ValueError (invalid input that passed Pydantic validation)."""
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(exc),
            "request_id": request_id,
        },
    )


async def integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return 409 for database IntegrityError (duplicate/constraint violations)."""
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    logger.warning("Database integrity error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=409,
        content={
            "detail": "A conflict occurred. The resource may already exist or a constraint was violated.",
            "request_id": request_id,
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    # Log the full stack trace via exc_info so it goes to structured logs
    # but is NOT included in the HTTP response body.
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        },
    )
