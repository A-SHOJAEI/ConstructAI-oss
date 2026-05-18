"""Prometheus custom metrics for ConstructAI."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Lazy metric initialization to avoid import errors
_metrics_initialized = False
_metrics: dict = {}


def _init_metrics():
    """Initialize Prometheus metrics (lazy)."""
    global _metrics_initialized, _metrics
    if _metrics_initialized:
        return
    try:
        from prometheus_client import Counter, Gauge, Histogram

        _metrics["agent_latency"] = Histogram(
            "constructai_agent_latency_seconds",
            "Agent response time",
            ["agent_name", "action"],
        )
        _metrics["agent_errors"] = Counter(
            "constructai_agent_errors_total",
            "Agent error count",
            ["agent_name", "error_type"],
        )
        _metrics["inference_latency"] = Histogram(
            "constructai_inference_latency_seconds",
            "ML inference time",
            ["model_name"],
        )
        _metrics["kafka_consumer_lag"] = Gauge(
            "constructai_kafka_consumer_lag",
            "Kafka consumer lag",
            ["topic", "consumer_group"],
        )
        _metrics["active_camera_streams"] = Gauge(
            "constructai_active_camera_streams",
            "Active video streams",
        )
        _metrics["http_requests"] = Counter(
            "constructai_http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status"],
        )
        _metrics["active_tenants"] = Gauge(
            "constructai_active_tenants",
            "Number of active tenants",
        )
        _metrics_initialized = True
        logger.info("Prometheus metrics initialized")
    except ImportError:
        logger.warning("prometheus_client not available")


def record_agent_latency(agent_name: str, action: str, duration: float):
    """Record agent response latency."""
    _init_metrics()
    metric = _metrics.get("agent_latency")
    if metric:
        metric.labels(agent_name=agent_name, action=action).observe(duration)


def record_agent_error(agent_name: str, error_type: str):
    """Record agent error."""
    _init_metrics()
    metric = _metrics.get("agent_errors")
    if metric:
        metric.labels(agent_name=agent_name, error_type=error_type).inc()


def record_inference_latency(model_name: str, duration: float):
    """Record ML inference latency."""
    _init_metrics()
    metric = _metrics.get("inference_latency")
    if metric:
        metric.labels(model_name=model_name).observe(duration)


def _normalize_endpoint(path: str) -> str:
    """Normalize endpoint paths to prevent high-cardinality metric labels."""
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "{id}", path)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    return path


def record_http_request(method: str, endpoint: str, status: int):
    """Record HTTP request."""
    _init_metrics()
    metric = _metrics.get("http_requests")
    if metric:
        metric.labels(
            method=method,
            endpoint=_normalize_endpoint(endpoint),
            status=str(status),
        ).inc()


def set_active_streams(count: int):
    """Set active camera stream count."""
    _init_metrics()
    metric = _metrics.get("active_camera_streams")
    if metric:
        metric.set(count)


def set_active_tenants(count: int):
    """Set active tenant count."""
    _init_metrics()
    metric = _metrics.get("active_tenants")
    if metric:
        metric.set(count)


def get_metrics() -> dict:
    """Get dict of metric names for inspection."""
    _init_metrics()
    return {k: type(v).__name__ for k, v in _metrics.items()}
