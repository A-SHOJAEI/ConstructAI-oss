"""Tests for performance helpers: QueryOptimizer + ModelWarmup.

Both modules are small but pin the public surface — index-recommendation
priority bands, the model-list invariants, and the warmup result schema.
"""

from __future__ import annotations

from app.services.performance.model_warmup import ModelWarmup
from app.services.performance.query_optimizer import QueryOptimizer

# =========================================================================
# QueryOptimizer.recommend_indexes
# =========================================================================


async def test_recommend_indexes_empty_input():
    opt = QueryOptimizer()
    assert await opt.recommend_indexes([]) == []


async def test_recommend_indexes_below_threshold_skipped():
    """Queries under 500 ms mean time → no recommendation."""
    opt = QueryOptimizer()
    out = await opt.recommend_indexes([{"query": "SELECT 1", "mean_time_ms": 100}])
    assert out == []


async def test_recommend_indexes_medium_priority():
    """500 < mean_time ≤ 1000 → medium priority."""
    opt = QueryOptimizer()
    out = await opt.recommend_indexes([{"query": "SELECT 1", "mean_time_ms": 750}])
    assert len(out) == 1
    assert out[0]["priority"] == "medium"


async def test_recommend_indexes_high_priority():
    """mean_time > 1000 → high priority."""
    opt = QueryOptimizer()
    out = await opt.recommend_indexes([{"query": "SELECT 1", "mean_time_ms": 1500}])
    assert len(out) == 1
    assert out[0]["priority"] == "high"


async def test_recommend_indexes_query_pattern_truncated():
    """Query pattern is truncated to 100 chars to keep recommendation
    payloads bounded."""
    opt = QueryOptimizer()
    long_query = "SELECT " + ("col_name, " * 50) + "FROM tbl"
    out = await opt.recommend_indexes([{"query": long_query, "mean_time_ms": 1500}])
    assert len(out[0]["query_pattern"]) == 100


async def test_recommend_indexes_multiple_queries_independent():
    opt = QueryOptimizer()
    queries = [
        {"query": "fast", "mean_time_ms": 50},
        {"query": "medium", "mean_time_ms": 600},
        {"query": "slow", "mean_time_ms": 2000},
    ]
    out = await opt.recommend_indexes(queries)
    assert len(out) == 2  # fast skipped
    priorities = {r["priority"] for r in out}
    assert priorities == {"medium", "high"}


async def test_recommend_indexes_threshold_boundary_exact_500():
    """At exactly 500 ms, the > 500 check is False → no recommendation."""
    opt = QueryOptimizer()
    out = await opt.recommend_indexes([{"query": "x", "mean_time_ms": 500}])
    assert out == []


async def test_recommend_indexes_threshold_boundary_exact_1000():
    """At exactly 1000 ms, > 1000 is False → medium (not high)."""
    opt = QueryOptimizer()
    out = await opt.recommend_indexes([{"query": "x", "mean_time_ms": 1000}])
    assert out[0]["priority"] == "medium"


async def test_get_slow_queries_returns_list():
    """The placeholder must return a list (not raise)."""
    opt = QueryOptimizer()
    out = await opt.get_slow_queries()
    assert out == []


async def test_analyze_health_returns_required_keys():
    opt = QueryOptimizer()
    out = await opt.analyze_health()
    for key in ("status", "slow_query_count", "index_recommendations", "cache_hit_ratio"):
        assert key in out
    assert 0.0 <= out["cache_hit_ratio"] <= 1.0


# =========================================================================
# ModelWarmup
# =========================================================================


def test_models_to_warmup_canonical():
    """Pin the documented model list — refactor must not silently drop
    one."""
    expected_names = {
        "document_classifier",
        "defect_detector",
        "activity_recognizer",
        "ppe_detector",
        "embedding_model",
    }
    actual = {m["name"] for m in ModelWarmup.MODELS_TO_WARMUP}
    assert actual == expected_names


def test_each_model_has_name_and_type():
    """Required schema for the warmup loop — name + type."""
    for model_info in ModelWarmup.MODELS_TO_WARMUP:
        assert "name" in model_info
        assert "type" in model_info
        assert isinstance(model_info["name"], str)
        assert isinstance(model_info["type"], str)


def test_initial_state_no_models_loaded():
    w = ModelWarmup()
    assert w.get_loaded_models() == []
    assert w.is_loaded("document_classifier") is False


async def test_warmup_all_loads_every_model():
    w = ModelWarmup()
    results = await w.warmup_all()
    assert len(results) == len(ModelWarmup.MODELS_TO_WARMUP)
    for name, info in results.items():
        assert info["status"] == "loaded"
        assert "type" in info


async def test_warmup_marks_models_loaded():
    """After warmup, is_loaded() should return True for each model."""
    w = ModelWarmup()
    await w.warmup_all()
    for model_info in ModelWarmup.MODELS_TO_WARMUP:
        assert w.is_loaded(model_info["name"])


async def test_warmup_records_failures(monkeypatch):
    """If _load_model raises, the result must record status=failed
    with the error message — but other models still get loaded."""
    w = ModelWarmup()

    original_load = w._load_model
    call_count = {"n": 0}

    async def flaky_load(model_info):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated load failure")
        await original_load(model_info)

    monkeypatch.setattr(w, "_load_model", flaky_load)

    results = await w.warmup_all()
    # First entry failed:
    first_name = ModelWarmup.MODELS_TO_WARMUP[0]["name"]
    assert results[first_name]["status"] == "failed"
    assert "simulated load failure" in results[first_name]["error"]
    # Subsequent entries still loaded:
    for model_info in ModelWarmup.MODELS_TO_WARMUP[1:]:
        assert results[model_info["name"]]["status"] == "loaded"


def test_get_loaded_models_returns_list():
    """API contract — must return a list, not a dict_keys view."""
    w = ModelWarmup()
    out = w.get_loaded_models()
    assert isinstance(out, list)
