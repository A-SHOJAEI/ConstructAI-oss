from __future__ import annotations

import pytest

from app.services.safety.alert_service import (
    clear_dedup_cache_async,
    create_alert_record,
    is_duplicate,
    process_safety_event,
)
from tests.fixtures.mock_detections import MOCK_SAFETY_EVENT


class TestAlertService:
    @pytest.fixture(autouse=True)
    async def _clear_dedup(self):
        # Sync setup_method() couldn't flush Redis (the production primary
        # dedup store); leftover keys from previous tests caused fresh
        # events to look like duplicates.
        await clear_dedup_cache_async()
        yield
        await clear_dedup_cache_async()

    async def test_create_alert_record(self):
        record = create_alert_record(
            project_id="proj-1",
            camera_id="cam-1",
            zone_id="zone-1",
            priority="P2_high",
            alert_type="ppe_violation",
            description="Missing hardhat",
            detections=[{}],
            confidence=0.85,
        )
        assert record["priority"] == "P2_high"
        assert record["is_acknowledged"] is False
        assert "id" in record

    async def test_dedup_suppresses_duplicate(self):
        assert await is_duplicate("cam1", "zone1", "ppe_violation", "track1") is False
        assert await is_duplicate("cam1", "zone1", "ppe_violation", "track1") is True

    async def test_dedup_allows_different_track(self):
        assert await is_duplicate("cam1", "zone1", "ppe_violation", "track1") is False
        assert await is_duplicate("cam1", "zone1", "ppe_violation", "track2") is False

    async def test_process_safety_event_returns_alert(self):
        result = await process_safety_event(MOCK_SAFETY_EVENT)
        assert result is not None
        assert result["alert_type"] == "ppe_violation"
        assert "P" in result["priority"]

    async def test_process_safety_event_dedup(self):
        result1 = await process_safety_event(MOCK_SAFETY_EVENT)
        result2 = await process_safety_event(MOCK_SAFETY_EVENT)
        assert result1 is not None
        assert result2 is None  # Duplicate suppressed
