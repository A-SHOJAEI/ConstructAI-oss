"""Structured JSON logging with correlation IDs using structlog."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

# Fields whose values must be redacted in log output
_SENSITIVE_KEYS_RE = re.compile(
    r"(password|passwd|token|secret|authorization|cookie|api_key|access_key|private_key)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


def _sanitize_log_event(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that redacts sensitive values from log entries."""
    for key in list(event_dict.keys()):
        if _SENSITIVE_KEYS_RE.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def setup_logging(log_level: str = "INFO"):
    """Configure structlog with JSON output and correlation IDs."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _sanitize_log_event,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "constructai"):
    """Get a structured logger instance."""
    return structlog.get_logger(name)


def bind_correlation_id(correlation_id: str):
    """Bind a correlation ID to the current context."""
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
    )


def bind_tenant_context(org_id: str, user_id: str = ""):
    """Bind tenant context to current log context."""
    structlog.contextvars.bind_contextvars(
        org_id=org_id,
        user_id=user_id,
    )


def clear_context():
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
