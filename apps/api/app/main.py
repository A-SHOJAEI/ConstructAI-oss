import asyncio
import hmac
import logging
import signal
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.router import api_router
from app.config import settings
from app.error_handlers import (
    http_exception_handler,
    integrity_error_handler,
    unhandled_exception_handler,
    validation_exception_handler,
    value_error_handler,
)
from app.middleware import RequestLoggingMiddleware
from app.middleware.audit import AuditContextMiddleware
from app.middleware.compression import CompressionMiddleware
from app.middleware.csrf import CSRFMiddleware
from app.middleware.rate_limiter import RateLimiter
from app.middleware.response_profiler import ResponseProfiler
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.tenant_context import TenantContextMiddleware
from app.services.observability.structured_logger import setup_logging
from app.services.observability.tracing import TracingSetup
from app.services.security.redis_state import require_redis_for_production

logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 50 * 1024 * 1024  # 50 MB


def _check_feature_dependencies():
    """Check that runtime dependencies for enabled features are available."""
    checks = [
        ("confluent_kafka", "Kafka event streaming (webhooks, async processing)"),
        ("timm", "ML defect classification"),
        ("faster_whisper", "Voice transcription"),
    ]
    missing = []
    for module_name, feature_desc in checks:
        try:
            __import__(module_name)
            logger.info("Dependency check OK: %s (%s)", module_name, feature_desc)
        except ImportError:
            missing.append((module_name, feature_desc))
            logger.warning(
                "MISSING DEPENDENCY: %s — %s will be degraded", module_name, feature_desc
            )

    if missing and settings.ENVIRONMENT in ("production", "staging"):
        logger.critical(
            "Production deployment missing %d feature dependencies: %s",
            len(missing),
            ", ".join(m[0] for m in missing),
        )


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured limit.

    SECURITY (H-08): Also enforces the limit on chunked transfers (no
    Content-Length header) by wrapping the ASGI receive callable to track
    accumulated bytes.
    """

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return Response("Request body too large", status_code=413)
            except (ValueError, OverflowError):
                return Response("Invalid Content-Length", status_code=400)
        else:
            # SECURITY (H-08): For chunked/streaming requests without
            # Content-Length, wrap receive to enforce the byte limit.
            received_bytes = 0
            original_receive = request.receive

            async def _size_limited_receive():
                nonlocal received_bytes
                message = await original_receive()
                body = message.get("body", b"")
                received_bytes += len(body)
                if received_bytes > MAX_REQUEST_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Request body too large")
                return message

            request._receive = _size_limited_receive

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT in ("staging", "production"):
        settings.validate_production_config()
        # L-1: loud startup warning if /metrics is left unauthenticated in prod.
        if getattr(settings, "METRICS_ALLOW_ANONYMOUS", False):
            logger.warning(
                "METRICS_ALLOW_ANONYMOUS=True in %s — /metrics is unauthenticated. "
                "Set METRICS_TOKEN and unset METRICS_ALLOW_ANONYMOUS to secure.",
                settings.ENVIRONMENT,
            )

    # SECURITY: Validate ENCRYPTION_KEY is non-empty in all environments.
    # In development, log a loud warning; in staging/production, refuse to start.
    if not settings.ENCRYPTION_KEY:
        if settings.ENVIRONMENT in ("staging", "production"):
            raise RuntimeError(
                f"ENCRYPTION_KEY must be set in {settings.ENVIRONMENT} environment. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        else:
            logger.warning(
                "SECURITY WARNING: ENCRYPTION_KEY is not set. "
                "Field-level encryption is disabled. This is acceptable for "
                "local development but MUST be configured in staging/production."
            )

    # SECURITY [H-02]: Ensure Redis is reachable (required in staging/production).
    await require_redis_for_production()

    # Verify critical feature dependencies
    _check_feature_dependencies()

    # --- Observability bootstrap ---
    # 1. Structured logging (structlog JSON output)
    setup_logging()
    logger.info("Structured logging initialised")

    # 2. Distributed tracing (OpenTelemetry, best-effort)
    tracing = TracingSetup()
    tracing.setup(app=app)

    # 3. Prometheus metrics are lazily initialised on first use;
    #    the /metrics ASGI sub-app is mounted in create_app().

    # 4. Pre-warm the local cross-encoder reranker so the first /ask doesn't
    # eat the 3-5 s model-load on the user-facing latency path. Best-effort:
    # any failure here just falls through to lazy load on first call.
    try:
        from app.services.rag.reranker import _get_local_reranker

        await asyncio.to_thread(_get_local_reranker)
        logger.info("reranker pre-warmed at startup")
    except Exception as exc:
        logger.warning("reranker pre-warm failed (non-fatal): %s", exc)

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_sigterm(*_args):
        logger.info("Received SIGTERM, initiating graceful shutdown...")
        shutdown_event.set()

    import sys

    if sys.platform != "win32":
        try:
            signal.signal(signal.SIGTERM, handle_sigterm)
        except ValueError:
            logger.warning("Could not register SIGTERM handler (not on main thread)")

    yield

    # Graceful shutdown: allow in-flight requests to complete
    logger.info("Shutting down gracefully...")
    from app.database import engine

    await engine.dispose()


def create_app() -> FastAPI:
    # SECURITY [L-06]: Disable OpenAPI docs in both staging and production.
    # Only expose docs in development environments.
    _enable_docs = settings.ENVIRONMENT == "development"
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if _enable_docs else None,
        redoc_url="/redoc" if _enable_docs else None,
    )

    # Middleware stack (outermost to innermost).
    # Note: FastAPI adds middleware in reverse order, so the last
    # add_middleware call is the outermost layer.
    # Parse and validate CORS origins — reject wildcard when credentials are enabled.
    _cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if "*" in _cors_origins and len(_cors_origins) > 0:
        logger.warning(
            "CORS_ORIGINS contains '*' which is incompatible with allow_credentials=True. "
            "Removing wildcard — set explicit origins instead."
        )
        _cors_origins = [o for o in _cors_origins if o != "*"]
    # SEC-03: Validate CORS origins match FRONTEND_URL domain in production/staging
    if settings.ENVIRONMENT in ("production", "staging"):
        frontend_domain = urlparse(settings.FRONTEND_URL).hostname
        for origin in _cors_origins:
            origin_domain = urlparse(origin).hostname
            if origin_domain and frontend_domain and origin_domain != frontend_domain:
                logger.warning(
                    "CORS origin %s does not match FRONTEND_URL domain %s",
                    origin,
                    frontend_domain,
                )
    # Middleware stack: FastAPI adds in reverse — LAST add_middleware = outermost.
    # CORS must be outermost to handle OPTIONS preflight before auth/tenant checks.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(AuditContextMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(
        RateLimiter,
        default_limit=settings.RATE_LIMIT_DEFAULT,
        burst_limit=settings.RATE_LIMIT_BURST,
    )
    app.add_middleware(ResponseProfiler)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(CompressionMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    # CORS outermost — must be last so it wraps everything and handles OPTIONS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        max_age=600,
    )

    # Exception handlers (order matters: more specific first)
    # Starlette's add_exception_handler signature declares `Exception` for the
    # second arg; our handlers are typed to specific subclasses. cast to keep
    # handler signatures specific while satisfying the Starlette API.
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
    # IntegrityError from SQLAlchemy (always available — SQLAlchemy is a core dep)
    from sqlalchemy.exc import IntegrityError

    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(api_router, prefix=settings.API_PREFIX)

    # Mount Prometheus /metrics endpoint (API-key protected)
    try:
        from prometheus_client import make_asgi_app as _make_metrics_app

        _raw_metrics_app = _make_metrics_app()

        async def _metrics_auth_wrapper(scope, receive, send):
            """Protect /metrics with Bearer token or METRICS_TOKEN."""
            if scope["type"] == "http":
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode()
                metrics_token = getattr(settings, "METRICS_TOKEN", "")
                # SECURITY [P2-2]: Explicit boolean checks instead of string truthiness
                has_auth_header = isinstance(auth, str) and len(auth.strip()) > 0
                has_token_configured = (
                    isinstance(metrics_token, str) and len(metrics_token.strip()) > 0
                )
                # SECURITY [M-18]: When METRICS_TOKEN is unset, deny access
                # unless METRICS_ALLOW_ANONYMOUS is explicitly true.
                metrics_allow_anon = getattr(settings, "METRICS_ALLOW_ANONYMOUS", False)
                if not has_auth_header and not has_token_configured and not metrics_allow_anon:
                    from starlette.responses import JSONResponse

                    resp = JSONResponse(
                        {"detail": "Forbidden: METRICS_TOKEN not configured"},
                        status_code=403,
                    )
                    await resp(scope, receive, send)
                    return
                elif not has_auth_header and not has_token_configured and metrics_allow_anon:
                    pass  # Explicitly allowed anonymous access
                elif has_token_configured and hmac.compare_digest(auth, f"Bearer {metrics_token}"):
                    pass  # Metrics token matches
                elif auth.startswith("Bearer "):
                    from app.utils.security import decode_access_token

                    payload = decode_access_token(auth[7:])
                    if payload is None:
                        from starlette.responses import JSONResponse

                        resp = JSONResponse({"detail": "Unauthorized"}, status_code=401)
                        await resp(scope, receive, send)
                        return
                    # Valid JWT accepted — metrics is operational data, not customer data.
                    # Role-based restriction requires DB lookup; deferred to post-launch.
                else:
                    from starlette.responses import JSONResponse

                    resp = JSONResponse({"detail": "Unauthorized"}, status_code=401)
                    await resp(scope, receive, send)
                    return
            await _raw_metrics_app(scope, receive, send)

        app.mount("/metrics", _metrics_auth_wrapper)
        logger.info("Prometheus /metrics endpoint mounted (auth-protected)")
    except ImportError:
        logger.warning("prometheus_client not installed; /metrics endpoint disabled")

    return app


app = create_app()
