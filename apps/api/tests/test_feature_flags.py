from __future__ import annotations

from app.services.beta.feature_flags import FeatureFlagService


class TestFeatureFlags:
    def test_register_and_check_disabled(self):
        svc = FeatureFlagService()
        svc.register_flag("new_dashboard", enabled=False)
        assert svc.is_enabled("new_dashboard") is False

    def test_register_and_check_enabled(self):
        svc = FeatureFlagService()
        svc.register_flag(
            "new_dashboard",
            enabled=True,
            rollout_percentage=100,
        )
        assert svc.is_enabled("new_dashboard") is True

    def test_tenant_override_true(self):
        svc = FeatureFlagService()
        svc.register_flag(
            "beta_feature",
            enabled=False,
            tenant_overrides={"org-1": True},
        )
        assert svc.is_enabled("beta_feature", org_id="org-1") is True
        assert svc.is_enabled("beta_feature", org_id="org-2") is False

    def test_tenant_override_false(self):
        svc = FeatureFlagService()
        svc.register_flag(
            "feature_x",
            enabled=True,
            rollout_percentage=100,
            tenant_overrides={"org-bad": False},
        )
        assert (
            svc.is_enabled(
                "feature_x",
                org_id="org-bad",
            )
            is False
        )

    def test_rollout_percentage(self):
        svc = FeatureFlagService()
        svc.register_flag(
            "gradual",
            enabled=True,
            rollout_percentage=50,
        )
        # With deterministic hashing, some users enabled, some not
        results = [svc.is_enabled("gradual", user_id=f"user-{i}") for i in range(100)]
        enabled_count = sum(results)
        assert 20 < enabled_count < 80  # roughly 50%

    def test_unknown_flag_disabled(self):
        svc = FeatureFlagService()
        assert svc.is_enabled("nonexistent") is False

    def test_get_all_flags(self):
        svc = FeatureFlagService()
        svc.register_flag(
            "flag_a",
            enabled=True,
            rollout_percentage=100,
        )
        svc.register_flag("flag_b", enabled=False)
        flags = svc.get_all_flags()
        assert len(flags) == 2
        assert "flag_a" in flags
