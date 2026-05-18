"""Tests for the request logging and response profiler middlewares.

Pin documented thresholds (slow=500ms, N+1=10 queries), the
X-Request-ID header contract, and the get_stats percentile math.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.response_profiler import ResponseProfiler

# =========================================================================
# RequestLoggingMiddleware — request_id + X-Request-ID header
# =========================================================================


@pytest.mark.asyncio
async def test_request_logging_assigns_uuid_request_id():
    """[contract] Each request gets a fresh UUID as request_id, set
    on request.state and exposed via X-Request-ID response header.
    Pin so a refactor doesn't quietly switch to a non-UUID format."""
    fake_request = MagicMock()
    fake_request.method = "GET"
    fake_request.url.path = "/api/v1/projects"
    fake_request.state = MagicMock(spec=["request_id"])
    fake_response = MagicMock()
    fake_response.headers = {}
    fake_response.status_code = 200

    async def fake_next(_request):
        return fake_response

    middleware = RequestLoggingMiddleware(app=MagicMock())
    out = await middleware.dispatch(fake_request, fake_next)

    # Valid UUID:
    uuid.UUID(fake_request.state.request_id)
    # Same UUID echoed in response header:
    assert out.headers["X-Request-ID"] == fake_request.state.request_id


@pytest.mark.asyncio
async def test_request_logging_each_request_unique_id():
    """[invariant] Each request gets a different UUID — no shared
    state across requests."""
    middleware = RequestLoggingMiddleware(app=MagicMock())

    async def fake_next(_request):
        resp = MagicMock()
        resp.headers = {}
        resp.status_code = 200
        return resp

    ids = []
    for _ in range(5):
        fake_request = MagicMock()
        fake_request.method = "GET"
        fake_request.url.path = "/x"
        fake_request.state = MagicMock(spec=["request_id"])
        await middleware.dispatch(fake_request, fake_next)
        ids.append(fake_request.state.request_id)

    assert len(set(ids)) == 5  # all unique


# =========================================================================
# ResponseProfiler — constants
# =========================================================================


def test_response_profiler_slow_threshold_500ms():
    """[contract] 500ms slow-request threshold. Pin so a refactor
    doesn't quietly raise (loses sensitivity) or lower (alert noise)."""
    assert ResponseProfiler.SLOW_THRESHOLD_MS == 500


def test_response_profiler_n_plus_one_threshold_10():
    """[contract] 10 queries/request triggers N+1 warning. Pin so
    refactor doesn't quietly raise (misses N+1 bugs) or lower
    (false positives on legitimate batch endpoints)."""
    assert ResponseProfiler.N_PLUS_ONE_THRESHOLD == 10


def test_response_profiler_default_slow_threshold():
    """Default constructor uses 500ms (matches class constant)."""
    profiler = ResponseProfiler(app=MagicMock())
    assert profiler.slow_threshold_ms == 500


def test_response_profiler_custom_slow_threshold():
    """Custom threshold passed to __init__ is honored."""
    profiler = ResponseProfiler(app=MagicMock(), slow_threshold_ms=1000)
    assert profiler.slow_threshold_ms == 1000


def test_response_profiler_request_stats_capped_at_1000():
    """[memory] Request stats deque is bounded at 1000 (no unbounded
    growth)."""
    profiler = ResponseProfiler(app=MagicMock())
    assert profiler._request_stats.maxlen == 1000


# =========================================================================
# ResponseProfiler — dispatch + X-Response-Time
# =========================================================================


@pytest.mark.asyncio
async def test_profiler_adds_x_response_time_header():
    """[contract] X-Response-Time header set on every response in
    'XYZms' format. Pin so a refactor doesn't break frontend
    perf monitoring."""
    fake_request = MagicMock()
    fake_request.method = "GET"
    fake_request.url.path = "/x"
    fake_request.state = MagicMock(spec=["query_count"])
    fake_response = MagicMock()
    fake_response.headers = {}
    fake_response.status_code = 200

    async def fake_next(_request):
        return fake_response

    profiler = ResponseProfiler(app=MagicMock())
    with patch("app.middleware.response_profiler.record_http_request"):
        out = await profiler.dispatch(fake_request, fake_next)

    assert "X-Response-Time" in out.headers
    assert out.headers["X-Response-Time"].endswith("ms")


@pytest.mark.asyncio
async def test_profiler_initializes_query_count_zero():
    """[contract] request.state.query_count starts at 0 — incremented
    by SQLAlchemy event hooks in production. Pin: refactor must NOT
    skip initialization (would break N+1 detection)."""
    fake_request = MagicMock()
    fake_request.method = "GET"
    fake_request.url.path = "/x"
    fake_request.state = MagicMock(spec=["query_count"])
    fake_response = MagicMock()
    fake_response.headers = {}
    fake_response.status_code = 200

    async def fake_next(req):
        # Verify state.query_count was set BEFORE call_next runs:
        assert req.state.query_count == 0
        return fake_response

    profiler = ResponseProfiler(app=MagicMock())
    with patch("app.middleware.response_profiler.record_http_request"):
        await profiler.dispatch(fake_request, fake_next)


@pytest.mark.asyncio
async def test_profiler_records_http_request_metric():
    """[contract] Every dispatch records to Prometheus via
    record_http_request(method, endpoint, status). Pin so a refactor
    doesn't break /metrics output."""
    fake_request = MagicMock()
    fake_request.method = "POST"
    fake_request.url.path = "/api/v1/projects"
    fake_request.state = MagicMock(spec=["query_count"])
    fake_response = MagicMock()
    fake_response.headers = {}
    fake_response.status_code = 201

    async def fake_next(_request):
        return fake_response

    profiler = ResponseProfiler(app=MagicMock())
    with patch("app.middleware.response_profiler.record_http_request") as fake_record:
        await profiler.dispatch(fake_request, fake_next)

    fake_record.assert_called_once_with(
        method="POST",
        endpoint="/api/v1/projects",
        status=201,
    )


@pytest.mark.asyncio
async def test_profiler_appends_to_request_stats():
    """Each dispatch appends to the bounded stats deque."""
    fake_request = MagicMock()
    fake_request.method = "GET"
    fake_request.url.path = "/x"
    fake_request.state = MagicMock(spec=["query_count"])
    fake_response = MagicMock()
    fake_response.headers = {}
    fake_response.status_code = 200

    async def fake_next(_request):
        return fake_response

    profiler = ResponseProfiler(app=MagicMock())
    with patch("app.middleware.response_profiler.record_http_request"):
        await profiler.dispatch(fake_request, fake_next)
        await profiler.dispatch(fake_request, fake_next)

    assert len(profiler._request_stats) == 2
    assert profiler._request_stats[0]["method"] == "GET"
    assert profiler._request_stats[0]["path"] == "/x"


# =========================================================================
# ResponseProfiler.get_stats — percentile math
# =========================================================================


def test_get_stats_empty_returns_zero_count():
    """[edge case] No requests yet -> total_requests=0, no /0 NaN."""
    profiler = ResponseProfiler(app=MagicMock())
    out = profiler.get_stats()
    assert out == {"total_requests": 0}


def test_get_stats_computes_avg_p95_p99():
    """[contract] Pin avg + p95 + p99 calculation. Sorted durations:
    p95 = sorted[int(N * 0.95)], p99 = sorted[int(N * 0.99)]."""
    profiler = ResponseProfiler(app=MagicMock())
    # Inject 100 fake records with durations 1..100:
    for i in range(1, 101):
        profiler._request_stats.append(
            {"method": "GET", "path": "/x", "duration_ms": float(i), "query_count": 0}
        )
    out = profiler.get_stats()
    assert out["total_requests"] == 100
    # Mean of 1..100 = 50.5:
    assert out["avg_ms"] == 50.5
    # p95 = sorted[int(100*0.95)] = sorted[95] = 96 (1-indexed):
    assert out["p95_ms"] == 96.0
    # p99 = sorted[int(100*0.99)] = sorted[99] = 100:
    assert out["p99_ms"] == 100.0


def test_get_stats_counts_slow_requests():
    """slow_count = number of durations above slow_threshold_ms."""
    profiler = ResponseProfiler(app=MagicMock(), slow_threshold_ms=100)
    for d in [50, 200, 300, 80, 150]:  # 3 over 100ms
        profiler._request_stats.append(
            {"method": "GET", "path": "/x", "duration_ms": float(d), "query_count": 0}
        )
    out = profiler.get_stats()
    assert out["slow_count"] == 3
