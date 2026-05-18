"""Tests verifying DefectClassifier async compatibility."""

import inspect


class TestDefectClassifierAsync:
    """Verify classify() is properly async and fallback is sync."""

    def test_classify_is_coroutine_function(self):
        """DefectClassifier.classify should be an async method."""
        from app.services.quality.defect_classifier import DefectClassifier

        assert inspect.iscoroutinefunction(DefectClassifier.classify), (
            "classify() must be async def"
        )

    def test_model_classify_is_coroutine_function(self):
        """DefectClassifier._model_classify should be an async method."""
        from app.services.quality.defect_classifier import DefectClassifier

        assert inspect.iscoroutinefunction(DefectClassifier._model_classify), (
            "_model_classify() must be async def"
        )

    def test_fallback_classify_is_not_coroutine(self):
        """_fallback_classify should be a regular (sync) method."""
        from app.services.quality.defect_classifier import DefectClassifier

        assert not inspect.iscoroutinefunction(DefectClassifier._fallback_classify), (
            "_fallback_classify() should be sync"
        )

    def test_fallback_returns_dict(self):
        """_fallback_classify should return a plain dict, not a coroutine."""
        from app.services.quality.defect_classifier import DefectClassifier

        classifier = DefectClassifier()
        result = classifier._fallback_classify()
        assert isinstance(result, dict), "Fallback should return dict"
        assert "defect_type" in result
