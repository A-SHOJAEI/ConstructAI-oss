"""Tests for the parametric cost model pipeline.

Covers:
- Synthetic data generation (feature distributions, cost ranges)
- Feature encoding (v1.0 and v1.1 formats)
- Heuristic fallback (known inputs → expected ranges)
- XGBoost model training + prediction
- Prediction intervals
- Model auto-discovery
- Prediction logging
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

# Add monorepo root to path so `ml.*` imports work from apps/api
_MONOREPO_ROOT = str(Path(__file__).resolve().parents[3])
if _MONOREPO_ROOT not in sys.path:
    sys.path.insert(0, _MONOREPO_ROOT)

from app.services.estimating.parametric_model import (
    _confidence_margins,
    _detect_model_version,
    _encode_features_v11,
    _inflation_factor,
    _resolve_type,
    get_prediction_log,
    predict_cost,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_params(**overrides) -> dict:
    """Create a default project params dict with optional overrides."""
    defaults = {
        "sqft": 50_000,
        "stories": 3,
        "type": "commercial_office",
        "region": "national",
        "quality_level": "standard",
        "construction_year": 2024,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Test: Heuristic fallback (existing behavior, must still pass)
# ---------------------------------------------------------------------------


class TestHeuristicFallback:
    """Tests for the heuristic cost prediction path (no ML model)."""

    @pytest.mark.asyncio
    async def test_commercial_office_range(self):
        """50K SF commercial office should be in known range."""
        result = await predict_cost(_make_params())
        psf = float(result["predicted_cost_per_sqft"])
        # RSMeans base is $285/SF; with standard quality, national region, 2024
        assert 275 <= psf <= 295
        assert result["model_used"] == "heuristic"

    @pytest.mark.asyncio
    async def test_healthcare_more_expensive(self):
        """Healthcare should cost more per SF than warehouse."""
        healthcare = await predict_cost(_make_params(type="healthcare"))
        warehouse = await predict_cost(_make_params(type="industrial_warehouse"))
        assert healthcare["predicted_cost_per_sqft"] > warehouse["predicted_cost_per_sqft"]

    @pytest.mark.asyncio
    async def test_premium_quality_multiplier(self):
        """Premium quality should be 1.5x standard."""
        standard = await predict_cost(_make_params(quality_level="standard"))
        premium = await predict_cost(_make_params(quality_level="premium"))
        ratio = float(premium["predicted_cost_per_sqft"]) / float(
            standard["predicted_cost_per_sqft"]
        )
        assert 1.45 <= ratio <= 1.55

    @pytest.mark.asyncio
    async def test_northeast_region_premium(self):
        """Northeast should be ~15% more than national."""
        national = await predict_cost(_make_params(region="national"))
        northeast = await predict_cost(_make_params(region="northeast"))
        ratio = float(northeast["predicted_cost_per_sqft"]) / float(
            national["predicted_cost_per_sqft"]
        )
        assert 1.10 <= ratio <= 1.20

    @pytest.mark.asyncio
    async def test_story_multiplier_highrise(self):
        """10-story building should cost more per SF than 3-story."""
        low = await predict_cost(_make_params(stories=3))
        high = await predict_cost(_make_params(stories=10))
        assert high["predicted_cost_per_sqft"] > low["predicted_cost_per_sqft"]

    @pytest.mark.asyncio
    async def test_inflation_2026(self):
        """2026 costs should be ~9.2% higher than 2024 (1.045^2)."""
        c2024 = await predict_cost(_make_params(construction_year=2024))
        c2026 = await predict_cost(_make_params(construction_year=2026))
        ratio = float(c2026["predicted_cost_per_sqft"]) / float(c2024["predicted_cost_per_sqft"])
        assert 1.08 <= ratio <= 1.10

    @pytest.mark.asyncio
    async def test_confidence_interval_surrounds_estimate(self):
        """CI should bracket the total cost."""
        result = await predict_cost(_make_params())
        ci = result["confidence_interval"]
        assert ci["low"] < result["total_predicted_cost"]
        assert ci["high"] > result["total_predicted_cost"]

    @pytest.mark.asyncio
    async def test_confidence_interval_simple_type(self):
        """Simple types should have tighter CI (+-12%)."""
        result = await predict_cost(_make_params(type="residential_single_family"))
        ci = result["confidence_interval"]
        total = float(result["total_predicted_cost"])
        low_pct = float(ci["low"]) / total
        high_pct = float(ci["high"]) / total
        assert 0.86 <= low_pct <= 0.90
        assert 1.10 <= high_pct <= 1.14

    @pytest.mark.asyncio
    async def test_confidence_interval_complex_type(self):
        """Complex types should have wider CI (+-25%)."""
        result = await predict_cost(_make_params(type="healthcare"))
        ci = result["confidence_interval"]
        total = float(result["total_predicted_cost"])
        low_pct = float(ci["low"]) / total
        high_pct = float(ci["high"]) / total
        assert 0.73 <= low_pct <= 0.77
        assert 1.23 <= high_pct <= 1.27

    @pytest.mark.asyncio
    async def test_legacy_type_alias(self):
        """Legacy 'commercial' should map to 'commercial_office'."""
        legacy = await predict_cost(_make_params(type="commercial"))
        explicit = await predict_cost(_make_params(type="commercial_office"))
        assert legacy["predicted_cost_per_sqft"] == explicit["predicted_cost_per_sqft"]

    @pytest.mark.asyncio
    async def test_heuristic_returns_prediction_intervals_empty(self):
        """Heuristic path returns empty prediction_intervals dict."""
        result = await predict_cost(_make_params())
        assert "prediction_intervals" in result
        assert result["prediction_intervals"] == {}


# ---------------------------------------------------------------------------
# Test: Known building type costs
# ---------------------------------------------------------------------------


class TestKnownCosts:
    """Verify heuristic costs match RSMeans-calibrated values."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "btype,expected_psf",
        [
            ("commercial_office", 285),
            ("healthcare", 450),
            ("industrial_warehouse", 130),
            ("residential_single_family", 165),
            ("infrastructure_road", 95),
        ],
    )
    async def test_base_cost_per_sf(self, btype, expected_psf):
        """Heuristic base cost should match the RSMeans table."""
        result = await predict_cost(
            _make_params(
                type=btype,
                quality_level="standard",
                region="national",
                construction_year=2024,
                stories=1,
            ),
        )
        psf = float(result["predicted_cost_per_sqft"])
        # Allow small rounding tolerance
        assert abs(psf - expected_psf) < 1.0, f"{btype}: expected ~{expected_psf}, got {psf}"


# ---------------------------------------------------------------------------
# Test: Feature encoding
# ---------------------------------------------------------------------------


class TestFeatureEncoding:
    """Tests for v1.0 and v1.1 feature encoding."""

    def test_resolve_type_alias(self):
        assert _resolve_type("commercial") == "commercial_office"
        assert _resolve_type("residential") == "residential_single_family"
        assert _resolve_type("COMMERCIAL") == "commercial_office"

    def test_resolve_type_direct(self):
        assert _resolve_type("healthcare") == "healthcare"
        assert _resolve_type("mixed_use") == "mixed_use"

    def test_v11_encoding_shape(self):
        """v1.1 feature vector should have 24 elements."""
        vec = _encode_features_v11(_make_params())
        assert vec.shape == (1, 24)

    def test_v11_one_hot_commercial_office(self):
        """Building type one-hot should have exactly one 1."""
        vec = _encode_features_v11(_make_params(type="commercial_office"))
        one_hot = vec[0, :14]
        assert sum(one_hot) == 1.0
        assert one_hot[0] == 1.0  # commercial_office is index 0

    def test_v11_one_hot_healthcare(self):
        """Healthcare should be index 9."""
        vec = _encode_features_v11(_make_params(type="healthcare"))
        assert vec[0, 9] == 1.0
        assert sum(vec[0, :14]) == 1.0

    def test_v11_log_area(self):
        """Area should be log-transformed."""
        vec = _encode_features_v11(_make_params(sqft=50_000))
        log_area = vec[0, 14]  # first continuous feature after one-hot
        assert abs(log_area - math.log(50_000)) < 0.01

    def test_v11_quality_encoding(self):
        """Quality levels should be ordinally encoded 0-3."""
        for quality, expected in [
            ("economy", 0),
            ("average", 1),
            ("above_average", 2),
            ("luxury", 3),
        ]:
            vec = _encode_features_v11(_make_params(quality_level=quality))
            assert vec[0, 16] == expected, f"quality={quality} expected {expected}"

    def test_detect_model_version_v10(self):
        """4-feature model should be detected as v1.0."""
        loaded = {"feature_names": ["sqft", "stories", "type", "region"]}
        assert _detect_model_version(loaded) == "v1.0"

    def test_detect_model_version_v11(self):
        """24-feature model should be detected as v1.1."""
        loaded = {"feature_names": [f"f{i}" for i in range(24)]}
        assert _detect_model_version(loaded) == "v1.1"


# ---------------------------------------------------------------------------
# Test: Inflation
# ---------------------------------------------------------------------------


class TestInflation:
    def test_2024_base_year(self):
        assert _inflation_factor(2024) == Decimal("1")

    def test_2025_one_year(self):
        factor = _inflation_factor(2025)
        assert Decimal("1.04") < factor < Decimal("1.05")

    def test_past_year_deflates(self):
        factor = _inflation_factor(2023)
        assert factor < Decimal("1")


# ---------------------------------------------------------------------------
# Test: Confidence margins
# ---------------------------------------------------------------------------


class TestConfidenceMargins:
    def test_simple_type(self):
        low, high = _confidence_margins("residential_single_family")
        assert low == Decimal("0.88")
        assert high == Decimal("1.12")

    def test_complex_type(self):
        low, high = _confidence_margins("healthcare")
        assert low == Decimal("0.75")
        assert high == Decimal("1.25")

    def test_medium_type(self):
        low, high = _confidence_margins("commercial_office")
        assert low == Decimal("0.82")
        assert high == Decimal("1.18")


# ---------------------------------------------------------------------------
# Test: Synthetic data generation
# ---------------------------------------------------------------------------


class TestSyntheticDataGeneration:
    """Tests for the synthetic training data generator."""

    def test_generate_correct_count(self):
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=500, seed=42)
        assert len(samples) == 500

    def test_all_building_types_present(self):
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=1000, seed=42)
        types = {s.building_type for s in samples}
        assert len(types) == 14

    def test_cost_range_reasonable(self):
        """All costs should be positive and within realistic bounds."""
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=1000, seed=42)
        for s in samples:
            assert s.cost_per_sf > 0, f"Negative cost for {s.building_type}"
            assert s.cost_per_sf < 5000, f"Unrealistic cost {s.cost_per_sf} for {s.building_type}"

    def test_log_area_matches_area(self):
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=100, seed=42)
        for s in samples:
            expected_log = round(math.log(max(s.gross_area_sf, 1)), 4)
            assert abs(s.log_gross_area_sf - expected_log) < 0.01

    def test_quality_encoding_range(self):
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=500, seed=42)
        encodings = {s.quality_encoded for s in samples}
        assert encodings.issubset({0, 1, 2, 3})

    def test_climate_zone_range(self):
        from ml.data.generate_cost_training_data import generate_samples

        samples = generate_samples(num_samples=500, seed=42)
        zones = {s.climate_zone for s in samples}
        assert all(1 <= z <= 8 for z in zones)

    def test_save_and_load(self):
        """Should save to JSON and produce valid file."""
        from ml.data.generate_cost_training_data import generate_samples, save_dataset

        samples = generate_samples(num_samples=100, seed=42)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_dataset(samples, tmpdir)
            assert path.exists()
            with open(path) as f:
                data = json.load(f)
            assert data["metadata"]["num_samples"] == 100
            assert len(data["data"]) == 100

    def test_hazus_cross_validation(self):
        """Generated data should be in rough agreement with Hazus baselines."""
        from ml.data.generate_cost_training_data import (
            generate_samples,
            validate_against_hazus,
        )

        samples = generate_samples(num_samples=5000, seed=42)
        results = validate_against_hazus(samples)
        # Generated costs include amenity multipliers (basement, elevator,
        # parking, climate) on top of RSMeans base, so the ratio should be
        # above 1.0.  Hazus baseline is replacement cost (lower than new
        # construction).  We just verify the median is within a broad but
        # sane range of the RSMeans anchor (0.8x - 2.0x).
        for btype, r in results.items():
            if r["ratio_to_rsm"]:
                assert 0.80 <= r["ratio_to_rsm"] <= 2.0, (
                    f"{btype}: ratio_to_rsm={r['ratio_to_rsm']} outside expected range"
                )


# ---------------------------------------------------------------------------
# Test: XGBoost model training + prediction
# ---------------------------------------------------------------------------


class TestXGBoostModel:
    """Tests for XGBoost training and prediction with a real model."""

    @pytest.fixture(autouse=True)
    def _reset_model_cache(self):
        """Ensure model cache is reset between tests."""
        import app.services.estimating.parametric_model as pm

        pm._cached_model = None
        pm._model_load_attempted = False
        yield
        pm._cached_model = None
        pm._model_load_attempted = False

    def _train_small_model(self):
        """Train a small model for testing."""
        from ml.data.generate_cost_training_data import generate_samples
        from ml.training.train_cost_model import encode_dataset, train_model

        samples = generate_samples(num_samples=2000, seed=42)
        from dataclasses import asdict

        records = [asdict(s) for s in samples]

        X, y = encode_dataset(records)
        valid = y > 0
        X, y = X[valid], y[valid]

        n_test = int(len(X) * 0.15)
        X_train, X_test = X[n_test:], X[:n_test]
        y_train, y_test = y[n_test:], y[:n_test]

        result = train_model(
            X_train,
            y_train,
            X_test,
            y_test,
            params={
                "n_estimators": 50,
                "max_depth": 4,
                "learning_rate": 0.1,
            },
        )
        return result

    def test_training_produces_valid_model(self):
        """Training should produce a model with reasonable metrics."""
        result = self._train_small_model()
        assert result["model"] is not None
        assert result["metrics"]["r2"] > 0.5, f"R²={result['metrics']['r2']} too low"
        assert result["metrics"]["mape"] < 30, f"MAPE={result['metrics']['mape']}% too high"
        assert result["residual_std"] > 0

    def test_feature_importance_non_empty(self):
        """Feature importance should be populated."""
        result = self._train_small_model()
        assert len(result["feature_importance"]) == 24
        # Top features should include building type or area
        top_features = list(result["feature_importance"].keys())[:5]
        assert any("type_" in f or "log_gross" in f or "location" in f for f in top_features)

    def test_save_and_load_model(self):
        """Should save/load via joblib round-trip."""
        from ml.training.train_cost_model import save_to_registry

        result = self._train_small_model()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = Path(tmpdir) / "best_model.joblib"
            assert model_path.exists()
            meta_path = Path(tmpdir) / "metadata.json"
            assert meta_path.exists()

            # Load and verify
            import joblib

            loaded = joblib.load(model_path)
            assert "model" in loaded
            assert len(loaded["feature_names"]) == 24

    @pytest.mark.asyncio
    async def test_xgboost_prediction_path(self):
        """When a v1.1 model is available, predict_cost should use XGBoost."""
        from ml.training.train_cost_model import save_to_registry

        result = self._train_small_model()

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = str(Path(tmpdir) / "best_model.joblib")

            pred = await predict_cost(
                _make_params(
                    location_factor=1.0,
                    has_basement=0,
                    has_elevator=0,
                    parking_type="none",
                    num_units=0,
                    climate_zone=4,
                ),
                model_data={"model_path": model_path},
            )

        assert pred["model_used"] == "xgboost"
        assert pred["model_available"] is True
        assert pred["predicted_cost_per_sqft"] > 0
        assert pred["total_predicted_cost"] > 0

    @pytest.mark.asyncio
    async def test_xgboost_prediction_intervals(self):
        """XGBoost path should return multi-level prediction intervals."""
        from ml.training.train_cost_model import save_to_registry

        result = self._train_small_model()

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = str(Path(tmpdir) / "best_model.joblib")

            pred = await predict_cost(
                _make_params(location_factor=1.0, climate_zone=4),
                model_data={"model_path": model_path},
            )

        pi = pred["prediction_intervals"]
        assert "50%" in pi
        assert "80%" in pi
        assert "90%" in pi
        assert "95%" in pi

        # Wider intervals should have wider ranges
        assert pi["95%"]["high_per_sf"] >= pi["50%"]["high_per_sf"]
        assert pi["50%"]["low_per_sf"] >= pi["95%"]["low_per_sf"]

    @pytest.mark.asyncio
    async def test_xgboost_healthcare_vs_warehouse(self):
        """XGBoost should predict healthcare > warehouse (same as heuristic)."""
        from ml.training.train_cost_model import save_to_registry

        result = self._train_small_model()

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = str(Path(tmpdir) / "best_model.joblib")
            model_data = {"model_path": model_path}

            healthcare = await predict_cost(
                _make_params(type="healthcare", location_factor=1.0, climate_zone=4),
                model_data=model_data,
            )
            warehouse = await predict_cost(
                _make_params(type="industrial_warehouse", location_factor=1.0, climate_zone=4),
                model_data=model_data,
            )

        assert healthcare["predicted_cost_per_sqft"] > warehouse["predicted_cost_per_sqft"]

    @pytest.mark.asyncio
    async def test_xgboost_quality_ordering(self):
        """XGBoost should price luxury > economy."""
        from ml.training.train_cost_model import save_to_registry

        result = self._train_small_model()

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = str(Path(tmpdir) / "best_model.joblib")
            model_data = {"model_path": model_path}

            economy = await predict_cost(
                _make_params(quality_level="economy", location_factor=1.0, climate_zone=4),
                model_data=model_data,
            )
            luxury = await predict_cost(
                _make_params(quality_level="luxury", location_factor=1.0, climate_zone=4),
                model_data=model_data,
            )

        # Quality is applied as a multiplier after model prediction
        assert luxury["predicted_cost_per_sqft"] > economy["predicted_cost_per_sqft"]


# ---------------------------------------------------------------------------
# Test: Model auto-discovery
# ---------------------------------------------------------------------------


class TestModelAutoDiscovery:
    """Tests for automatic model loading from registry."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        import app.services.estimating.parametric_model as pm

        pm._cached_model = None
        pm._model_load_attempted = False
        yield
        pm._cached_model = None
        pm._model_load_attempted = False

    @pytest.mark.asyncio
    async def test_falls_back_when_no_model(self):
        """Without a trained model, should use heuristic."""
        result = await predict_cost(_make_params())
        assert result["model_used"] == "heuristic"

    @pytest.mark.asyncio
    async def test_auto_discovers_model(self):
        """Should auto-discover model from registry path."""
        from dataclasses import asdict

        # Train a tiny model
        from ml.data.generate_cost_training_data import generate_samples
        from ml.training.train_cost_model import encode_dataset, save_to_registry, train_model

        import app.services.estimating.parametric_model as pm

        samples = generate_samples(num_samples=500, seed=42)
        records = [asdict(s) for s in samples]
        X, y = encode_dataset(records)
        valid = y > 0
        X, y = X[valid], y[valid]
        result = train_model(
            X[:400],
            y[:400],
            X[400:],
            y[400:],
            params={"n_estimators": 20, "max_depth": 3, "learning_rate": 0.1},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_to_registry(result, tmpdir)
            model_path = Path(tmpdir) / "best_model.joblib"

            # Patch registry paths to include our temp dir
            with patch.object(pm, "_MODEL_REGISTRY_PATHS", [model_path]):
                pred = await predict_cost(
                    _make_params(location_factor=1.0, climate_zone=4),
                )

            assert pred["model_used"] == "xgboost"

    @pytest.mark.asyncio
    async def test_env_var_override(self):
        """COST_MODEL_PATH env var should take precedence."""
        from dataclasses import asdict

        from ml.data.generate_cost_training_data import generate_samples
        from ml.training.train_cost_model import encode_dataset, save_to_registry, train_model

        import app.services.estimating.parametric_model as pm

        samples = generate_samples(num_samples=500, seed=42)
        records = [asdict(s) for s in samples]
        X, y = encode_dataset(records)
        valid = y > 0
        X, y = X[valid], y[valid]
        result = train_model(
            X[:400],
            y[:400],
            X[400:],
            y[400:],
            params={"n_estimators": 20, "max_depth": 3, "learning_rate": 0.1},
        )

        # L-29 jails COST_MODEL_PATH to the project's `models/` directory,
        # so a generic /tmp tempdir is rejected. Place the artifact in the
        # repo's models/ tree under a unique subdirectory and clean up.
        import shutil

        # Repo root models/. tests/ is under apps/api/, so repo root is
        # parents[3].
        models_root = Path(__file__).resolve().parents[3] / "models"
        models_root.mkdir(exist_ok=True)
        artifact_dir = models_root / "_test_cost_env_override"
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        artifact_dir.mkdir()
        try:
            save_to_registry(result, str(artifact_dir))
            model_path = str(artifact_dir / "best_model.joblib")

            with patch.dict("os.environ", {"COST_MODEL_PATH": model_path}):
                pm._cached_model = None
                pm._model_load_attempted = False
                pred = await predict_cost(
                    _make_params(location_factor=1.0, climate_zone=4),
                )

            assert pred["model_used"] == "xgboost"
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test: Prediction logging
# ---------------------------------------------------------------------------


class TestPredictionLogging:
    """Tests for prediction logging for continuous improvement."""

    @pytest.fixture(autouse=True)
    def _clear_log(self):
        import app.services.estimating.parametric_model as pm

        pm._prediction_log.clear()
        pm._cached_model = None
        pm._model_load_attempted = False
        yield
        pm._prediction_log.clear()
        pm._cached_model = None
        pm._model_load_attempted = False

    @pytest.mark.asyncio
    async def test_predictions_are_logged(self):
        """Each prediction should be logged."""
        await predict_cost(_make_params())
        log = get_prediction_log()
        assert len(log) == 1
        assert log[0]["output"]["model_used"] == "heuristic"
        assert log[0]["input"]["type"] == "commercial_office"

    @pytest.mark.asyncio
    async def test_log_caps_at_max_size(self):
        """Log should not grow beyond _MAX_LOG_SIZE."""
        from collections import deque

        import app.services.estimating.parametric_model as pm

        # Replace the deque with a smaller one to test capping
        pm._prediction_log = deque(maxlen=10)

        for i in range(15):
            await predict_cost(_make_params(sqft=1000 * (i + 1)))

        log = get_prediction_log()
        assert len(log) == 10

        # Restore original
        pm._prediction_log = deque(maxlen=pm._MAX_LOG_SIZE)


# ---------------------------------------------------------------------------
# Test: Realistic scenarios with known expected ranges
# ---------------------------------------------------------------------------


class TestRealisticScenarios:
    """End-to-end scenario tests with known inputs and expected ranges."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        import app.services.estimating.parametric_model as pm

        pm._cached_model = None
        pm._model_load_attempted = False
        yield
        pm._cached_model = None
        pm._model_load_attempted = False

    @pytest.mark.asyncio
    async def test_small_warehouse_texas(self):
        """20K SF warehouse in Texas = low-cost scenario.

        Expected: ~$130/SF * 0.92 (southeast region) = ~$120/SF
        """
        result = await predict_cost(
            {
                "sqft": 20_000,
                "stories": 1,
                "type": "industrial_warehouse",
                "region": "southwest",
                "quality_level": "standard",
                "construction_year": 2024,
            }
        )
        psf = float(result["predicted_cost_per_sqft"])
        total = float(result["total_predicted_cost"])
        assert 110 <= psf <= 130, f"Expected ~$120/SF, got ${psf}"
        assert 2_200_000 <= total <= 2_600_000

    @pytest.mark.asyncio
    async def test_nyc_luxury_highrise(self):
        """500K SF luxury residential high-rise in NYC.

        Expected: $195/SF * 1.50 (premium) * 1.15 (northeast) * 1.05^7 (10 stories)
        """
        result = await predict_cost(
            {
                "sqft": 500_000,
                "stories": 30,
                "type": "residential_multi_family",
                "region": "northeast",
                "quality_level": "premium",
                "construction_year": 2025,
            }
        )
        psf = float(result["predicted_cost_per_sqft"])
        # Multi-family at $195 * premium 1.50 * NE 1.15 * story premium * inflation
        # Should be >$500/SF for a luxury NYC high-rise
        assert psf > 400, f"Expected >$400/SF for NYC luxury high-rise, got ${psf}"
        total = float(result["total_predicted_cost"])
        assert total > 200_000_000, "NYC luxury high-rise should be >$200M"

    @pytest.mark.asyncio
    async def test_hospital_pacific(self):
        """200K SF hospital in Pacific region.

        Expected: $450/SF * 1.18 (pacific) = ~$531/SF
        """
        result = await predict_cost(
            {
                "sqft": 200_000,
                "stories": 5,
                "type": "healthcare",
                "region": "pacific",
                # Valid quality levels: economy / standard / premium / luxury / ultra_premium.
                "quality_level": "premium",
                "construction_year": 2024,
            }
        )
        psf = float(result["predicted_cost_per_sqft"])
        # Healthcare $450 * premium 1.25 * pacific 1.18 * story premium
        assert psf > 550, f"Expected >$550/SF for Pacific hospital, got ${psf}"
        ci = result["confidence_interval"]
        # Healthcare has wide CI (+-25%)
        ci_spread = float(ci["high"] - ci["low"]) / float(result["total_predicted_cost"])
        assert ci_spread > 0.40, "Healthcare CI should be wide (+-25%)"

    @pytest.mark.asyncio
    async def test_k12_school_midwest(self):
        """80K SF K-12 school in Midwest.

        Expected: $310/SF * 0.95 (midwest) = ~$295/SF
        """
        result = await predict_cost(
            {
                "sqft": 80_000,
                "stories": 2,
                "type": "education_k12",
                "region": "midwest",
                "quality_level": "standard",
                "construction_year": 2024,
            }
        )
        psf = float(result["predicted_cost_per_sqft"])
        assert 280 <= psf <= 310, f"Expected ~$295/SF for Midwest school, got ${psf}"

    @pytest.mark.asyncio
    async def test_stories_cap_at_20(self):
        """Story multiplier should cap at 20 stories (17 above 3)."""
        s20 = await predict_cost(_make_params(stories=20))
        s50 = await predict_cost(_make_params(stories=50))
        # Both should have same story multiplier since cap at 20
        assert s20["predicted_cost_per_sqft"] == s50["predicted_cost_per_sqft"]

    @pytest.mark.asyncio
    async def test_max_stories_validation(self):
        """Stories >200 should raise ValueError."""
        with pytest.raises(ValueError, match="200 or fewer"):
            await predict_cost(_make_params(stories=201))


# ---------------------------------------------------------------------------
# Test: Prediction interval evaluation
# ---------------------------------------------------------------------------


class TestPredictionIntervalEvaluation:
    """Tests for prediction interval coverage computation."""

    def test_coverage_computation(self):
        """Coverage should be between 0 and 1."""
        from dataclasses import asdict

        # Create a trivial model + test data
        from ml.data.generate_cost_training_data import generate_samples
        from ml.training.train_cost_model import (
            encode_dataset,
            evaluate_prediction_intervals,
            train_model,
        )

        samples = generate_samples(num_samples=1000, seed=42)
        records = [asdict(s) for s in samples]
        X, y = encode_dataset(records)
        valid = y > 0
        X, y = X[valid], y[valid]

        result = train_model(
            X[:800],
            y[:800],
            X[800:],
            y[800:],
            params={"n_estimators": 50, "max_depth": 4, "learning_rate": 0.1},
        )

        coverage = evaluate_prediction_intervals(
            result["model"],
            X[800:],
            y[800:],
            result["residual_std"],
        )

        # Higher confidence levels should have higher coverage
        assert coverage["95%"]["actual_coverage"] >= coverage["50%"]["actual_coverage"]
        # All coverages should be between 0 and 1
        for _level, info in coverage.items():
            assert 0 <= info["actual_coverage"] <= 1.0

    def test_metrics_computation(self):
        """compute_metrics should return MAPE, RMSE, R², MAE."""
        from ml.training.train_cost_model import compute_metrics

        y_true = np.array([100.0, 200.0, 300.0, 400.0])
        y_pred = np.array([110.0, 190.0, 310.0, 380.0])

        metrics = compute_metrics(y_true, y_pred)
        assert "mape" in metrics
        assert "rmse" in metrics
        assert "r2" in metrics
        assert "mae" in metrics
        assert metrics["mape"] > 0
        assert metrics["r2"] > 0.9  # Very close predictions
