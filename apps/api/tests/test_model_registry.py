from __future__ import annotations

from app.services.mlops.model_registry import ModelRegistry


class TestModelRegistry:
    async def test_register_model(self):
        registry = ModelRegistry()
        version = await registry.register_model(
            "defect_detector",
            "/models/defect_v1",
            {"accuracy": 0.92, "f1": 0.89},
        )
        assert version == "1"

    async def test_multiple_versions(self):
        registry = ModelRegistry()
        v1 = await registry.register_model(
            "ppe_detector",
            "/v1",
            {"acc": 0.90},
        )
        v2 = await registry.register_model(
            "ppe_detector",
            "/v2",
            {"acc": 0.93},
        )
        assert v1 == "1"
        assert v2 == "2"

    async def test_promote_to_production(self):
        registry = ModelRegistry()
        await registry.register_model(
            "model_a",
            "/v1",
            {"acc": 0.90},
        )
        await registry.promote_to_production("model_a", "1")
        prod = await registry.get_production_model("model_a")
        assert prod is not None
        assert prod["stage"] == "production"

    async def test_promote_demotes_old(self):
        registry = ModelRegistry()
        await registry.register_model(
            "model_b",
            "/v1",
            {"acc": 0.90},
        )
        await registry.register_model(
            "model_b",
            "/v2",
            {"acc": 0.95},
        )
        await registry.promote_to_production("model_b", "1")
        await registry.promote_to_production("model_b", "2")
        versions = await registry.get_model_versions("model_b")
        assert versions[0]["stage"] == "archived"
        assert versions[1]["stage"] == "production"

    async def test_no_production_model(self):
        registry = ModelRegistry()
        await registry.register_model("model_c", "/v1", {})
        prod = await registry.get_production_model("model_c")
        assert prod is None

    async def test_list_models(self):
        registry = ModelRegistry()
        await registry.register_model("model_x", "/v1", {})
        await registry.register_model("model_y", "/v1", {})
        models = await registry.list_models()
        assert len(models) == 2
