"""Tests for AI progress tracking from site photos.

Covers:
- Detection-to-CSI mapping logic
- Activity keyword matching
- Percent complete estimation with historical comparison
- Variance calculation (ahead/behind/on_track)
- Progress snapshot creation and application
- Photo analysis pipeline (with mocked YOLO)
- Edge cases: no detections, no activities, empty history
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vision.progress_tracker import (
    ACTIVITY_PHASE_KEYWORDS,
    DETECTION_TO_CSI_MAP,
    ActivityMatch,
    auto_update_schedule_progress,
    compare_against_schedule,
    create_progress_snapshot,
    estimate_percent_complete,
    map_detections_to_activities,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_detection(class_name: str, confidence: float = 0.85) -> dict:
    return {
        "class_name": class_name,
        "confidence": confidence,
        "bbox": [10, 20, 100, 200],
    }


def _make_activity(
    name: str,
    activity_code: str = "A100",
    wbs_code: str = "",
    pct_complete: float = 0,
    duration_days: int = 10,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "activity_code": activity_code,
        "wbs_code": wbs_code,
        "pct_complete": Decimal(str(pct_complete)),
        "duration_days": duration_days,
        "start_date": date.today() - timedelta(days=5),
        "finish_date": date.today() + timedelta(days=5),
        "resource_assignments": [],
    }


# ===========================================================================
# TestDetectionToCSIMapping
# ===========================================================================


class TestDetectionToCSIMapping:
    """Test DETECTION_TO_CSI_MAP completeness and correctness."""

    def test_excavator_maps_to_earthwork(self):
        assert "31 00 00" in DETECTION_TO_CSI_MAP["excavator"]

    def test_crane_maps_to_steel_and_concrete(self):
        csi = DETECTION_TO_CSI_MAP["crane"]
        assert "05 00 00" in csi
        assert "03 00 00" in csi

    def test_concrete_mixer_maps_to_concrete(self):
        assert "03 00 00" in DETECTION_TO_CSI_MAP["concrete_mixer"]

    def test_scaffolding_maps_to_temporary(self):
        assert "01 50 00" in DETECTION_TO_CSI_MAP["scaffolding"]

    def test_dump_truck_maps_to_earthwork(self):
        assert "31 00 00" in DETECTION_TO_CSI_MAP["dump_truck"]

    def test_loader_maps_to_earthwork(self):
        assert "31 00 00" in DETECTION_TO_CSI_MAP["loader"]

    def test_worker_classes_not_in_equipment_map(self):
        for cls in ("person", "hard_hat", "safety_vest"):
            assert cls not in DETECTION_TO_CSI_MAP


# ===========================================================================
# TestActivityPhaseKeywords
# ===========================================================================


class TestActivityPhaseKeywords:
    """Test ACTIVITY_PHASE_KEYWORDS coverage."""

    def test_earthwork_keywords(self):
        keywords = ACTIVITY_PHASE_KEYWORDS["31 00 00"]
        assert "excavat" in keywords
        assert "grade" in keywords
        assert "backfill" in keywords

    def test_concrete_keywords(self):
        keywords = ACTIVITY_PHASE_KEYWORDS["03 00 00"]
        assert "concrete" in keywords
        assert "pour" in keywords
        assert "formwork" in keywords
        assert "rebar" in keywords

    def test_steel_keywords(self):
        keywords = ACTIVITY_PHASE_KEYWORDS["05 00 00"]
        assert "steel" in keywords
        assert "erect" in keywords

    def test_all_csi_divisions_have_keywords(self):
        # Every CSI in detection map should have keywords
        for csi_list in DETECTION_TO_CSI_MAP.values():
            for csi in csi_list:
                assert csi in ACTIVITY_PHASE_KEYWORDS, f"Missing keywords for {csi}"


# ===========================================================================
# TestMapDetectionsToActivities
# ===========================================================================


class TestMapDetectionsToActivities:
    """Test the detection-to-activity mapping logic."""

    def test_excavator_matches_excavation_activity(self):
        detections = [_make_detection("excavator", 0.90)]
        activities = [_make_activity("Site Excavation and Grading")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) >= 1
        assert matches[0].detection_class == "excavator"
        assert matches[0].csi_division == "31 00 00"

    def test_crane_matches_steel_erection(self):
        detections = [_make_detection("crane", 0.88)]
        activities = [_make_activity("Structural Steel Erection")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) >= 1
        assert "steel" in matches[0].activity_name.lower() or matches[0].detection_class == "crane"

    def test_concrete_mixer_matches_concrete_pour(self):
        detections = [_make_detection("concrete_mixer", 0.85)]
        activities = [_make_activity("Concrete Foundation Pour")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) >= 1
        assert matches[0].csi_division == "03 00 00"

    def test_no_match_for_unrelated_activity(self):
        detections = [_make_detection("excavator", 0.90)]
        activities = [_make_activity("Install HVAC Ductwork")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) == 0

    def test_worker_detections_ignored_in_matching(self):
        detections = [
            _make_detection("person", 0.95),
            _make_detection("hard_hat", 0.90),
            _make_detection("safety_vest", 0.88),
        ]
        activities = [_make_activity("Concrete Foundation Pour")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) == 0

    def test_multiple_equipment_match_same_activity(self):
        detections = [
            _make_detection("excavator", 0.90),
            _make_detection("dump_truck", 0.85),
        ]
        activities = [_make_activity("Site Earthwork and Grading")]
        matches = map_detections_to_activities(detections, activities)

        # Both should match the earthwork activity
        assert len(matches) == 2
        activity_ids = {m.activity_id for m in matches}
        assert len(activity_ids) == 1  # same activity

    def test_match_score_incorporates_confidence(self):
        detections = [_make_detection("excavator", 0.95)]
        activities = [_make_activity("Excavation Phase 1")]
        matches = map_detections_to_activities(detections, activities)

        assert len(matches) == 1
        assert 0 < matches[0].match_score <= 1.0

    def test_empty_detections_returns_empty(self):
        matches = map_detections_to_activities([], [_make_activity("Test")])
        assert matches == []

    def test_empty_activities_returns_empty(self):
        detections = [_make_detection("crane", 0.90)]
        matches = map_detections_to_activities(detections, [])
        assert matches == []

    def test_no_duplicate_activity_detection_pairs(self):
        # Same detection twice should not produce duplicates
        detections = [
            _make_detection("excavator", 0.90),
            _make_detection("excavator", 0.85),
        ]
        activities = [_make_activity("Site Excavation")]
        matches = map_detections_to_activities(detections, activities)

        pairs = [(m.activity_id, m.detection_class) for m in matches]
        assert len(pairs) == len(set(pairs))


# ===========================================================================
# TestEstimatePercentComplete
# ===========================================================================


class TestEstimatePercentComplete:
    """Test the percent complete estimation heuristics."""

    def test_single_equipment_gives_base_estimate(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.7,
                detection_confidence=0.90,
            )
        ]
        result = estimate_percent_complete(matches)

        assert "a1" in result
        pct = result["a1"]
        assert Decimal("5") <= pct <= Decimal("85")

    def test_multiple_equipment_types_boost_estimate(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.7,
                detection_confidence=0.90,
            ),
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="dump_truck",
                csi_division="31 00 00",
                match_score=0.6,
                detection_confidence=0.85,
            ),
        ]
        single_match = [matches[0]]
        single_result = estimate_percent_complete(single_match)
        multi_result = estimate_percent_complete(matches)

        assert multi_result["a1"] > single_result["a1"]

    def test_historical_data_increases_estimate(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.7,
                detection_confidence=0.90,
            )
        ]
        historical = [
            {
                "activities_progress": {"a1": 40.0},
                "snapshot_date": date.today() - timedelta(days=1),
            }
        ]

        result_no_history = estimate_percent_complete(matches)
        result_with_history = estimate_percent_complete(matches, historical)

        assert result_with_history["a1"] > result_no_history["a1"]

    def test_estimate_capped_at_85(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.99,
                detection_confidence=0.99,
            )
        ]
        # History at 82% — should still cap at 85%
        historical = [
            {
                "activities_progress": {"a1": 82.0},
                "snapshot_date": date.today() - timedelta(days=1),
            }
        ]
        result = estimate_percent_complete(matches, historical)
        assert result["a1"] <= Decimal("85")

    def test_estimate_minimum_5_percent(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.01,
                detection_confidence=0.30,
            )
        ]
        result = estimate_percent_complete(matches)
        assert result["a1"] >= Decimal("5")

    def test_no_matches_returns_empty(self):
        result = estimate_percent_complete([])
        assert result == {}

    def test_never_goes_backwards_from_history(self):
        matches = [
            ActivityMatch(
                activity_id="a1",
                activity_name="Excavation",
                detection_class="excavator",
                csi_division="31 00 00",
                match_score=0.3,
                detection_confidence=0.40,
            )
        ]
        historical = [
            {
                "activities_progress": {"a1": 50.0},
                "snapshot_date": date.today() - timedelta(days=1),
            }
        ]
        result = estimate_percent_complete(matches, historical)
        # Should not go below the historical value of 50
        assert result["a1"] >= Decimal("50")


# ===========================================================================
# TestCompareAgainstSchedule
# ===========================================================================


class TestCompareAgainstSchedule:
    """Test variance calculation between estimated and scheduled progress."""

    @pytest.mark.asyncio
    async def test_ahead_of_schedule(self):
        activity_id = str(uuid.uuid4())
        estimated = {activity_id: Decimal("60")}

        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(activity_id)
        mock_activity.name = "Foundation Excavation"
        mock_activity.pct_complete = Decimal("40")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_activity]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        variances = await compare_against_schedule(db, _PROJECT_ID, estimated)

        assert len(variances) == 1
        assert variances[0].status == "ahead"
        assert variances[0].variance_pct == Decimal("20")

    @pytest.mark.asyncio
    async def test_behind_schedule(self):
        activity_id = str(uuid.uuid4())
        estimated = {activity_id: Decimal("20")}

        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(activity_id)
        mock_activity.name = "Concrete Pour"
        mock_activity.pct_complete = Decimal("50")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_activity]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        variances = await compare_against_schedule(db, _PROJECT_ID, estimated)

        assert len(variances) == 1
        assert variances[0].status == "behind"
        assert variances[0].variance_pct == Decimal("-30")

    @pytest.mark.asyncio
    async def test_on_track(self):
        activity_id = str(uuid.uuid4())
        estimated = {activity_id: Decimal("52")}

        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(activity_id)
        mock_activity.name = "Steel Erection"
        mock_activity.pct_complete = Decimal("50")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_activity]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        variances = await compare_against_schedule(db, _PROJECT_ID, estimated)

        assert len(variances) == 1
        assert variances[0].status == "on_track"

    @pytest.mark.asyncio
    async def test_empty_progress_returns_empty(self):
        db = AsyncMock()
        variances = await compare_against_schedule(db, _PROJECT_ID, {})
        assert variances == []

    @pytest.mark.asyncio
    async def test_unmatched_activity_excluded(self):
        """If the activity_id from estimates is not found in DB, skip it."""
        estimated = {str(uuid.uuid4()): Decimal("50")}

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        variances = await compare_against_schedule(db, _PROJECT_ID, estimated)
        assert variances == []


# ===========================================================================
# TestAutoUpdateScheduleProgress
# ===========================================================================


class TestAutoUpdateScheduleProgress:
    """Test writing AI progress to schedule activity metadata."""

    @pytest.mark.asyncio
    async def test_updates_metadata(self):
        activity_id = str(uuid.uuid4())
        progress = {activity_id: Decimal("45.5")}

        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(activity_id)
        mock_activity.metadata_ = {}

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_activity]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        count = await auto_update_schedule_progress(db, _PROJECT_ID, progress)

        assert count == 1
        assert mock_activity.metadata_["ai_pct_complete"] == 45.5
        assert "last_photo_update" in mock_activity.metadata_

    @pytest.mark.asyncio
    async def test_empty_progress_returns_zero(self):
        db = AsyncMock()
        count = await auto_update_schedule_progress(db, _PROJECT_ID, {})
        assert count == 0

    @pytest.mark.asyncio
    async def test_snapshot_id_stored(self):
        activity_id = str(uuid.uuid4())
        snapshot_id = uuid.uuid4()
        progress = {activity_id: Decimal("30")}

        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(activity_id)
        mock_activity.metadata_ = {}

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_activity]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        await auto_update_schedule_progress(db, _PROJECT_ID, progress, snapshot_id)

        assert mock_activity.metadata_["last_snapshot_id"] == str(snapshot_id)


# ===========================================================================
# TestCreateProgressSnapshot
# ===========================================================================


class TestCreateProgressSnapshot:
    """Test snapshot creation with weighted overall progress calculation."""

    @pytest.mark.asyncio
    async def test_snapshot_overall_progress(self):
        a1 = str(uuid.uuid4())
        a2 = str(uuid.uuid4())
        progress = {a1: Decimal("60"), a2: Decimal("30")}

        # Mock activities with durations
        mock_a1 = MagicMock()
        mock_a1.id = uuid.UUID(a1)
        mock_a1.duration_days = 20  # heavier weight

        mock_a2 = MagicMock()
        mock_a2.id = uuid.UUID(a2)
        mock_a2.duration_days = 10

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_a1, mock_a2]

        mock_snapshot = MagicMock()
        mock_snapshot.id = uuid.uuid4()
        mock_snapshot.overall_progress = Decimal("50.00")
        mock_snapshot.activities_progress = {a1: 60.0, a2: 30.0}
        mock_snapshot.snapshot_date = date.today()

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.models.progress_tracking.ProgressSnapshot",
            return_value=mock_snapshot,
        ):
            await create_progress_snapshot(db, _PROJECT_ID, [uuid.uuid4()], progress)

        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_with_empty_progress(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_snapshot = MagicMock()
        mock_snapshot.id = uuid.uuid4()

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.models.progress_tracking.ProgressSnapshot",
            return_value=mock_snapshot,
        ):
            await create_progress_snapshot(db, _PROJECT_ID, [], {})

        db.add.assert_called_once()


# ===========================================================================
# TestAnalyzeProgressPhoto
# ===========================================================================


class TestAnalyzeProgressPhoto:
    """Test the full photo analysis pipeline with mocked YOLO."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_detections(self):
        """Test that photo analysis runs detection, maps, and stores."""
        mock_detections = [
            {"class_name": "excavator", "confidence": 0.92, "bbox": [10, 20, 100, 200]},
            {"class_name": "person", "confidence": 0.95, "bbox": [50, 50, 80, 180]},
            {"class_name": "hard_hat", "confidence": 0.88, "bbox": [52, 50, 78, 70]},
        ]

        act_id = str(uuid.uuid4())
        mock_activity = MagicMock()
        mock_activity.id = uuid.UUID(act_id)
        mock_activity.name = "Site Excavation"
        mock_activity.activity_code = "A100"
        mock_activity.wbs_code = "1.1"
        mock_activity.pct_complete = Decimal("20")
        mock_activity.duration_days = 15
        mock_activity.start_date = date.today() - timedelta(days=5)
        mock_activity.finish_date = date.today() + timedelta(days=10)
        mock_activity.resource_assignments = []

        mock_photo = MagicMock()
        mock_photo.id = uuid.uuid4()

        # Setup DB mock
        db = AsyncMock()
        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # Schedule activities query
                mock_result.scalars.return_value.all.return_value = [mock_activity]
            elif call_count == 2:
                # Historical snapshots query
                mock_result.scalars.return_value.all.return_value = []
            else:
                mock_result.scalars.return_value.all.return_value = []
            return mock_result

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch(
                "app.services.vision.progress_tracker._run_detection",
                return_value=mock_detections,
            ),
            patch(
                "app.models.progress_tracking.ProgressPhoto",
                return_value=mock_photo,
            ),
        ):
            from app.services.vision.progress_tracker import analyze_progress_photo

            result = await analyze_progress_photo(
                db=db,
                project_id=_PROJECT_ID,
                photo_bytes=b"fake_image_data",
                photo_url="test.jpg",
                uploaded_by=_USER_ID,
            )

        assert result.worker_count == 2  # person + hard_hat
        assert len(result.equipment_detected) == 1
        assert result.equipment_detected[0]["class_name"] == "excavator"

    @pytest.mark.asyncio
    async def test_no_detections_returns_zero_confidence(self):
        """Test behavior when YOLO finds nothing."""
        db = AsyncMock()
        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            return mock_result

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        mock_photo = MagicMock()
        mock_photo.id = uuid.uuid4()

        with (
            patch(
                "app.services.vision.progress_tracker._run_detection",
                return_value=[],
            ),
            patch(
                "app.models.progress_tracking.ProgressPhoto",
                return_value=mock_photo,
            ),
        ):
            from app.services.vision.progress_tracker import analyze_progress_photo

            result = await analyze_progress_photo(
                db=db,
                project_id=_PROJECT_ID,
                photo_bytes=b"fake",
                photo_url="test.jpg",
            )

        assert result.worker_count == 0
        assert result.equipment_detected == []
        assert result.overall_confidence == Decimal("0.00")


# ===========================================================================
# TestProgressPhotoModel
# ===========================================================================


class TestProgressPhotoModel:
    """Test the ProgressPhoto SQLAlchemy model."""

    def test_model_instantiation(self):
        from app.models.progress_tracking import ProgressPhoto

        photo = ProgressPhoto(
            project_id=_PROJECT_ID,
            photo_url="https://example.com/photo.jpg",
            s3_key="progress/test/photo.jpg",
        )
        assert photo.photo_url == "https://example.com/photo.jpg"
        assert photo.s3_key == "progress/test/photo.jpg"


class TestProgressSnapshotModel:
    """Test the ProgressSnapshot SQLAlchemy model."""

    def test_model_instantiation(self):
        from app.models.progress_tracking import ProgressSnapshot

        snap = ProgressSnapshot(
            project_id=_PROJECT_ID,
            snapshot_date=date.today(),
            activities_progress={"a1": 50.0},
            overall_progress=Decimal("50.00"),
        )
        assert snap.activities_progress == {"a1": 50.0}
        assert snap.overall_progress == Decimal("50.00")
