from __future__ import annotations

from app.services.observability.alerting import ALERT_RULES, AlertManager
from app.services.observability.metrics import (
    get_metrics,
    record_agent_error,
    record_agent_latency,
)
from app.services.observability.structured_logger import (
    bind_correlation_id,
    clear_context,
    get_logger,
    setup_logging,
)
from app.services.observability.tracing import TracingSetup


class TestStructuredLogging:
    def test_setup_logging(self):
        setup_logging("INFO")  # Should not raise

    def test_get_logger(self):
        log = get_logger("test")
        assert log is not None

    def test_bind_and_clear_context(self):
        bind_correlation_id("test-123")
        clear_context()  # Should not raise


class TestTracing:
    def test_tracing_init(self):
        tracing = TracingSetup(service_name="test-service")
        assert tracing.service_name == "test-service"
        assert tracing.initialized is False


class TestMetrics:
    def test_get_metrics(self):
        metrics = get_metrics()
        assert isinstance(metrics, dict)

    def test_record_agent_latency(self):
        record_agent_latency("test_agent", "infer", 0.5)

    def test_record_agent_error(self):
        record_agent_error("test_agent", "timeout")


class TestAlerting:
    def test_alert_rules_defined(self):
        assert len(ALERT_RULES) >= 5

    async def test_evaluate_no_alerts(self):
        mgr = AlertManager()
        triggered = await mgr.evaluate_rules({})
        assert len(triggered) == 0

    async def test_alert_triggered(self):
        mgr = AlertManager()
        triggered = await mgr.evaluate_rules(
            {
                "api_error_rate_high": 0.10,
            }
        )
        assert len(triggered) == 1
        assert triggered[0]["severity"] == "critical"
