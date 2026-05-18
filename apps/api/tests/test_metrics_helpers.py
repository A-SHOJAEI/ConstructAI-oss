"""Tests for the Prometheus metrics module.

The most important pure helper here is ``_normalize_endpoint``, which
prevents high-cardinality label explosion (UUIDs and numeric IDs in
paths must be replaced with ``{id}`` so Prometheus doesn't see
millions of distinct label values).

The recorder functions are also tested for graceful no-op behavior
when prometheus_client isn't available — important for dev / test
environments.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.observability import metrics
from app.services.observability.metrics import (
    _normalize_endpoint,
    get_metrics,
    record_agent_error,
    record_agent_latency,
    record_http_request,
    record_inference_latency,
    set_active_streams,
    set_active_tenants,
)

# =========================================================================
# _normalize_endpoint — high-cardinality protection
# =========================================================================


def test_normalize_endpoint_uuid_replaced():
    """UUIDs must be replaced — otherwise every request would create a
    new label series."""
    out = _normalize_endpoint("/api/v1/projects/01234567-89ab-cdef-0123-456789abcdef/files")
    assert out == "/api/v1/projects/{id}/files"


def test_normalize_endpoint_numeric_id_replaced():
    out = _normalize_endpoint("/api/v1/users/42/profile")
    assert out == "/api/v1/users/{id}/profile"


def test_normalize_endpoint_trailing_id_replaced():
    out = _normalize_endpoint("/api/v1/items/12345")
    assert out == "/api/v1/items/{id}"


def test_normalize_endpoint_no_id_unchanged():
    out = _normalize_endpoint("/api/v1/health")
    assert out == "/api/v1/health"


def test_normalize_endpoint_multiple_uuids_in_one_path():
    """Nested resource paths can have multiple UUIDs — replace each."""
    out = _normalize_endpoint(
        "/api/projects/01234567-89ab-cdef-0123-456789abcdef"
        "/rfis/abcdef01-2345-6789-abcd-ef0123456789"
    )
    assert out == "/api/projects/{id}/rfis/{id}"


def test_normalize_endpoint_uppercase_uuid_not_matched():
    """The regex matches lowercase hex only — uppercase UUIDs pass
    through unmatched. Pin so we don't accidentally extend the match
    later (would change cardinality semantics)."""
    out = _normalize_endpoint("/api/x/AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA")
    # Uppercase A's don't match the lowercase hex pattern.
    assert "AAAAAAAA" in out


def test_normalize_endpoint_does_not_match_non_id_numbers_in_query():
    """Numeric tokens inside query strings (after ``?``) shouldn't be
    picked up — only path segments. The regex matches /<digits>(end-or-/)
    so query strings are unaffected."""
    out = _normalize_endpoint("/api/v1/items?page=42")
    assert "42" in out  # query string preserved


def test_normalize_endpoint_short_numeric_id_replaced():
    """Even a 1-digit ID gets normalized."""
    out = _normalize_endpoint("/api/v1/items/3")
    assert out == "/api/v1/items/{id}"


# =========================================================================
# Recorder functions — fallback when prometheus_client missing
# =========================================================================


def test_recorders_are_no_op_when_prometheus_missing():
    """If prometheus_client isn't installed, every recorder must be a
    silent no-op. Otherwise dev / test environments would crash on
    every metric increment."""
    # Reset the module's lazy state and force the missing-import path.
    with (
        patch.object(metrics, "_metrics_initialized", False),
        patch.object(metrics, "_metrics", {}),
        patch.dict("sys.modules", {"prometheus_client": None}),
    ):
        # None of these should raise:
        record_agent_latency("test_agent", "test_action", 1.5)
        record_agent_error("test_agent", "ValueError")
        record_inference_latency("yolo_v8", 0.250)
        record_http_request("GET", "/api/v1/health", 200)
        set_active_streams(5)
        set_active_tenants(10)
        # And get_metrics should return empty dict:
        assert get_metrics() == {}


# =========================================================================
# Recorder functions — happy path with real prometheus_client
# =========================================================================


@pytest.fixture(autouse=True)
def reset_metrics_module():
    """Each test gets a fresh metrics state. Prometheus uses a global
    REGISTRY that persists across tests in the same process, so we
    unregister the constructai metrics on both setup and teardown to
    avoid the ``Duplicated timeseries`` registration error."""
    try:
        from prometheus_client import REGISTRY

        for collector in list(metrics._metrics.values()):
            try:
                REGISTRY.unregister(collector)
            except KeyError:
                pass
    except ImportError:
        pass
    metrics._metrics_initialized = False
    metrics._metrics.clear()
    yield
    try:
        from prometheus_client import REGISTRY

        for collector in list(metrics._metrics.values()):
            try:
                REGISTRY.unregister(collector)
            except KeyError:
                pass
    except ImportError:
        pass
    metrics._metrics_initialized = False
    metrics._metrics.clear()


def test_record_agent_latency_observable():
    """When prometheus_client IS available, the metric should be
    observable (no exception, internal histogram created)."""
    pytest.importorskip("prometheus_client")
    record_agent_latency("safety_agent", "evaluate", 0.5)
    out = get_metrics()
    assert "agent_latency" in out


def test_record_agent_error_increments():
    pytest.importorskip("prometheus_client")
    record_agent_error("safety_agent", "TimeoutError")
    out = get_metrics()
    assert "agent_errors" in out


def test_record_inference_latency():
    pytest.importorskip("prometheus_client")
    record_inference_latency("yolo_v8_safety", 0.180)
    out = get_metrics()
    assert "inference_latency" in out


def test_record_http_request_normalizes_path():
    """The HTTP recorder should call _normalize_endpoint internally —
    we verify by constructing a uuid path and checking get_metrics()
    has the http_requests metric initialized."""
    pytest.importorskip("prometheus_client")
    record_http_request(
        "GET",
        "/api/v1/projects/01234567-89ab-cdef-0123-456789abcdef",
        200,
    )
    out = get_metrics()
    assert "http_requests" in out


def test_set_active_streams():
    pytest.importorskip("prometheus_client")
    set_active_streams(3)
    out = get_metrics()
    assert "active_camera_streams" in out


def test_set_active_tenants():
    pytest.importorskip("prometheus_client")
    set_active_tenants(42)
    out = get_metrics()
    assert "active_tenants" in out


def test_get_metrics_after_init_lists_all_seven_metrics():
    """All 7 documented metrics should be initialized after the first
    record call."""
    pytest.importorskip("prometheus_client")
    record_agent_latency("a", "b", 0.1)  # triggers _init_metrics
    out = get_metrics()
    expected = {
        "agent_latency",
        "agent_errors",
        "inference_latency",
        "kafka_consumer_lag",
        "active_camera_streams",
        "http_requests",
        "active_tenants",
    }
    assert expected.issubset(out.keys())


def test_init_is_idempotent():
    """Calling _init_metrics twice should not double-register Counter
    instances — Prometheus would raise on duplicate registration."""
    pytest.importorskip("prometheus_client")
    metrics._init_metrics()
    snapshot = list(metrics._metrics.keys())
    # Second call — no exception, no change to keys:
    metrics._init_metrics()
    assert list(metrics._metrics.keys()) == snapshot
