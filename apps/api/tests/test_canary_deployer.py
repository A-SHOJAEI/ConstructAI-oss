from __future__ import annotations

from app.services.mlops.canary_deployer import CanaryDeployer


class TestCanaryDeployer:
    async def test_deploy_canary(self):
        deployer = CanaryDeployer(evaluation_hours=0)
        result = await deployer.deploy_canary(
            "model_a",
            "v2",
            traffic_percent=5,
        )
        assert result["status"] == "active"
        assert result["traffic_percent"] == 5

    async def test_canary_routes_traffic(self):
        deployer = CanaryDeployer(traffic_percent=5)
        await deployer.deploy_canary("model_a", "v2")
        # With 5% traffic, roughly 5 out of 100 route to canary
        canary_count = sum(1 for i in range(100) if deployer.should_route_to_canary("model_a", i))
        assert 1 <= canary_count <= 15

    async def test_canary_promote(self):
        deployer = CanaryDeployer(evaluation_hours=0)
        await deployer.deploy_canary("model_a", "v2")
        await deployer.record_metrics(
            "model_a",
            True,
            {"accuracy": 0.95},
        )
        await deployer.record_metrics(
            "model_a",
            False,
            {"accuracy": 0.90},
        )
        result = await deployer.promote_or_rollback("model_a")
        assert result["action"] == "promote"
        assert result["status"] == "promoted"

    async def test_canary_rollback(self):
        deployer = CanaryDeployer(evaluation_hours=0)
        await deployer.deploy_canary("model_b", "v2")
        await deployer.record_metrics(
            "model_b",
            True,
            {"accuracy": 0.80},
        )
        await deployer.record_metrics(
            "model_b",
            False,
            {"accuracy": 0.90},
        )
        result = await deployer.promote_or_rollback("model_b")
        assert result["action"] == "rollback"
        assert result["status"] == "rolled_back"

    async def test_evaluate_canary(self):
        deployer = CanaryDeployer(evaluation_hours=0)
        await deployer.deploy_canary("model_c", "v2")
        await deployer.record_metrics(
            "model_c",
            True,
            {"accuracy": 0.92},
        )
        await deployer.record_metrics(
            "model_c",
            False,
            {"accuracy": 0.90},
        )
        evaluation = await deployer.evaluate_canary("model_c")
        assert evaluation["recommendation"] == "promote"
