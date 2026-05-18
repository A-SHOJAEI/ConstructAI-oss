"""Tests for the alert rule evaluator.

Pin every documented alert rule, the condition parser (``>`` and
``==``), the active-vs-history accounting (alerts trigger once, then
get cleared on the next non-triggering evaluation), and the
``clear()`` reset.
"""

from __future__ import annotations

import pytest

from app.services.observability.alerting import (
    ALERT_RULES,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    AlertManager,
)

# =========================================================================
# ALERT_RULES — pin canonical rules
# =========================================================================


def test_severity_constants():
    """Pin the canonical severity strings — refactor must not break the
    string contract that PagerDuty/Opsgenie integrations consume."""
    assert SEVERITY_CRITICAL == "critical"
    assert SEVERITY_WARNING == "warning"
    assert SEVERITY_INFO == "info"


def test_canonical_rules_present():
    """Pin the documented rule set so a refactor doesn't quietly drop
    one of the production-grade alerts."""
    rule_names = {r["name"] for r in ALERT_RULES}
    expected = {
        "api_error_rate_high",
        "inference_latency_high",
        "disk_usage_high",
        "kafka_consumer_lag",
        "camera_stream_down",
    }
    assert expected.issubset(rule_names)


def test_each_rule_has_required_fields():
    for rule in ALERT_RULES:
        for required in ("name", "description", "metric", "condition", "severity", "notify"):
            assert required in rule, f"rule {rule.get('name')} missing {required}"
        assert rule["severity"] in {SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO}
        assert isinstance(rule["notify"], list)
        assert rule["notify"]  # at least one channel


def test_critical_alerts_route_to_pagerduty():
    """Critical alerts must wake someone up — PagerDuty is the canonical
    paging channel."""
    for rule in ALERT_RULES:
        if rule["severity"] == SEVERITY_CRITICAL:
            assert "pagerduty" in rule["notify"], (
                f"critical rule {rule['name']} must route to pagerduty"
            )


# =========================================================================
# AlertManager._check_condition — parser
# =========================================================================


@pytest.fixture
def mgr() -> AlertManager:
    return AlertManager()


def test_check_condition_gt_above_threshold(mgr: AlertManager):
    assert mgr._check_condition("rate > 0.05", 0.10) is True


def test_check_condition_gt_below_threshold(mgr: AlertManager):
    assert mgr._check_condition("rate > 0.05", 0.01) is False


def test_check_condition_gt_at_threshold(mgr: AlertManager):
    """At exactly the threshold, ``>`` is False (strict greater-than)."""
    assert mgr._check_condition("rate > 0.05", 0.05) is False


def test_check_condition_eq_match(mgr: AlertManager):
    assert mgr._check_condition("value == 0", 0.0) is True


def test_check_condition_eq_mismatch(mgr: AlertManager):
    assert mgr._check_condition("value == 0", 1.0) is False


def test_check_condition_unknown_operator_returns_false(mgr: AlertManager):
    """Unknown comparator → fail-closed (no false alerts)."""
    assert mgr._check_condition("value < 0", 100.0) is False
    assert mgr._check_condition("invalid", 5.0) is False


def test_check_condition_p95_threshold_parses(mgr: AlertManager):
    """p95 > X is the canonical inference-latency condition."""
    assert mgr._check_condition("p95 > 5.0", 6.5) is True
    assert mgr._check_condition("p95 > 5.0", 4.5) is False


# =========================================================================
# AlertManager.evaluate_rules — triggering / resolution
# =========================================================================


async def test_evaluate_triggers_on_threshold_breach(mgr: AlertManager):
    """High API error rate → trigger ``api_error_rate_high``."""
    triggered = await mgr.evaluate_rules({"api_error_rate_high": 0.10})
    assert len(triggered) == 1
    alert = triggered[0]
    assert alert["rule"] == "api_error_rate_high"
    assert alert["severity"] == SEVERITY_CRITICAL
    assert "pagerduty" in alert["notify"]
    assert alert["current_value"] == 0.10


async def test_evaluate_does_not_re_emit_active_alert(mgr: AlertManager):
    """Once an alert is firing, repeated evaluations should NOT emit
    another trigger event — that would page on every metric tick."""
    await mgr.evaluate_rules({"api_error_rate_high": 0.10})
    second = await mgr.evaluate_rules({"api_error_rate_high": 0.15})
    assert second == []  # already active, no re-trigger
    assert len(mgr.get_active_alerts()) == 1


async def test_evaluate_resolves_alert_when_metric_recovers(mgr: AlertManager):
    """When the metric drops below threshold, the alert clears from
    active — pager will get a resolve."""
    await mgr.evaluate_rules({"api_error_rate_high": 0.10})
    assert len(mgr.get_active_alerts()) == 1
    # Recover:
    await mgr.evaluate_rules({"api_error_rate_high": 0.01})
    assert mgr.get_active_alerts() == []


async def test_evaluate_camera_stream_down_eq_zero(mgr: AlertManager):
    """Camera-stream-down uses ``== 0`` — pin that the eq parser works
    for the documented rule."""
    triggered = await mgr.evaluate_rules({"camera_stream_down": 0})
    assert len(triggered) == 1
    assert triggered[0]["severity"] == SEVERITY_CRITICAL


async def test_evaluate_skips_metrics_not_in_rules(mgr: AlertManager):
    """A metric that doesn't correspond to any configured rule is
    silently ignored — must not crash."""
    triggered = await mgr.evaluate_rules({"unknown_custom_metric": 999.0})
    assert triggered == []


async def test_evaluate_emits_to_history_even_if_resolved(mgr: AlertManager):
    """Active alerts get cleared on resolve, but history persists."""
    await mgr.evaluate_rules({"api_error_rate_high": 0.10})
    await mgr.evaluate_rules({"api_error_rate_high": 0.01})
    assert mgr.get_active_alerts() == []
    history = mgr.get_alert_history()
    assert len(history) == 1
    assert history[0]["rule"] == "api_error_rate_high"


async def test_evaluate_multiple_rules_all_triggered(mgr: AlertManager):
    """Two different metrics breaching at once → two triggers."""
    triggered = await mgr.evaluate_rules(
        {
            "api_error_rate_high": 0.10,
            "kafka_consumer_lag": 5000,
        }
    )
    rules_fired = {a["rule"] for a in triggered}
    assert rules_fired == {"api_error_rate_high", "kafka_consumer_lag"}


# =========================================================================
# AlertManager — get_active_alerts / get_alert_history / clear
# =========================================================================


def test_get_active_alerts_returns_copy(mgr: AlertManager):
    """get_active_alerts() must return a list copy — caller mutation
    must not affect internal state."""
    mgr._active_alerts["x"] = {"rule": "x"}
    active = mgr.get_active_alerts()
    active.clear()
    # Internal state preserved:
    assert "x" in mgr._active_alerts


def test_get_alert_history_returns_copy(mgr: AlertManager):
    mgr._alert_history.append({"rule": "x"})
    hist = mgr.get_alert_history()
    hist.clear()
    assert mgr._alert_history  # untouched


async def test_clear_resets_all_state(mgr: AlertManager):
    await mgr.evaluate_rules({"api_error_rate_high": 0.10})
    assert mgr.get_active_alerts()
    assert mgr.get_alert_history()
    mgr.clear()
    assert mgr.get_active_alerts() == []
    assert mgr.get_alert_history() == []
