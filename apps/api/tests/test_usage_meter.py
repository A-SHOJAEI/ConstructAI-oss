"""Tests for the in-process usage meter (api calls, AI inferences,
storage, documents). Redis is patched out so each test runs against
the in-memory fallback only — that keeps each test independent of
container state and exercises the real fall-back code path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services.usage.meter import (
    UsageMeter,
    UsageRecord,
    UsageSummary,
    get_usage_meter,
)


@pytest.fixture
def meter():
    """Fresh meter, with Redis lookup forced to None so the in-memory
    fallback runs. Production paths still write through to Redis when it
    is reachable; production-grade test isolation just doesn't need it."""
    with patch("app.services.usage.meter._get_redis", new=AsyncMock(return_value=None)):
        yield UsageMeter()


# ---- UsageRecord ---------------------------------------------------------


def test_usage_record_auto_timestamps_when_omitted():
    rec = UsageRecord(org_id="o1", metric="api_call", value=1.0)
    # ISO 8601 UTC; the value should be parseable and within the last
    # 10 seconds — protects against e.g. UTC offset bugs.
    parsed = datetime.fromisoformat(rec.recorded_at)
    assert parsed.tzinfo is not None
    delta = abs((datetime.now(UTC) - parsed).total_seconds())
    assert delta < 10


def test_usage_record_keeps_explicit_timestamp():
    rec = UsageRecord(
        org_id="o1",
        metric="api_call",
        value=1.0,
        recorded_at="2026-01-15T12:00:00+00:00",
    )
    assert rec.recorded_at == "2026-01-15T12:00:00+00:00"


def test_usage_record_metadata_default_is_empty_dict():
    a = UsageRecord(org_id="o1", metric="m", value=1)
    b = UsageRecord(org_id="o2", metric="m", value=1)
    a.metadata["k"] = "v"
    # Each instance must have its own dict — using `field(default=...)``
    # would alias them and the second record would inherit "k".
    assert b.metadata == {}


# ---- record / record_* helpers -------------------------------------------


async def test_record_appends_to_in_memory_list(meter: UsageMeter):
    await meter.record("org-1", "api_call", value=2.0, metadata={"x": 1})
    assert len(meter._records) == 1
    rec = meter._records[0]
    assert rec.org_id == "org-1"
    assert rec.metric == "api_call"
    assert rec.value == 2.0
    assert rec.metadata == {"x": 1}


async def test_record_api_call_sets_endpoint_metadata(meter: UsageMeter):
    await meter.record_api_call("org-1", "/v1/projects")
    rec = meter._records[0]
    assert rec.metric == "api_call"
    assert rec.metadata == {"endpoint": "/v1/projects"}


async def test_record_ai_inference_sets_model_and_latency(meter: UsageMeter):
    await meter.record_ai_inference("org-1", "claude-sonnet-4", 312.5)
    rec = meter._records[0]
    assert rec.metric == "ai_inference"
    assert rec.metadata == {"model": "claude-sonnet-4", "latency_ms": 312.5}


async def test_record_storage_uses_value_field(meter: UsageMeter):
    await meter.record_storage("org-1", 4096)
    rec = meter._records[0]
    assert rec.metric == "storage_bytes"
    assert rec.value == 4096.0


async def test_record_document_processed_carries_type(meter: UsageMeter):
    await meter.record_document_processed("org-1", "spec")
    rec = meter._records[0]
    assert rec.metric == "document_processed"
    assert rec.metadata == {"type": "spec"}


async def test_record_evicts_oldest_when_buffer_full(meter: UsageMeter):
    """The buffer caps at _MAX_RECORDS; on overflow it drops the oldest
    _TRIM_SIZE entries instead of growing without bound."""
    from app.services.usage import meter as meter_mod

    # Shrink limits for the test so we don't allocate 50k records.
    with (
        patch.object(meter_mod, "_MAX_RECORDS", 5),
        patch.object(meter_mod, "_TRIM_SIZE", 2),
    ):
        for i in range(5):
            await meter.record("org", "api_call", metadata={"i": i})
        assert len(meter._records) == 5
        # 6th triggers the eviction path:
        await meter.record("org", "api_call", metadata={"i": 5})
    assert len(meter._records) == 4
    # The first two (i=0, i=1) should be gone; remaining tail starts at i=2.
    assert meter._records[0].metadata["i"] == 2
    assert meter._records[-1].metadata["i"] == 5


# ---- get_summary --------------------------------------------------------


async def test_summary_groups_by_metric(meter: UsageMeter):
    await meter.record_api_call("org-1", "/a")
    await meter.record_api_call("org-1", "/b")
    await meter.record_ai_inference("org-1", "m", 10.0)
    await meter.record_storage("org-1", 1024)
    await meter.record_document_processed("org-1", "drawing")

    summary = await meter.get_summary("org-1")
    assert isinstance(summary, UsageSummary)
    assert summary.api_calls == 2
    assert summary.ai_inferences == 1
    assert summary.storage_bytes == 1024
    assert summary.documents_processed == 1


async def test_summary_filters_by_org(meter: UsageMeter):
    await meter.record_api_call("org-1", "/a")
    await meter.record_api_call("org-2", "/a")
    summary_1 = await meter.get_summary("org-1")
    summary_2 = await meter.get_summary("org-2")
    assert summary_1.api_calls == 1
    assert summary_2.api_calls == 1


async def test_summary_default_period_is_month_to_today(meter: UsageMeter):
    summary = await meter.get_summary("org-1")
    assert summary.period_start == date.today().replace(day=1).isoformat()
    assert summary.period_end == date.today().isoformat()


async def test_summary_filters_by_period(meter: UsageMeter):
    today = date.today()
    yesterday = today - timedelta(days=1)
    older = today - timedelta(days=10)

    # Inject records with explicit timestamps spanning the boundary.
    meter._records = [
        UsageRecord("org-1", "api_call", 1.0, recorded_at=f"{older}T12:00:00+00:00"),
        UsageRecord("org-1", "api_call", 1.0, recorded_at=f"{yesterday}T12:00:00+00:00"),
        UsageRecord("org-1", "api_call", 1.0, recorded_at=f"{today}T12:00:00+00:00"),
    ]
    summary = await meter.get_summary(
        "org-1",
        period_start=yesterday.isoformat(),
        period_end=today.isoformat(),
    )
    assert summary.api_calls == 2  # `older` excluded


async def test_summary_returns_zeros_when_org_has_no_records(meter: UsageMeter):
    summary = await meter.get_summary("ghost-org")
    assert summary.api_calls == 0
    assert summary.ai_inferences == 0
    assert summary.storage_bytes == 0
    assert summary.documents_processed == 0


# ---- Redis-backed paths --------------------------------------------------


async def test_record_writes_to_redis_when_available():
    """When Redis is reachable, ``record`` rpushes the JSON payload and
    sets a TTL on the key."""
    fake = AsyncMock()
    fake.rpush = AsyncMock(return_value=1)
    fake.expire = AsyncMock(return_value=True)

    with patch("app.services.usage.meter._get_redis", new=AsyncMock(return_value=fake)):
        meter = UsageMeter()
        await meter.record("org-1", "api_call", value=3.0)

    fake.rpush.assert_awaited_once()
    args, _ = fake.rpush.call_args
    assert args[0] == "cai:usage:org-1"
    fake.expire.assert_awaited_once()


async def test_record_falls_back_silently_when_redis_raises():
    """Redis errors must not propagate — the in-memory record still
    happens, and the caller never knows."""
    fake = AsyncMock()
    fake.rpush = AsyncMock(side_effect=ConnectionError("redis down"))

    with patch("app.services.usage.meter._get_redis", new=AsyncMock(return_value=fake)):
        meter = UsageMeter()
        # Must not raise:
        await meter.record("org-1", "api_call")

    assert len(meter._records) == 1


async def test_summary_reads_from_redis_first():
    """When Redis has data, ``get_summary`` builds the summary from it
    rather than from the in-process buffer."""
    today = date.today().isoformat()
    payload = [
        f'{{"org_id": "org-1", "metric": "api_call", "value": 1, "recorded_at": "{today}T09:00:00+00:00", "metadata": {{}}}}',
        f'{{"org_id": "org-1", "metric": "ai_inference", "value": 1, "recorded_at": "{today}T10:00:00+00:00", "metadata": {{}}}}',
    ]
    fake = AsyncMock()
    fake.lrange = AsyncMock(return_value=payload)

    with patch("app.services.usage.meter._get_redis", new=AsyncMock(return_value=fake)):
        meter = UsageMeter()
        # Buffer is empty — proves the data must have come from Redis.
        summary = await meter.get_summary("org-1")

    assert summary.api_calls == 1
    assert summary.ai_inferences == 1


# ---- get_usage_meter singleton ------------------------------------------


def test_get_usage_meter_returns_singleton():
    from app.services.usage import meter as meter_mod

    # Reset for hermeticity — module may have been touched by other tests.
    meter_mod._meter = None
    a = get_usage_meter()
    b = get_usage_meter()
    assert a is b
