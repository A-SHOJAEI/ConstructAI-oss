"""XGBoost-based parametric cost estimation for construction projects.

The model is trained via ``ml/training/train_cost_model.py`` and loaded
automatically from the model registry at ``models/cost_xgboost_v1.0/``.
When no trained model is available, falls back to a heuristic lookup
table calibrated to 2024 RSMeans data.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import logging
import math
import os
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

try:
    import numpy as np
    from xgboost import XGBRegressor

    _HAS_ML_DEPS = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    XGBRegressor = None  # type: ignore[assignment,misc]
    _HAS_ML_DEPS = False
    logger.warning("xgboost/numpy not installed; parametric model training unavailable")

try:
    import joblib

    _HAS_JOBLIB = True
except ImportError:  # pragma: no cover
    joblib = None  # type: ignore[assignment]
    _HAS_JOBLIB = False
    logger.warning("joblib not installed; model serialization unavailable")

# ---------------------------------------------------------------------------
# Model signature verification (HMAC-SHA256)
# ---------------------------------------------------------------------------

_MODEL_SIGNATURE_KEY: str | None = os.environ.get("MODEL_SIGNATURE_KEY")


def _get_signature_key() -> str:
    """Return the HMAC key for model signature verification."""
    global _MODEL_SIGNATURE_KEY
    if _MODEL_SIGNATURE_KEY:
        return _MODEL_SIGNATURE_KEY
    try:
        from app.config import settings

        _MODEL_SIGNATURE_KEY = settings.MODEL_SIGNATURE_KEY
    except Exception:
        logger.debug("Could not load MODEL_SIGNATURE_KEY from settings")
    return _MODEL_SIGNATURE_KEY or ""


def _compute_signature(file_bytes: bytes, key: str) -> str:
    """Compute HMAC-SHA256 signature of file bytes."""
    return hmac.new(key.encode(), file_bytes, hashlib.sha256).hexdigest()


def _verify_signature(file_path: Path) -> bool:
    """Verify HMAC-SHA256 signature of a model file.

    Looks for a ``.sig`` file alongside the model. If the signature key
    is not configured, verification is skipped with a warning.
    """
    key = _get_signature_key()
    if not key:
        from app.config import settings

        if settings.ENVIRONMENT in ("production", "staging"):
            raise RuntimeError("MODEL_SIGNATURE_KEY required in production")
        logger.warning("Signature verification skipped (dev only)")
        return True

    sig_path = Path(str(file_path) + ".sig")
    if not sig_path.exists():
        logger.error("Signature file missing for %s", file_path)
        return False

    file_bytes = file_path.read_bytes()
    expected_sig = sig_path.read_text().strip()
    computed_sig = _compute_signature(file_bytes, key)
    if not hmac.compare_digest(computed_sig, expected_sig):
        logger.error("Signature verification FAILED for %s", file_path)
        return False

    logger.debug("Signature verification passed for %s", file_path)
    return True


# ---------------------------------------------------------------------------
# Heuristic fallback cost tables (calibrated to 2024 RSMeans data)
# ---------------------------------------------------------------------------

BASE_COSTS_PSF: dict[str, Decimal] = {
    # Commercial
    "commercial_office": Decimal("285.00"),
    "commercial_retail": Decimal("225.00"),
    # Residential
    "residential_single_family": Decimal("165.00"),
    "residential_multi_family": Decimal("195.00"),
    "residential_luxury": Decimal("350.00"),
    # Industrial
    "industrial_warehouse": Decimal("130.00"),
    "industrial_manufacturing": Decimal("220.00"),
    # Infrastructure
    "infrastructure_bridge": Decimal("380.00"),
    "infrastructure_road": Decimal("95.00"),
    # Specialty
    "healthcare": Decimal("450.00"),
    "education_k12": Decimal("310.00"),
    "education_higher": Decimal("380.00"),
    "hospitality": Decimal("340.00"),
    "mixed_use": Decimal("275.00"),
}

# Backward-compatible aliases: legacy short names map to specific subtypes
_TYPE_ALIASES: dict[str, str] = {
    "commercial": "commercial_office",
    "residential": "residential_single_family",
    "industrial": "industrial_warehouse",
    "infrastructure": "infrastructure_bridge",
}

QUALITY_MULTIPLIERS: dict[str, Decimal] = {
    "economy": Decimal("0.75"),
    "low": Decimal("0.85"),
    "standard": Decimal("1.00"),
    "high": Decimal("1.25"),
    "premium": Decimal("1.50"),
    "ultra_premium": Decimal("1.85"),
}

REGION_FACTORS: dict[str, Decimal] = {
    "national": Decimal("1.00"),
    "northeast": Decimal("1.15"),
    "southeast": Decimal("0.90"),
    "midwest": Decimal("0.95"),
    "west": Decimal("1.10"),
    "northwest": Decimal("1.05"),
    "southwest": Decimal("0.92"),
    "mountain": Decimal("0.98"),
    "pacific": Decimal("1.18"),
}

# ---------------------------------------------------------------------------
# Project-type-specific confidence ranges for heuristic estimates
# ---------------------------------------------------------------------------

# Simple projects: +/- 12%
_SIMPLE_TYPES = frozenset(
    {
        "residential_single_family",
        "industrial_warehouse",
        "infrastructure_road",
    }
)

# Complex projects: +/- 25%
_COMPLEX_TYPES = frozenset(
    {
        "healthcare",
        "infrastructure_bridge",
        "residential_luxury",
    }
)

# Everything else: medium complexity +/- 18%

# ---------------------------------------------------------------------------
# v1.1 Feature encoding (matches train_cost_model.py)
# ---------------------------------------------------------------------------

_BUILDING_TYPES = [
    "commercial_office",
    "commercial_retail",
    "residential_single_family",
    "residential_multi_family",
    "residential_luxury",
    "industrial_warehouse",
    "industrial_manufacturing",
    "infrastructure_bridge",
    "infrastructure_road",
    "healthcare",
    "education_k12",
    "education_higher",
    "hospitality",
    "mixed_use",
]
_TYPE_INDEX = {t: i for i, t in enumerate(_BUILDING_TYPES)}

_QUALITY_ENCODING: dict[str, int] = {
    "economy": 0,
    "average": 1,
    "above_average": 2,
    "luxury": 3,
    # Map old quality names to new ordinal
    "low": 0,
    "standard": 1,
    "high": 2,
    "premium": 3,
    "ultra_premium": 3,
}

_PARKING_ENCODING: dict[str, int] = {
    "none": 0,
    "surface": 1,
    "structured": 2,
    "underground": 3,
}

# Legacy label encoding (for v1.0 models with 4-feature format)
_TYPE_ENCODING: dict[str, int] = {
    "commercial_office": 0,
    "commercial_retail": 1,
    "residential_single_family": 2,
    "residential_multi_family": 3,
    "residential_luxury": 4,
    "industrial_warehouse": 5,
    "industrial_manufacturing": 6,
    "infrastructure_bridge": 7,
    "infrastructure_road": 8,
    "healthcare": 9,
    "education_k12": 10,
    "education_higher": 11,
    "hospitality": 12,
    "mixed_use": 13,
    # Legacy aliases
    "commercial": 0,
    "residential": 2,
    "industrial": 5,
    "infrastructure": 7,
}

_REGION_ENCODING: dict[str, int] = {
    "national": 0,
    "northeast": 1,
    "southeast": 2,
    "midwest": 3,
    "west": 4,
    "northwest": 5,
    "southwest": 6,
    "mountain": 7,
    "pacific": 8,
}

# ---------------------------------------------------------------------------
# Inflation adjustment (ENR CCI-based)
# ---------------------------------------------------------------------------

_INFLATION_BASE_YEAR = 2024
# Updated annually. Override via PARAMETRIC_INFLATION_RATE env var.
_ANNUAL_INFLATION_RATE = Decimal(os.environ.get("PARAMETRIC_INFLATION_RATE", "0.045"))


def _inflation_factor(target_year: int) -> Decimal:
    """Return the cumulative compound inflation factor relative to the base year (2024).

    Formula: factor = (1 + rate) ^ (target_year - 2024)
    Uses compound interest rather than linear to avoid underestimation.
    """
    year_delta = target_year - _INFLATION_BASE_YEAR
    if year_delta == 0:
        return Decimal("1")
    base = Decimal("1") + _ANNUAL_INFLATION_RATE
    return base ** abs(year_delta) if year_delta > 0 else Decimal("1") / (base ** abs(year_delta))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_type(raw_type: str) -> str:
    """Resolve a project type string, following aliases for backward compat."""
    lowered = raw_type.lower()
    return _TYPE_ALIASES.get(lowered, lowered)


def _confidence_margins(resolved_type: str) -> tuple[Decimal, Decimal]:
    """Return (low_multiplier, high_multiplier) for the given project type."""
    if resolved_type in _SIMPLE_TYPES:
        return Decimal("0.88"), Decimal("1.12")
    if resolved_type in _COMPLEX_TYPES:
        return Decimal("0.75"), Decimal("1.25")
    # Medium complexity
    return Decimal("0.82"), Decimal("1.18")


def _encode_features_v1(records: list[dict]) -> tuple[Any, Any]:
    """Convert list of project dicts into numpy feature matrix (v1.0 format: 4 features)."""
    features = []
    targets = []

    for rec in records:
        sqft = float(rec.get("sqft", 0))
        stories = float(rec.get("stories", 1))
        resolved = _resolve_type(rec.get("type", "commercial"))
        proj_type = _TYPE_ENCODING.get(resolved, 0)
        region = _REGION_ENCODING.get(rec.get("region", "national"), 0)

        features.append([sqft, stories, proj_type, region])
        targets.append(float(rec.get("cost_per_sqft", 0)))

    return np.array(features), np.array(targets)  # type: ignore[union-attr]


def _encode_features_v11(params: dict) -> Any:
    """Encode a single project dict into v1.1 feature vector (24 features).

    Matches the encoding in ``ml/training/train_cost_model.py``.
    """
    features: list[float] = []

    # One-hot building type (14 columns)
    btype = _resolve_type(params.get("type", "commercial"))
    type_idx = _TYPE_INDEX.get(btype, 0)
    for i in range(len(_BUILDING_TYPES)):
        features.append(1.0 if i == type_idx else 0.0)

    # Continuous/ordinal features
    sqft = float(params.get("sqft", 10_000))
    features.append(math.log(max(sqft, 1)))  # log_gross_area_sf
    features.append(float(params.get("stories", 1)))  # num_stories
    quality = params.get("quality_level", "standard").lower()
    features.append(float(_QUALITY_ENCODING.get(quality, 1)))  # quality_encoded
    features.append(float(params.get("location_factor", 1.0)))  # location_factor
    features.append(float(params.get("construction_year", _INFLATION_BASE_YEAR)))  # estimate_year
    features.append(float(params.get("has_basement", 0)))  # has_basement
    features.append(float(params.get("has_elevator", 0)))  # has_elevator
    parking = params.get("parking_type", "none")
    features.append(float(_PARKING_ENCODING.get(parking, 0)))  # parking_encoded
    features.append(float(params.get("num_units", 0)))  # num_units
    features.append(float(params.get("climate_zone", 4)))  # climate_zone

    return np.array([features], dtype=np.float32)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Auto-discovery of trained model from registry
# ---------------------------------------------------------------------------


# Search paths for the trained model (checked in order). Some deployments
# (e.g. the Docker image where the file lives at /app/app/services/...) have
# fewer parent directories than the source tree, so guard each path lookup
# with try/except — an IndexError here would crash the whole module import.
def _candidate_model_path(parent_index: int, *parts: str) -> Path | None:
    try:
        return Path(__file__).resolve().parents[parent_index].joinpath(*parts)
    except IndexError:
        return None


_MODEL_REGISTRY_PATHS: list[Path] = [
    p
    for p in (
        # Relative to the API app directory (source tree)
        _candidate_model_path(4, "models", "cost_xgboost_v1.0", "best_model.joblib"),
        # Relative to the monorepo root (source tree)
        _candidate_model_path(5, "models", "cost_xgboost_v1.0", "best_model.joblib"),
        # Relative to ml/ training output
        _candidate_model_path(5, "ml", "models", "cost_xgboost_v1.0", "best_model.joblib"),
        # Container layout: /app/models/...
        _candidate_model_path(2, "models", "cost_xgboost_v1.0", "best_model.joblib"),
    )
    if p is not None
]

# Environment variable override
_MODEL_PATH_ENV = "COST_MODEL_PATH"

# Cached loaded model (singleton, loaded on first use)
_cached_model: dict | None = None
_model_load_attempted: bool = False


def _discover_model() -> dict | None:
    """Try to auto-discover and load the trained cost model.

    Returns the loaded model dict or None if no model is found.
    """
    global _cached_model, _model_load_attempted

    if _model_load_attempted:
        return _cached_model

    _model_load_attempted = True

    if not _HAS_JOBLIB or not _HAS_ML_DEPS:
        logger.debug("ML dependencies not available; skipping model discovery")
        return None

    # Check environment variable first.
    # L-29: Constrain to the models root (same jail as C-3). Prevents a
    # malicious/misconfigured env var from pointing at an arbitrary path
    # (e.g. /etc/shadow, /tmp/attacker.pkl). Accept either the monorepo
    # root (parents[5]/models) or the apps/ neighbour (parents[4]/models)
    # — the registry paths above check both, so the jail must too.
    env_path = os.environ.get(_MODEL_PATH_ENV)
    if env_path:
        path: Path | None = None
        candidate_roots = [
            (Path(__file__).resolve().parents[5] / "models").resolve(),
            (Path(__file__).resolve().parents[4] / "models").resolve(),
        ]
        for models_root in candidate_roots:
            try:
                raw = Path(env_path)
                candidate = (raw if raw.is_absolute() else (models_root.parent / raw)).resolve()
                candidate.relative_to(models_root)
                path = candidate
                break
            except (ValueError, OSError):
                continue
        if path is None:
            logger.error(
                "Rejecting %s=%r: path is outside the models/ jail", _MODEL_PATH_ENV, env_path
            )
        if path is not None and path.exists():
            if not _verify_signature(path):
                logger.error("Rejecting model at %s: signature verification failed", path)
            else:
                try:
                    _cached_model = joblib.load(path)  # type: ignore[union-attr]
                    logger.info("Loaded cost model from env var: %s", path)
                    return _cached_model
                except Exception:
                    logger.exception("Failed to load cost model from %s", path)

    # Check registry paths
    for path in _MODEL_REGISTRY_PATHS:
        if path.exists():
            if not _verify_signature(path):
                logger.error("Rejecting model at %s: signature verification failed", path)
                continue
            try:
                _cached_model = joblib.load(path)  # type: ignore[union-attr]
                logger.info("Loaded cost model from registry: %s", path)
                return _cached_model
            except Exception:
                logger.exception("Failed to load cost model from %s", path)
                continue

    logger.info("No trained cost model found; will use heuristic fallback")
    return None


def _detect_model_version(loaded: dict) -> str:
    """Detect whether a loaded model uses v1.0 (4-feature) or v1.1 (24-feature) encoding."""
    feature_names = loaded.get("feature_names", [])
    if len(feature_names) > 10:
        return "v1.1"
    return "v1.0"


# ---------------------------------------------------------------------------
# Model serialization helpers
# ---------------------------------------------------------------------------

_FEATURE_NAMES = ["sqft", "stories", "type", "region"]


def save_model(model: Any, path: str) -> str:
    """Serialize a trained XGBoost model + metadata to disk using joblib.

    Parameters
    ----------
    model:
        A dict containing at minimum ``"model"`` (the fitted XGBRegressor)
        and optional metadata keys like ``"feature_names"``,
        ``"training_date"``, and ``"metrics"``.
    path:
        Filesystem path where the model artifact will be written.

    Returns
    -------
    The absolute path to the saved model file.
    """
    if not _HAS_JOBLIB:
        raise RuntimeError(
            "joblib is required for model serialization. Install with: pip install joblib"
        )

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    joblib.dump(model, path)  # type: ignore[union-attr]

    # Write HMAC-SHA256 signature file
    key = _get_signature_key()
    if key:
        file_bytes = Path(path).read_bytes()
        sig = _compute_signature(file_bytes, key)
        sig_path = path + ".sig"
        Path(sig_path).write_text(sig)
        logger.info("Saved model signature to %s", sig_path)
    else:
        logger.warning("MODEL_SIGNATURE_KEY not set; model saved without signature")

    logger.info("Saved parametric model to %s", path)
    return os.path.abspath(path)


def load_model(path: str) -> dict:
    """Load a serialized model dict from disk.

    Verifies HMAC-SHA256 signature before loading.

    Returns
    -------
    The model dict previously saved via :func:`save_model`.
    """
    if not _HAS_JOBLIB:
        raise RuntimeError(
            "joblib is required for model deserialization. Install with: pip install joblib"
        )

    if not _verify_signature(Path(path)):
        raise ValueError(f"Model signature verification failed for {path}")

    data = joblib.load(path)  # type: ignore[union-attr]
    logger.info("Loaded parametric model from %s", path)
    return data


def _load_model_from_bytes(raw: bytes) -> dict:
    """Deserialize a model dict from raw bytes (as produced by joblib.dump to a BytesIO).

    **INTERNAL ONLY** -- Never pass user-supplied bytes to this function.
    This is intended solely for loading model bytes from trusted internal
    sources (e.g., model registry database, verified S3 artifacts).

    HMAC-SHA256 verification is performed if a signature key is configured.
    """
    if not _HAS_JOBLIB:
        raise RuntimeError(
            "joblib is required for model deserialization. Install with: pip install joblib"
        )

    # Verify HMAC signature for bytes if key is available
    key = _get_signature_key()
    if key:
        logger.warning(
            "_load_model_from_bytes called without file-level signature verification; "
            "ensure caller is internal and data is from a trusted source"
        )

    buf = io.BytesIO(raw)
    data = joblib.load(buf)  # type: ignore[union-attr]
    return data


def reload_model() -> dict | None:
    """Force re-discovery of the trained model (e.g., after deploying a new version)."""
    global _cached_model, _model_load_attempted
    _cached_model = None
    _model_load_attempted = False
    return _discover_model()


# ---------------------------------------------------------------------------
# Prediction logging (for continuous improvement)
# ---------------------------------------------------------------------------

from collections import deque  # noqa: E402  (grouped with this logical section)

_MAX_LOG_SIZE = 1000
_prediction_log: deque[dict] = deque(maxlen=_MAX_LOG_SIZE)


def _log_prediction(params: dict, result: dict) -> None:
    """Log a prediction for future model improvement.

    Stores the last N predictions in a bounded deque. In production, these
    would be flushed to a database or file for retraining.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "org_id": params.get("org_id"),
        "input": {
            "type": params.get("type"),
            "sqft": params.get("sqft"),
            "stories": params.get("stories"),
            "quality_level": params.get("quality_level"),
            "region": params.get("region"),
            "construction_year": params.get("construction_year"),
        },
        "output": {
            "cost_per_sqft": float(result["predicted_cost_per_sqft"]),
            "total_cost": float(result["total_predicted_cost"]),
            "model_used": result["model_used"],
        },
    }
    _prediction_log.append(entry)


def get_prediction_log(org_id: str | None = None) -> list[dict]:
    """Return recent predictions for analysis, scoped by organization.

    Args:
        org_id: When provided, only predictions for this organization
            are returned.  This prevents cross-org financial data leakage.
            When ``None``, all predictions are returned (admin use only).
    """
    if org_id is None:
        return list(_prediction_log)
    return [entry for entry in _prediction_log if entry.get("org_id") == org_id]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def train_parametric_model(training_data: list[dict]) -> dict:
    """Train an XGBoost regression model on historical project data.

    training_data: list of dicts with features (sqft, stories, type, region)
    and target (cost_per_sqft).
    Returns dict with: model_id, r2_score, rmse, feature_importance, trained_at,
    and the fitted ``model`` object for downstream serialization.
    """
    if not _HAS_ML_DEPS:
        raise RuntimeError(
            "xgboost and numpy are required for model training. "
            "Install with: pip install xgboost numpy"
        )

    if len(training_data) < 5:
        raise ValueError("At least 5 training records required")

    features, targets = _encode_features_v1(training_data)

    # 80/20 train/test split
    split_idx = int(len(features) * 0.8)
    feat_train, feat_test = features[:split_idx], features[split_idx:]
    y_train, y_test = targets[:split_idx], targets[split_idx:]

    model = XGBRegressor(  # type: ignore[misc]
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        random_state=42,
    )
    model.fit(feat_train, y_train)

    # Evaluate
    y_pred = model.predict(feat_test)
    ss_res = float(np.sum((y_test - y_pred) ** 2))  # type: ignore[union-attr]
    ss_tot = float(np.sum((y_test - np.mean(y_test)) ** 2))  # type: ignore[union-attr]
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    rmse = float(np.sqrt(np.mean((y_test - y_pred) ** 2)))  # type: ignore[union-attr]

    importance = model.feature_importances_
    feature_importance = {
        name: round(float(imp), 4) for name, imp in zip(_FEATURE_NAMES, importance, strict=True)
    }

    # Compute residuals on test set for prediction interval estimation
    residuals = y_test - y_pred
    residual_std = float(np.std(residuals)) if len(residuals) > 1 else 0.0  # type: ignore[union-attr]

    model_id = str(uuid4())
    trained_at = datetime.now(UTC).isoformat()

    logger.info(
        "Trained parametric model %s: R2=%.3f RMSE=%.2f",
        model_id,
        r2,
        rmse,
    )

    return {
        "model_id": model_id,
        "model": model,
        "r2_score": round(r2, 4),
        "rmse": round(rmse, 2),
        "residual_std": round(residual_std, 4),
        "feature_importance": feature_importance,
        "feature_names": list(_FEATURE_NAMES),
        "trained_at": trained_at,
    }


async def save_trained_model(model: dict, model_id: str, output_dir: str) -> str:
    """Serialize a model returned by :func:`train_parametric_model` to disk.

    Parameters
    ----------
    model:
        The dict returned by ``train_parametric_model``.
    model_id:
        A unique identifier used as the filename stem.
    output_dir:
        Directory where the model file will be written.

    Returns
    -------
    The absolute path to the saved model file.
    """
    filename = f"parametric_{model_id}.joblib"
    path = os.path.join(output_dir, filename)

    payload = {
        "model": model["model"],
        "feature_names": model.get("feature_names", list(_FEATURE_NAMES)),
        "training_date": model.get("trained_at", datetime.now(UTC).isoformat()),
        "metrics": {
            "r2_score": model.get("r2_score"),
            "rmse": model.get("rmse"),
            "residual_std": model.get("residual_std"),
        },
        "model_id": model.get("model_id", model_id),
    }

    saved_path = save_model(payload, path)
    logger.info("Saved trained model %s to %s", model_id, saved_path)
    return saved_path


async def predict_cost(project_params: dict, model_data: dict | None = None) -> dict:
    """Predict project cost using parametric model.

    project_params: dict with sqft, stories, type, region, quality_level,
    and optionally construction_year (int, default 2024).

    Accepts v1.1 features (has_basement, has_elevator, parking_type,
    num_units, climate_zone, location_factor) when a v1.1 model is loaded.

    model_data: optional dict with either ``"model_bytes"`` (bytes) or
    ``"model_path"`` (str) to load a trained XGBoost model.  When None,
    auto-discovers the model from the registry.

    Returns dict with: predicted_cost_per_sqft, total_predicted_cost,
    confidence_interval, prediction_intervals, model_used, model_available.

    All cost calculations use ``Decimal`` to avoid floating-point
    rounding errors common with currency values.
    """
    TWO_PLACES = Decimal("0.01")

    sqft = Decimal(str(project_params.get("sqft", 0)))
    if sqft <= 0:
        raise ValueError("gross_area (sqft) must be greater than 0")

    stories = int(project_params.get("stories", 1))
    if stories < 1:
        raise ValueError("num_stories must be >= 1")
    if stories > 200:
        raise ValueError("num_stories must be 200 or fewer")

    raw_type = project_params.get("type", "commercial")
    proj_type = _resolve_type(raw_type)
    region = project_params.get("region", "national").lower()

    quality = project_params.get("quality_level", "standard").lower()
    valid_quality_levels = {"economy", "standard", "premium", "luxury", "ultra_premium"}
    if quality not in valid_quality_levels:
        raise ValueError(
            f"quality_level must be one of {sorted(valid_quality_levels)}, got '{quality}'"
        )

    location_factor = project_params.get("location_factor")
    if location_factor is not None and float(location_factor) <= 0:
        raise ValueError("location_factor must be > 0")

    construction_year = int(project_params.get("construction_year", _INFLATION_BASE_YEAR))

    # Attempt ML model prediction
    ml_prediction = None
    ml_residual_std = None
    model_available = False

    # Auto-discover model from registry if no explicit model_data
    if model_data is None:
        auto_model = _discover_model()
        if auto_model is not None:
            model_data = {"_loaded": auto_model}

    if model_data is not None and _HAS_ML_DEPS and _HAS_JOBLIB:
        loaded = None
        try:
            if "_loaded" in model_data:
                loaded = model_data["_loaded"]
            elif "model_bytes" in model_data and isinstance(model_data["model_bytes"], bytes):
                loaded = _load_model_from_bytes(model_data["model_bytes"])
                logger.debug("Deserialized model from bytes")
            elif "model_path" in model_data and isinstance(model_data["model_path"], str):
                loaded = load_model(model_data["model_path"])
                logger.debug("Loaded model from path: %s", model_data["model_path"])
        except Exception:
            logger.exception("Failed to load ML model; falling back to heuristic")
            loaded = None

        if loaded is not None:
            try:
                xgb_model = loaded["model"]
                model_version = _detect_model_version(loaded)

                if model_version == "v1.1":
                    # v1.1: 24-feature one-hot encoding
                    feature_vec = _encode_features_v11(project_params)
                else:
                    # v1.0: 4-feature label encoding
                    type_encoded = _TYPE_ENCODING.get(proj_type, 0)
                    region_encoded = _REGION_ENCODING.get(region, 0)
                    feature_vec = np.array(
                        [[float(sqft), float(stories), type_encoded, region_encoded]]
                    )  # type: ignore[union-attr]

                raw_pred = float(xgb_model.predict(feature_vec)[0])

                if raw_pred > 0:
                    ml_prediction = Decimal(str(round(raw_pred, 2)))
                    model_available = True
                    # Extract residual_std for prediction intervals
                    # v1.0 models store it in metrics.residual_std
                    # v1.1 models store it at top level
                    residual_std_val = loaded.get("residual_std")
                    if residual_std_val is None:
                        metrics = loaded.get("metrics", {})
                        residual_std_val = metrics.get("residual_std") if metrics else None
                    if residual_std_val is not None:
                        ml_residual_std = float(residual_std_val)
                    logger.debug("ML model (%s) predicted $%.2f/sqft", model_version, raw_pred)
                else:
                    logger.warning(
                        "ML model returned non-positive prediction (%.2f); falling back", raw_pred
                    )
            except Exception:
                logger.exception("ML model prediction failed; falling back to heuristic")

    # --- ML path: use model prediction ---
    if ml_prediction is not None:
        quality_multiplier = QUALITY_MULTIPLIERS.get(quality, Decimal("1.00"))
        region_factor = REGION_FACTORS.get(region, Decimal("1.00"))
        inflation = _inflation_factor(construction_year)

        # The ML model predicts base cost_per_sqft; apply quality, region, inflation on top
        predicted_psf = (ml_prediction * quality_multiplier * region_factor * inflation).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        total_cost = (predicted_psf * sqft).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Confidence interval from model residuals (approx 95% CI = +/- 1.96 * std)
        if ml_residual_std is not None and ml_residual_std > 0:
            margin = Decimal(str(round(1.96 * ml_residual_std, 2)))
            # Scale margin by same multipliers applied to the prediction
            scaled_margin = (
                margin * quality_multiplier * region_factor * inflation * sqft
            ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            ci_low = (total_cost - scaled_margin).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            ci_high = (total_cost + scaled_margin).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            # Floor the low end at zero
            if ci_low < Decimal("0"):
                ci_low = Decimal("0.00")
        else:
            # No residual info; use heuristic-style margins based on type
            low_mult, high_mult = _confidence_margins(proj_type)
            ci_low = (total_cost * low_mult).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            ci_high = (total_cost * high_mult).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Build multi-level prediction intervals from quantile errors
        prediction_intervals = {}
        if ml_residual_std is not None and ml_residual_std > 0:
            z_map = {"50%": 0.674, "80%": 1.282, "90%": 1.645, "95%": 1.960}
            for level, z in z_map.items():
                m = Decimal(str(round(z * ml_residual_std, 2)))
                m_scaled = (m * quality_multiplier * region_factor * inflation).quantize(
                    TWO_PLACES,
                    rounding=ROUND_HALF_UP,
                )
                pi_low = max(predicted_psf - m_scaled, Decimal("0"))
                pi_high = predicted_psf + m_scaled
                prediction_intervals[level] = {
                    "low_per_sf": pi_low,
                    "high_per_sf": pi_high,
                    "low_total": max(total_cost - m_scaled * sqft, Decimal("0")).quantize(
                        TWO_PLACES,
                        rounding=ROUND_HALF_UP,
                    ),
                    "high_total": (total_cost + m_scaled * sqft).quantize(
                        TWO_PLACES,
                        rounding=ROUND_HALF_UP,
                    ),
                }

        logger.info(
            "ML parametric prediction: type=%s sqft=%s -> $%s/sqft ($%s total)",
            proj_type,
            sqft,
            predicted_psf,
            total_cost,
        )

        result = {
            "predicted_cost_per_sqft": predicted_psf,
            "total_predicted_cost": total_cost,
            "confidence_interval": {"low": ci_low, "high": ci_high},
            "prediction_intervals": prediction_intervals,
            "model_used": "xgboost",
            "model_available": True,
        }
        _log_prediction(project_params, result)
        return result

    # --- Heuristic fallback ---
    base_psf = BASE_COSTS_PSF.get(proj_type, Decimal("250.00"))

    # Story multiplier: 1.05 for each story above 3, capped at 20 stories
    # (high-rise premium plateaus due to economies of scale in vertical construction)
    story_multiplier = Decimal("1.00")
    if stories > 3:
        effective_stories = min(stories - 3, 17)  # Cap at 20 stories (17 above 3)
        story_multiplier = Decimal("1.05") ** effective_stories

    quality_multiplier = QUALITY_MULTIPLIERS.get(quality, Decimal("1.00"))
    region_factor = REGION_FACTORS.get(region, Decimal("1.00"))
    inflation = _inflation_factor(construction_year)

    predicted_psf = (
        base_psf * story_multiplier * quality_multiplier * region_factor * inflation
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total_cost = (predicted_psf * sqft).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Type-specific confidence interval
    low_mult, high_mult = _confidence_margins(proj_type)
    ci_low = (total_cost * low_mult).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    ci_high = (total_cost * high_mult).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    logger.info(
        "Heuristic parametric prediction: type=%s sqft=%s -> $%s/sqft ($%s total)",
        proj_type,
        sqft,
        predicted_psf,
        total_cost,
    )

    result = {
        "predicted_cost_per_sqft": predicted_psf,
        "total_predicted_cost": total_cost,
        "confidence_interval": {"low": ci_low, "high": ci_high},
        "prediction_intervals": {},
        "model_used": "heuristic",
        "model_available": model_available,
    }
    _log_prediction(project_params, result)
    return result
