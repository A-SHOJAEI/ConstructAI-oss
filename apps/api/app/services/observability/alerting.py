"""Alert rule definitions for PagerDuty/Opsgenie integration."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Alert severity levels
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

ALERT_RULES: list[dict[str, Any]] = [
    {
        "name": "api_error_rate_high",
        "description": "API error rate exceeds 5%",
        "metric": "constructai_agent_errors_total",
        "condition": "rate > 0.05",
        "severity": SEVERITY_CRITICAL,
        "notify": ["pagerduty"],
    },
    {
        "name": "inference_latency_high",
        "description": "ML inference P95 exceeds 5 seconds",
        "metric": "constructai_inference_latency_seconds",
        "condition": "p95 > 5.0",
        "severity": SEVERITY_WARNING,
        "notify": ["opsgenie"],
    },
    {
        "name": "disk_usage_high",
        "description": "Disk usage exceeds 80%",
        "metric": "node_filesystem_avail_bytes",
        "condition": "usage_percent > 80",
        "severity": SEVERITY_WARNING,
        "notify": ["opsgenie"],
    },
    {
        "name": "kafka_consumer_lag",
        "description": "Kafka consumer lag exceeds 1000",
        "metric": "constructai_kafka_consumer_lag",
        "condition": "lag > 1000",
        "severity": SEVERITY_WARNING,
        "notify": ["opsgenie"],
    },
    {
        "name": "camera_stream_down",
        "description": ("Active camera streams dropped to zero"),
        "metric": "constructai_active_camera_streams",
        "condition": "value == 0",
        "severity": SEVERITY_CRITICAL,
        "notify": ["pagerduty"],
    },
]


class AlertManager:
    """Evaluate alert rules and send notifications."""

    def __init__(self):
        self._active_alerts: dict[str, dict] = {}
        self._alert_history: list[dict] = []

    async def evaluate_rules(self, current_metrics: dict) -> list[dict]:
        """Evaluate all alert rules against current metrics.

        Returns list of triggered alerts.
        """
        triggered = []
        for rule in ALERT_RULES:
            name = rule["name"]
            # Simple threshold checking placeholder
            if name in current_metrics:
                value = current_metrics[name]
                is_triggered = self._check_condition(
                    rule["condition"],
                    value,
                )
                if is_triggered and name not in self._active_alerts:
                    alert = {
                        "rule": name,
                        "description": rule["description"],
                        "severity": rule["severity"],
                        "current_value": value,
                        "notify": rule["notify"],
                    }
                    self._active_alerts[name] = alert
                    self._alert_history.append(alert)
                    triggered.append(alert)
                    logger.warning("Alert triggered: %s", name)
                elif not is_triggered and name in self._active_alerts:
                    del self._active_alerts[name]
                    logger.info("Alert resolved: %s", name)
        return triggered

    def _check_condition(self, condition: str, value: float) -> bool:
        """Simple condition evaluation (placeholder)."""
        if "> " in condition:
            threshold = float(condition.split("> ")[1])
            return value > threshold
        if "== " in condition:
            threshold = float(condition.split("== ")[1])
            return value == threshold
        return False

    def get_active_alerts(self) -> list[dict]:
        """Get all currently active alerts."""
        return list(self._active_alerts.values())

    def get_alert_history(self) -> list[dict]:
        """Get alert history."""
        return list(self._alert_history)

    def clear(self):
        """Clear all alerts (for testing)."""
        self._active_alerts.clear()
        self._alert_history.clear()
