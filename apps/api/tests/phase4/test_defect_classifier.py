"""Tests for defect classification service."""

from __future__ import annotations

from app.services.quality.defect_classifier import (
    DEFECT_TYPES,
    DEFECT_TYPES_V1_0,
    DEFECT_TYPES_V1_1,
    SEVERITY_MAP,
    DefectClassifier,
    _get_recommendations,
)


class TestDefectClassifier:
    async def test_fallback_classification(self):
        classifier = DefectClassifier()
        result = await classifier.classify(b"fake_image_bytes")
        assert "defect_type" in result
        assert "confidence" in result
        assert "severity_estimate" in result
        assert "recommendations" in result

    async def test_fallback_returns_valid_type(self):
        classifier = DefectClassifier()
        result = await classifier.classify(b"test")
        assert isinstance(result["confidence"], float)
        assert result["confidence"] > 0

    def test_defect_types_v1_1(self):
        assert len(DEFECT_TYPES_V1_1) == 8
        assert "crack" in DEFECT_TYPES_V1_1
        assert "spalling" in DEFECT_TYPES_V1_1
        assert "no_defect" in DEFECT_TYPES_V1_1
        assert "efflorescence" in DEFECT_TYPES_V1_1
        assert "exposed_rebar" in DEFECT_TYPES_V1_1

    def test_defect_types_v1_0_legacy(self):
        assert len(DEFECT_TYPES_V1_0) == 12
        assert "crack_structural" in DEFECT_TYPES_V1_0

    def test_default_defect_types_is_v1_1(self):
        assert DEFECT_TYPES is DEFECT_TYPES_V1_1

    def test_severity_map_covers_v1_1(self):
        for dtype in DEFECT_TYPES_V1_1:
            assert dtype in SEVERITY_MAP

    def test_severity_map_covers_v1_0(self):
        for dtype in DEFECT_TYPES_V1_0:
            assert dtype in SEVERITY_MAP

    def test_get_recommendations_v1_1_crack(self):
        recs = _get_recommendations("crack")
        assert len(recs) >= 2
        assert any("structural" in r.lower() for r in recs)

    def test_get_recommendations_v1_1_no_defect(self):
        recs = _get_recommendations("no_defect")
        assert len(recs) >= 1
        assert any("no defect" in r.lower() for r in recs)

    def test_get_recommendations_legacy(self):
        recs = _get_recommendations("crack_structural")
        assert len(recs) >= 2

    def test_get_recommendations_unknown_type(self):
        recs = _get_recommendations("unknown_type")
        assert len(recs) >= 1

    def test_severity_levels_v1_1(self):
        assert SEVERITY_MAP["crack"] == "critical"
        assert SEVERITY_MAP["exposed_rebar"] == "critical"
        assert SEVERITY_MAP["spalling"] == "major"
        assert SEVERITY_MAP["corrosion"] == "major"
        assert SEVERITY_MAP["biological_growth"] == "minor"
        assert SEVERITY_MAP["no_defect"] == "none"

    def test_severity_levels_legacy(self):
        assert SEVERITY_MAP["crack_structural"] == "critical"
        assert SEVERITY_MAP["rebar_exposure"] == "critical"
        assert SEVERITY_MAP["crack_cosmetic"] == "minor"

    def test_auto_version_detection_no_model(self):
        classifier = DefectClassifier()
        assert classifier._model_version == "v1.1"
        assert classifier._class_names == list(DEFECT_TYPES_V1_1)

    async def test_fallback_uses_v1_1_type(self):
        classifier = DefectClassifier()
        result = await classifier.classify(b"test")
        assert result["defect_type"] == "surface_deterioration"
