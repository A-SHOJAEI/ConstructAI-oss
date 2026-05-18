"""Tests for the beta-program support services.

Covers:
- FeatureFlagService — registration, evaluation, rollout%, tenant overrides.
- ABTestingFramework — experiment creation, deterministic variant assignment,
  result aggregation.
- FeedbackCollector — thumbs-up/down validation, per-agent summary.
- UsageAnalytics — event tracking, feature-adoption aggregation.

All four are pure in-memory data structures, so the tests run without
DB or Redis.
"""

from __future__ import annotations

import pytest

from app.services.beta.ab_testing import ABTestingFramework
from app.services.beta.feature_flags import FeatureFlagService
from app.services.beta.feedback_collector import FeedbackCollector
from app.services.beta.usage_analytics import UsageAnalytics

# =========================================================================
# FeatureFlagService
# =========================================================================


def test_feature_flag_unregistered_returns_false():
    svc = FeatureFlagService()
    assert svc.is_enabled("ghost-flag") is False


def test_feature_flag_disabled_returns_false():
    svc = FeatureFlagService()
    svc.register_flag("dark_mode", enabled=False, rollout_percentage=100)
    assert svc.is_enabled("dark_mode") is False


def test_feature_flag_full_rollout_returns_true():
    svc = FeatureFlagService()
    svc.register_flag("new_dashboard", enabled=True, rollout_percentage=100)
    assert svc.is_enabled("new_dashboard", "org-1", "user-1") is True


def test_feature_flag_zero_rollout_returns_false_for_everyone():
    svc = FeatureFlagService()
    svc.register_flag("new_dashboard", enabled=True, rollout_percentage=0)
    # 0% rollout = no user gets it.
    for i in range(20):
        assert svc.is_enabled("new_dashboard", f"org-{i}", f"user-{i}") is False


def test_feature_flag_partial_rollout_is_deterministic_per_user():
    """Same (flag, org, user) inputs must always produce the same
    decision — otherwise A/B numbers can't be trusted."""
    svc = FeatureFlagService()
    svc.register_flag("partial", enabled=True, rollout_percentage=50)
    a = svc.is_enabled("partial", "org-x", "user-y")
    b = svc.is_enabled("partial", "org-x", "user-y")
    assert a == b


def test_feature_flag_tenant_override_takes_precedence():
    """A tenant override beats the rollout percentage — used to give
    pilot customers early access regardless of bucketing."""
    svc = FeatureFlagService()
    svc.register_flag(
        "preview",
        enabled=True,
        rollout_percentage=0,  # nobody by rollout
        tenant_overrides={"pilot-org": True},
    )
    assert svc.is_enabled("preview", "pilot-org") is True
    assert svc.is_enabled("preview", "other-org") is False


def test_feature_flag_tenant_override_can_disable():
    """Override with False locks a tenant out even if the flag is
    fully rolled out."""
    svc = FeatureFlagService()
    svc.register_flag(
        "feature_x",
        enabled=True,
        rollout_percentage=100,
        tenant_overrides={"opt-out-org": False},
    )
    assert svc.is_enabled("feature_x", "opt-out-org") is False
    assert svc.is_enabled("feature_x", "other-org") is True


def test_get_all_flags_returns_copy():
    svc = FeatureFlagService()
    svc.register_flag("a", enabled=True)
    snap = svc.get_all_flags()
    snap.clear()  # caller-side mutation should not nuke the registry
    assert "a" in svc.get_all_flags()


def test_update_flag_changes_evaluation():
    svc = FeatureFlagService()
    svc.register_flag("toggle", enabled=False, rollout_percentage=100)
    assert svc.is_enabled("toggle", "u") is False
    svc.update_flag("toggle", enabled=True)
    assert svc.is_enabled("toggle", "u") is True


def test_update_flag_unknown_raises():
    svc = FeatureFlagService()
    with pytest.raises(ValueError, match="not found"):
        svc.update_flag("nonexistent", enabled=True)


# =========================================================================
# ABTestingFramework
# =========================================================================


async def test_create_experiment_with_default_split():
    framework = ABTestingFramework()
    exp = await framework.create_experiment("color_test", ["red", "blue"])
    assert exp["traffic_split"] == [50, 50]
    assert exp["status"] == "active"


async def test_create_experiment_rejects_single_variant():
    framework = ABTestingFramework()
    with pytest.raises(ValueError, match="at least 2 variants"):
        await framework.create_experiment("bad", ["only"])


async def test_create_experiment_rejects_empty_variants():
    framework = ABTestingFramework()
    with pytest.raises(ValueError, match="at least 2 variants"):
        await framework.create_experiment("bad", [])


async def test_create_experiment_with_custom_split():
    framework = ABTestingFramework()
    exp = await framework.create_experiment("uneven", ["a", "b"], traffic_split=[80, 20])
    assert exp["traffic_split"] == [80, 20]


async def test_assign_variant_is_deterministic():
    framework = ABTestingFramework()
    await framework.create_experiment("t", ["a", "b"])
    a = framework.assign_variant("t", "user-1")
    b = framework.assign_variant("t", "user-1")
    assert a == b


async def test_assign_variant_unknown_experiment_returns_control():
    framework = ABTestingFramework()
    assert framework.assign_variant("ghost", "user-1") == "control"


async def test_assign_variant_skewed_split_respects_weights():
    """When the split is heavily skewed, a large user sample should land
    mostly in the high-weight variant."""
    framework = ABTestingFramework()
    await framework.create_experiment("skew", ["big", "small"], traffic_split=[90, 10])
    counts = {"big": 0, "small": 0}
    for i in range(500):
        counts[framework.assign_variant("skew", f"user-{i}")] += 1
    # With 90/10, expect big > 4× small in a 500-user sample.
    assert counts["big"] > counts["small"] * 4


async def test_record_and_get_results():
    framework = ABTestingFramework()
    await framework.create_experiment("perf", ["fast", "slow"])
    await framework.record_result("perf", "fast", "latency_ms", 100.0)
    await framework.record_result("perf", "fast", "latency_ms", 110.0)
    await framework.record_result("perf", "slow", "latency_ms", 200.0)
    res = await framework.get_results("perf")
    assert res["variants"]["fast"]["count"] == 2
    assert res["variants"]["fast"]["mean"] == 105.0
    assert res["variants"]["slow"]["count"] == 1
    assert res["variants"]["slow"]["mean"] == 200.0


async def test_get_results_for_experiment_with_no_data():
    framework = ABTestingFramework()
    await framework.create_experiment("empty", ["a", "b"])
    res = await framework.get_results("empty")
    assert res == {"experiment": "empty", "variants": {}}


# =========================================================================
# FeedbackCollector
# =========================================================================


async def test_feedback_thumbs_up():
    fc = FeedbackCollector()
    entry = await fc.collect("u1", "doc_agent", rating=1, feedback_text="great!")
    assert entry["rating"] == 1
    assert entry["feedback_text"] == "great!"
    assert entry["agent_name"] == "doc_agent"
    # Auto-generated id:
    assert entry["id"] and entry["id"] != "u1"


async def test_feedback_thumbs_down():
    fc = FeedbackCollector()
    entry = await fc.collect("u1", "rfi_agent", rating=-1)
    assert entry["rating"] == -1


async def test_feedback_invalid_rating_rejected():
    fc = FeedbackCollector()
    for bad in (0, 2, -2, 5, 100):
        with pytest.raises(ValueError, match="must be 1 or -1"):
            await fc.collect("u1", "agent", rating=bad)


async def test_feedback_summary_aggregates_per_agent():
    fc = FeedbackCollector()
    await fc.collect("u1", "doc_agent", rating=1)
    await fc.collect("u2", "doc_agent", rating=1)
    await fc.collect("u3", "doc_agent", rating=-1)
    await fc.collect("u4", "rfi_agent", rating=-1)
    summary = await fc.get_summary()
    by_name = {row["agent_name"]: row for row in summary}
    assert by_name["doc_agent"]["total_ratings"] == 3
    assert by_name["doc_agent"]["positive_count"] == 2
    assert by_name["doc_agent"]["negative_count"] == 1
    assert by_name["doc_agent"]["approval_rate"] == round(2 / 3, 3)
    assert by_name["rfi_agent"]["approval_rate"] == 0.0


async def test_feedback_summary_filtered_by_agent():
    fc = FeedbackCollector()
    await fc.collect("u1", "doc_agent", rating=1)
    await fc.collect("u2", "rfi_agent", rating=-1)
    summary = await fc.get_summary(agent_name="doc_agent")
    assert len(summary) == 1
    assert summary[0]["agent_name"] == "doc_agent"


async def test_feedback_summary_no_entries_returns_empty():
    fc = FeedbackCollector()
    assert await fc.get_summary() == []


async def test_get_feedback_returns_recent_entries_with_limit():
    fc = FeedbackCollector()
    for i in range(5):
        await fc.collect(f"u{i}", "agent", rating=1)
    items = await fc.get_feedback(limit=3)
    # Latest 3:
    assert [e["user_id"] for e in items] == ["u2", "u3", "u4"]


async def test_get_feedback_can_filter_by_agent():
    fc = FeedbackCollector()
    await fc.collect("u1", "doc_agent", rating=1)
    await fc.collect("u2", "rfi_agent", rating=-1)
    items = await fc.get_feedback(agent_name="rfi_agent")
    assert len(items) == 1
    assert items[0]["user_id"] == "u2"


# =========================================================================
# UsageAnalytics
# =========================================================================


async def test_usage_track_records_event():
    analytics = UsageAnalytics()
    await analytics.track("doc_uploaded", user_id="u1", org_id="o1")
    items = await analytics.get_events()
    assert len(items) == 1
    assert items[0]["event"] == "doc_uploaded"
    assert items[0]["user_id"] == "u1"


async def test_usage_track_uses_empty_dict_for_default_properties():
    analytics = UsageAnalytics()
    await analytics.track("evt")
    items = await analytics.get_events()
    assert items[0]["properties"] == {}


async def test_usage_get_feature_adoption_counts_unique_users():
    analytics = UsageAnalytics()
    await analytics.track("doc_uploaded", user_id="u1")
    await analytics.track("doc_uploaded", user_id="u1")  # same user, same event
    await analytics.track("doc_uploaded", user_id="u2")
    await analytics.track("rfi_created", user_id="u1")
    adoption = await analytics.get_feature_adoption()
    assert adoption["doc_uploaded"]["unique_users"] == 2
    assert adoption["doc_uploaded"]["total_events"] == 3
    assert adoption["rfi_created"]["unique_users"] == 1


async def test_usage_get_feature_adoption_ignores_anonymous():
    analytics = UsageAnalytics()
    await analytics.track("evt")  # no user_id
    adoption = await analytics.get_feature_adoption()
    # Anonymous events still increment total but produce 0 unique_users.
    assert adoption["evt"]["unique_users"] == 0
    assert adoption["evt"]["total_events"] == 1


async def test_usage_get_events_filtered_and_limited():
    analytics = UsageAnalytics()
    for i in range(10):
        await analytics.track("a", user_id=f"u{i}")
    for i in range(5):
        await analytics.track("b", user_id=f"u{i}")
    items = await analytics.get_events(event_name="b", limit=3)
    assert len(items) == 3
    assert all(e["event"] == "b" for e in items)


async def test_usage_clear_drops_all_events():
    analytics = UsageAnalytics()
    await analytics.track("a")
    await analytics.track("b")
    analytics.clear()
    assert await analytics.get_events() == []
