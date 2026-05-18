from __future__ import annotations

from app.middleware.response_profiler import ResponseProfiler
from app.services.performance.cache_manager import DashboardCacheManager
from app.services.performance.model_warmup import ModelWarmup
from app.services.performance.query_optimizer import QueryOptimizer


class TestResponseProfiler:
    def test_profiler_init(self):
        profiler = ResponseProfiler(app=None)
        assert profiler.slow_threshold_ms == 500

    def test_get_stats_empty(self):
        profiler = ResponseProfiler(app=None)
        stats = profiler.get_stats()
        assert stats["total_requests"] == 0


class TestQueryOptimizer:
    async def test_analyze_health(self):
        opt = QueryOptimizer()
        health = await opt.analyze_health()
        assert health["status"] == "healthy"

    async def test_get_slow_queries(self):
        opt = QueryOptimizer()
        queries = await opt.get_slow_queries()
        assert isinstance(queries, list)


class TestCacheManager:
    async def test_set_and_get(self):
        cache = DashboardCacheManager()
        await cache.set(
            "portfolio_summary",
            "p1",
            {"spi": 1.0},
        )
        result = await cache.get("portfolio_summary", "p1")
        assert result is not None
        assert result["spi"] == 1.0

    async def test_cache_miss(self):
        cache = DashboardCacheManager()
        result = await cache.get("portfolio_summary", "missing")
        assert result is None

    async def test_invalidate(self):
        cache = DashboardCacheManager()
        await cache.set(
            "project_health",
            "p1",
            {"cpi": 0.9},
        )
        await cache.invalidate("project_health", "p1")
        result = await cache.get("project_health", "p1")
        assert result is None

    def test_cache_stats(self):
        cache = DashboardCacheManager()
        stats = cache.get_stats()
        assert "total_entries" in stats


class TestModelWarmup:
    async def test_warmup_all(self):
        warmup = ModelWarmup()
        results = await warmup.warmup_all()
        assert len(results) == 5
        for _name, status in results.items():
            assert status["status"] == "loaded"

    def test_is_loaded(self):
        warmup = ModelWarmup()
        assert warmup.is_loaded("nonexistent") is False

    def test_get_loaded_models(self):
        warmup = ModelWarmup()
        models = warmup.get_loaded_models()
        assert isinstance(models, list)
