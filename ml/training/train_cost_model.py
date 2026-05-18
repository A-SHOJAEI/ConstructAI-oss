#!/usr/bin/env python3
"""Train XGBoost parametric cost model with Optuna hyperparameter optimization.

Loads synthetic training data (or real project data), engineers features,
tunes hyperparameters via Bayesian optimization, evaluates with 5-fold CV,
and saves the best model to the model registry.

Usage:
    # Generate data + train in one go
    python -m ml.training.train_cost_model \
        --generate --num-samples 50000 \
        --output-dir models/cost_xgboost_v1.0

    # Train from existing data file
    python -m ml.training.train_cost_model \
        --data-file constructai-data/cost-data/training/cost_training_data.json \
        --output-dir models/cost_xgboost_v1.0

    # Quick test run (small data, few trials)
    python -m ml.training.train_cost_model \
        --generate --num-samples 5000 --optuna-trials 10 \
        --output-dir models/cost_xgboost_v1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

# One-hot encoded building types (14 types)
BUILDING_TYPES = [
    "commercial_office", "commercial_retail",
    "residential_single_family", "residential_multi_family", "residential_luxury",
    "industrial_warehouse", "industrial_manufacturing",
    "infrastructure_bridge", "infrastructure_road",
    "healthcare", "education_k12", "education_higher",
    "hospitality", "mixed_use",
]
_TYPE_INDEX = {t: i for i, t in enumerate(BUILDING_TYPES)}

# Ordinal quality encoding
_QUALITY_MAP = {"economy": 0, "average": 1, "above_average": 2, "luxury": 3}

# Parking encoding
_PARKING_MAP = {"none": 0, "surface": 1, "structured": 2, "underground": 3}

# Full feature name list (for model metadata and importance tracking)
FEATURE_NAMES = (
    # Building type one-hot (14 columns)
    [f"type_{t}" for t in BUILDING_TYPES]
    # Continuous features
    + [
        "log_gross_area_sf",    # log-transformed area
        "num_stories",
        "quality_encoded",      # ordinal 0-3
        "location_factor",      # composite regional factor
        "estimate_year",
        "has_basement",         # 0/1
        "has_elevator",         # 0/1
        "parking_encoded",      # ordinal 0-3
        "num_units",
        "climate_zone",         # 1-8
    ]
)


def _encode_record(rec: dict) -> list[float]:
    """Convert a single data record to a feature vector."""
    features: list[float] = []

    # One-hot for building type
    btype = rec.get("building_type", "commercial_office")
    type_idx = _TYPE_INDEX.get(btype, 0)
    for i in range(len(BUILDING_TYPES)):
        features.append(1.0 if i == type_idx else 0.0)

    # Continuous/ordinal features
    features.append(float(rec.get("log_gross_area_sf", math.log(10_000))))
    features.append(float(rec.get("num_stories", 1)))
    features.append(float(_QUALITY_MAP.get(rec.get("quality_level", "average"), 1)))
    features.append(float(rec.get("location_factor", 1.0)))
    features.append(float(rec.get("estimate_year", 2024)))
    features.append(float(rec.get("has_basement", 0)))
    features.append(float(rec.get("has_elevator", 0)))
    features.append(float(_PARKING_MAP.get(rec.get("parking_type", "none"), 0)))
    features.append(float(rec.get("num_units", 0)))
    features.append(float(rec.get("climate_zone", 4)))

    return features


def encode_dataset(records: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Convert a list of data records to feature matrix + target vector.

    Returns (X, y) where y is cost_per_sf.
    """
    X = np.array([_encode_record(r) for r in records], dtype=np.float32)
    y = np.array([float(r.get("cost_per_sf", 0)) for r in records], dtype=np.float32)
    return X, y


# ---------------------------------------------------------------------------
# Optuna hyperparameter optimization
# ---------------------------------------------------------------------------


def _optuna_objective(
    trial,
    X_train: np.ndarray,
    y_train: np.ndarray,
    n_folds: int = 5,
) -> float:
    """Optuna objective: minimize 5-fold CV MAPE."""
    from sklearn.model_selection import KFold
    from xgboost import XGBRegressor

    params = {
        "n_estimators": trial.suggest_int("n_estimators", 100, 800, step=50),
        "max_depth": trial.suggest_int("max_depth", 3, 10),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        "gamma": trial.suggest_float("gamma", 0.0, 5.0),
        "random_state": 42,
        "tree_method": "hist",
    }

    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
    mapes = []

    for train_idx, val_idx in kf.split(X_train):
        X_tr, X_val = X_train[train_idx], X_train[val_idx]
        y_tr, y_val = y_train[train_idx], y_train[val_idx]

        model = XGBRegressor(**params)
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        y_pred = model.predict(X_val)
        # MAPE (avoid division by zero)
        mask = y_val > 0
        if mask.sum() > 0:
            mape = np.mean(np.abs((y_val[mask] - y_pred[mask]) / y_val[mask])) * 100
            mapes.append(mape)

    return float(np.mean(mapes))


def run_optuna(
    X_train: np.ndarray,
    y_train: np.ndarray,
    n_trials: int = 50,
    n_folds: int = 5,
) -> dict:
    """Run Bayesian hyperparameter optimization via Optuna.

    Returns the best hyperparameter dict.
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        direction="minimize",
        study_name="cost_model_hpo",
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    study.optimize(
        lambda trial: _optuna_objective(trial, X_train, y_train, n_folds),
        n_trials=n_trials,
        show_progress_bar=True,
    )

    logger.info(
        "Optuna best trial: MAPE=%.2f%% (trial %d/%d)",
        study.best_value, study.best_trial.number + 1, n_trials,
    )

    return {
        "best_params": study.best_params,
        "best_mape": round(study.best_value, 4),
        "n_trials": n_trials,
        "n_folds": n_folds,
    }


# ---------------------------------------------------------------------------
# Training + evaluation
# ---------------------------------------------------------------------------


def train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    params: dict | None = None,
) -> dict:
    """Train XGBoost with given params, evaluate on test set.

    Returns dict with model, metrics, feature importance.
    """
    from xgboost import XGBRegressor

    if params is None:
        params = {
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 3,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "gamma": 0.1,
        }

    model = XGBRegressor(
        **params,
        random_state=42,
        tree_method="hist",
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Predictions
    y_pred = model.predict(X_test)

    # Metrics
    metrics = compute_metrics(y_test, y_pred)

    # Feature importance
    importance = model.feature_importances_
    feature_importance = {}
    for name, imp in zip(FEATURE_NAMES, importance, strict=False):
        feature_importance[name] = round(float(imp), 4)
    # Sort by importance descending
    feature_importance = dict(
        sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)
    )

    # Residuals for prediction intervals
    residuals = y_test - y_pred
    residual_std = float(np.std(residuals))

    # Per-quantile residual analysis for better prediction intervals
    abs_pct_errors = np.abs(residuals / np.where(y_test > 0, y_test, 1.0))
    quantile_errors = {
        "p50": round(float(np.percentile(abs_pct_errors, 50)) * 100, 2),
        "p80": round(float(np.percentile(abs_pct_errors, 80)) * 100, 2),
        "p90": round(float(np.percentile(abs_pct_errors, 90)) * 100, 2),
        "p95": round(float(np.percentile(abs_pct_errors, 95)) * 100, 2),
    }

    model_id = str(uuid4())

    return {
        "model_id": model_id,
        "model": model,
        "metrics": metrics,
        "residual_std": round(residual_std, 4),
        "quantile_errors": quantile_errors,
        "feature_importance": feature_importance,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(UTC).isoformat(),
    }


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Compute regression metrics: MAPE, RMSE, R², MAE."""
    mask = y_true > 0
    if mask.sum() == 0:
        return {"mape": 0.0, "rmse": 0.0, "r2": 0.0, "mae": 0.0}

    y_t, y_p = y_true[mask], y_pred[mask]

    mape = float(np.mean(np.abs((y_t - y_p) / y_t))) * 100
    rmse = float(np.sqrt(np.mean((y_t - y_p) ** 2)))
    mae = float(np.mean(np.abs(y_t - y_p)))

    ss_res = float(np.sum((y_t - y_p) ** 2))
    ss_tot = float(np.sum((y_t - np.mean(y_t)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "mape": round(mape, 2),
        "rmse": round(rmse, 2),
        "r2": round(r2, 4),
        "mae": round(mae, 2),
    }


def evaluate_prediction_intervals(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    residual_std: float,
    coverage_levels: list[float] | None = None,
) -> dict:
    """Evaluate prediction interval coverage at multiple confidence levels.

    Returns coverage (fraction of true values within predicted interval)
    at each confidence level.
    """
    if coverage_levels is None:
        coverage_levels = [0.50, 0.80, 0.90, 0.95]

    y_pred = model.predict(X_test)
    results = {}

    for level in coverage_levels:
        z = {0.50: 0.674, 0.80: 1.282, 0.90: 1.645, 0.95: 1.960}.get(level, 1.960)
        margin = z * residual_std
        in_interval = np.sum((y_test >= y_pred - margin) & (y_test <= y_pred + margin))
        coverage = float(in_interval) / len(y_test)
        results[f"{int(level*100)}%"] = {
            "target_coverage": level,
            "actual_coverage": round(coverage, 4),
            "margin_psf": round(margin, 2),
            "calibrated": abs(coverage - level) < 0.05,
        }

    return results


# ---------------------------------------------------------------------------
# Model registry save
# ---------------------------------------------------------------------------


def save_to_registry(
    result: dict,
    output_dir: str,
    optuna_result: dict | None = None,
    dataset_stats: dict | None = None,
) -> Path:
    """Save trained model + metadata to the model registry.

    Creates:
        output_dir/
            best_model.joblib       — serialized XGBoost model
            metadata.json           — training config, metrics, feature importance
            feature_names.txt       — one feature name per line
    """
    import joblib

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    model = result["model"]
    model_id = result["model_id"]

    # Save model
    model_path = out / "best_model.joblib"
    payload = {
        "model": model,
        "feature_names": result["feature_names"],
        "training_date": result["trained_at"],
        "metrics": result["metrics"],
        "residual_std": result["residual_std"],
        "quantile_errors": result["quantile_errors"],
        "model_id": model_id,
    }
    joblib.dump(payload, model_path)
    logger.info("Saved model to %s", model_path)

    # Save feature names
    feat_path = out / "feature_names.txt"
    feat_path.write_text("\n".join(result["feature_names"]) + "\n")

    # Save metadata
    metadata = {
        "model_id": model_id,
        "model_type": "xgboost",
        "version": "1.0",
        "task": "parametric_cost_estimation",
        "target": "cost_per_sf",
        "trained_at": result["trained_at"],
        "metrics": result["metrics"],
        "residual_std": result["residual_std"],
        "quantile_errors": result["quantile_errors"],
        "feature_importance": result["feature_importance"],
        "feature_names": result["feature_names"],
        "num_features": len(result["feature_names"]),
        "building_types": BUILDING_TYPES,
    }
    if optuna_result:
        metadata["hyperparameters"] = optuna_result["best_params"]
        metadata["optuna"] = {
            "best_mape": optuna_result["best_mape"],
            "n_trials": optuna_result["n_trials"],
        }
    if dataset_stats:
        metadata["dataset"] = dataset_stats

    meta_path = out / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    logger.info("Saved metadata to %s", meta_path)

    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_training_pipeline(
    data_file: str | None = None,
    generate: bool = False,
    num_samples: int = 50_000,
    output_dir: str = "models/cost_xgboost_v1.0",
    optuna_trials: int = 50,
    seed: int = 42,
    test_fraction: float = 0.15,
) -> dict:
    """End-to-end training pipeline.

    Returns dict with metrics, model_id, and output path.
    """
    start_time = time.time()

    # Step 1: Load or generate data
    if generate:
        from ml.data.generate_cost_training_data import generate_samples
        logger.info("Generating %d synthetic samples...", num_samples)
        samples = generate_samples(num_samples=num_samples, seed=seed)
        records = []
        for s in samples:
            from dataclasses import asdict
            records.append(asdict(s))
    elif data_file:
        logger.info("Loading data from %s", data_file)
        with open(data_file) as f:
            data = json.load(f)
        records = data["data"] if "data" in data else data
    else:
        raise ValueError("Provide either --data-file or --generate")

    logger.info("Loaded %d records", len(records))

    # CRITICAL: Exclude Procore-sourced records (TOS compliance)
    if records and isinstance(records[0], dict) and "data_source" in records[0]:
        pre_count = len(records)
        records = [r for r in records if r.get("data_source") != "procore"]
        procore_count = pre_count - len(records)
        if procore_count > 0:
            logger.warning(f"Filtering {procore_count} Procore-sourced records from training data")

    # Step 2: Encode features
    X, y = encode_dataset(records)
    logger.info("Feature matrix shape: %s, target shape: %s", X.shape, y.shape)

    # Filter out zero/negative costs
    valid_mask = y > 0
    X, y = X[valid_mask], y[valid_mask]
    logger.info("After filtering: %d valid samples", len(y))

    # Step 3: Train/test split (stratified by building type)
    rng = np.random.default_rng(seed)
    n_test = int(len(X) * test_fraction)
    indices = np.arange(len(X))
    rng.shuffle(indices)
    test_idx = indices[:n_test]
    train_idx = indices[n_test:]
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    logger.info("Train: %d, Test: %d", len(X_train), len(X_test))

    # Step 4: Hyperparameter optimization
    logger.info("Running Optuna (%d trials, 5-fold CV)...", optuna_trials)
    optuna_result = run_optuna(
        X_train, y_train,
        n_trials=optuna_trials,
        n_folds=5,
    )
    best_params = optuna_result["best_params"]
    logger.info("Best params: %s", best_params)

    # Step 5: Train final model with best params
    logger.info("Training final model with best hyperparameters...")
    result = train_model(X_train, y_train, X_test, y_test, params=best_params)

    # Step 6: Prediction interval evaluation
    logger.info("Evaluating prediction intervals...")
    interval_coverage = evaluate_prediction_intervals(
        result["model"], X_test, y_test, result["residual_std"],
    )
    result["prediction_interval_coverage"] = interval_coverage

    # Step 7: Dataset stats
    from collections import Counter
    type_counts = Counter(r.get("building_type", "unknown") for r in records)
    dataset_stats = {
        "total_samples": len(records),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "type_distribution": dict(type_counts.most_common()),
        "target_stats": {
            "mean": round(float(y.mean()), 2),
            "std": round(float(y.std()), 2),
            "min": round(float(y.min()), 2),
            "max": round(float(y.max()), 2),
            "median": round(float(np.median(y)), 2),
        },
    }

    # Step 8: Save to registry
    logger.info("Saving model to %s", output_dir)
    save_to_registry(result, output_dir, optuna_result, dataset_stats)

    elapsed = time.time() - start_time

    # Print results
    m = result["metrics"]
    print("\n" + "=" * 60)
    print("  COST MODEL TRAINING COMPLETE")
    print("=" * 60)
    print(f"  Model ID:     {result['model_id']}")
    print(f"  Samples:      {len(records):,} ({len(X_train):,} train / {len(X_test):,} test)")
    print(f"  MAPE:         {m['mape']:.2f}%")
    print(f"  RMSE:         ${m['rmse']:.2f}/SF")
    print(f"  R²:           {m['r2']:.4f}")
    print(f"  MAE:          ${m['mae']:.2f}/SF")
    print(f"  Residual Std: ${result['residual_std']:.2f}/SF")
    print()
    print("  Prediction Interval Coverage:")
    for level, info in interval_coverage.items():
        status = "OK" if info["calibrated"] else "MISCALIBRATED"
        print(f"    {level}: {info['actual_coverage']:.1%} ({status})")
    print()
    print("  Top 10 Features:")
    for name, imp in list(result["feature_importance"].items())[:10]:
        print(f"    {name:30s} {imp:.4f}")
    print()
    print(f"  Optuna:       {optuna_trials} trials, best MAPE={optuna_result['best_mape']:.2f}%")
    print(f"  Time:         {elapsed:.1f}s")
    print(f"  Output:       {output_dir}")
    print("=" * 60)

    return {
        "model_id": result["model_id"],
        "metrics": m,
        "residual_std": result["residual_std"],
        "quantile_errors": result["quantile_errors"],
        "prediction_interval_coverage": interval_coverage,
        "feature_importance": result["feature_importance"],
        "output_dir": output_dir,
        "elapsed_seconds": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train XGBoost parametric cost model",
    )
    parser.add_argument(
        "--data-file",
        help="Path to training data JSON file",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate synthetic training data instead of loading from file",
    )
    parser.add_argument(
        "--num-samples", type=int, default=50_000,
        help="Number of synthetic samples (only with --generate)",
    )
    parser.add_argument(
        "--output-dir", default="models/cost_xgboost_v1.0",
        help="Output directory for model registry",
    )
    parser.add_argument(
        "--optuna-trials", type=int, default=50,
        help="Number of Optuna HPO trials (default: 50)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed",
    )
    parser.add_argument(
        "--test-fraction", type=float, default=0.15,
        help="Fraction of data for test set (default: 0.15)",
    )
    args = parser.parse_args()

    run_training_pipeline(
        data_file=args.data_file,
        generate=args.generate,
        num_samples=args.num_samples,
        output_dir=args.output_dir,
        optuna_trials=args.optuna_trials,
        seed=args.seed,
        test_fraction=args.test_fraction,
    )


if __name__ == "__main__":
    main()
