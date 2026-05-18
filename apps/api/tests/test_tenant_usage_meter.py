"""Tests for the per-tenant billing usage meter.

Distinct from ``app/services/usage/meter.py`` (platform-wide events) —
this one aggregates per-org metrics against plan limits and is used by
the billing flow to gate access when a tenant runs over.
"""

from __future__ import annotations

import pytest

from app.services.tenant.usage_meter import UsageMeter


@pytest.fixture
def meter() -> UsageMeter:
    return UsageMeter()


# ---- record / get_usage --------------------------------------------------


async def test_record_accumulates_per_org_per_metric(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 1)
    await meter.record("org-1", "api_calls", 2)
    await meter.record("org-1", "storage_bytes", 1024)
    usage = await meter.get_usage("org-1")
    assert usage == {"api_calls": 3.0, "storage_bytes": 1024.0}


async def test_record_keeps_orgs_separate(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 5)
    await meter.record("org-2", "api_calls", 7)
    assert (await meter.get_usage("org-1"))["api_calls"] == 5.0
    assert (await meter.get_usage("org-2"))["api_calls"] == 7.0


async def test_record_rejects_unknown_metric(meter: UsageMeter):
    with pytest.raises(ValueError, match="Invalid metric type: bogus"):
        await meter.record("org-1", "bogus", 1)


async def test_record_accepts_every_documented_metric(meter: UsageMeter):
    """All METRIC_TYPES must round-trip — protects against typos in the
    set vs production callers."""
    for metric in UsageMeter.METRIC_TYPES:
        await meter.record("org-1", metric, 1)
    usage = await meter.get_usage("org-1")
    assert set(usage.keys()) == UsageMeter.METRIC_TYPES


async def test_get_usage_unknown_org_returns_empty(meter: UsageMeter):
    assert await meter.get_usage("never-seen") == {}


async def test_get_usage_returns_a_copy(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 1)
    usage = await meter.get_usage("org-1")
    usage["api_calls"] = 9_999
    # Modifying the snapshot must not corrupt the meter's own state.
    assert (await meter.get_usage("org-1"))["api_calls"] == 1.0


# ---- check_limit --------------------------------------------------------


async def test_check_limit_under_returns_within(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 1_000)
    within, pct = await meter.check_limit("org-1", "api_calls", "startup")
    assert within is True
    assert pct == 10.0  # 1000 / 10000


async def test_check_limit_at_threshold_is_within(meter: UsageMeter):
    """Exactly at the limit must NOT be flagged as exceeded — that's
    the typical billing-counter rounding semantic."""
    await meter.record("org-1", "api_calls", 10_000)
    within, pct = await meter.check_limit("org-1", "api_calls", "startup")
    assert within is True
    assert pct == 100.0


async def test_check_limit_over_returns_not_within(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 10_001)
    within, pct = await meter.check_limit("org-1", "api_calls", "startup")
    assert within is False
    assert pct > 100.0


async def test_check_limit_enterprise_is_unlimited(meter: UsageMeter):
    """Enterprise stores -1 to mean ``no cap``."""
    await meter.record("org-1", "api_calls", 10**9)
    within, pct = await meter.check_limit("org-1", "api_calls", "enterprise")
    assert within is True
    assert pct == 0.0  # unlimited reports 0% used


async def test_check_limit_unknown_plan_treats_as_unlimited(meter: UsageMeter):
    """Defensive: an unrecognized plan name shouldn't accidentally lock a
    tenant out — fall back to "no cap" instead. Falsy/missing limit = -1."""
    await meter.record("org-1", "api_calls", 999_999)
    within, pct = await meter.check_limit("org-1", "api_calls", "made-up-plan")
    assert within is True
    assert pct == 0.0


async def test_check_limit_no_recorded_usage_returns_zero_pct(meter: UsageMeter):
    within, pct = await meter.check_limit("org-1", "api_calls", "startup")
    assert within is True
    assert pct == 0.0


async def test_check_limit_growth_plan_uses_growth_caps(meter: UsageMeter):
    await meter.record("org-1", "api_calls", 50_000)
    within, pct = await meter.check_limit("org-1", "api_calls", "growth")
    assert within is True
    assert pct == 50.0


async def test_check_limit_storage_thresholds_per_plan(meter: UsageMeter):
    """Storage limit goes from 10 GB (startup) → 100 GB (growth) →
    unlimited (enterprise). Quick spot-check that the right cap is used
    for each plan name."""
    half_startup_cap = 5 * 1024**3  # 5 GB
    await meter.record("org-1", "storage_bytes", half_startup_cap)
    s_within, s_pct = await meter.check_limit("org-1", "storage_bytes", "startup")
    g_within, g_pct = await meter.check_limit("org-1", "storage_bytes", "growth")
    assert (s_within, s_pct) == (True, 50.0)
    assert g_within is True
    assert g_pct == 5.0  # same usage, 10× the cap → 5% used
