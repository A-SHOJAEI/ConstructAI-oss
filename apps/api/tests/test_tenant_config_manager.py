"""Tests for the per-tenant configuration manager.

Pin defaults, merge semantics on update, and per-agent model
preference lookup.
"""

from __future__ import annotations

import pytest

from app.services.tenant.config_manager import TenantConfigManager

# =========================================================================
# get_config
# =========================================================================


@pytest.fixture
def manager() -> TenantConfigManager:
    return TenantConfigManager()


@pytest.mark.asyncio
async def test_get_config_unknown_org_returns_defaults(manager: TenantConfigManager):
    """[contract] An org that's never been configured gets the
    documented default config."""
    out = await manager.get_config("brand-new-org")
    assert out["feature_flags"] == {}
    assert out["model_preferences"] == {}
    assert out["notification_settings"] == {}
    assert out["billing_plan"] == "startup"


@pytest.mark.asyncio
async def test_default_billing_plan_startup(manager: TenantConfigManager):
    """Default plan is "startup" — pin so a refactor can't quietly
    upgrade unrecognized orgs to a higher tier."""
    out = await manager.get_config("never-existed")
    assert out["billing_plan"] == "startup"


@pytest.mark.asyncio
async def test_get_config_returns_required_keys(manager: TenantConfigManager):
    """Pin schema invariants — every config has these 4 keys."""
    out = await manager.get_config("org-1")
    for required in (
        "feature_flags",
        "model_preferences",
        "notification_settings",
        "billing_plan",
    ):
        assert required in out


# =========================================================================
# update_config — merge semantics
# =========================================================================


@pytest.mark.asyncio
async def test_update_config_persists_changes(manager: TenantConfigManager):
    await manager.update_config("org-1", {"billing_plan": "enterprise"})
    out = await manager.get_config("org-1")
    assert out["billing_plan"] == "enterprise"


@pytest.mark.asyncio
async def test_update_config_merges_with_existing(manager: TenantConfigManager):
    """Update merges into existing — fields not in updates remain."""
    await manager.update_config("org-1", {"billing_plan": "growth"})
    await manager.update_config("org-1", {"feature_flags": {"flag_a": True}})

    out = await manager.get_config("org-1")
    assert out["billing_plan"] == "growth"  # preserved from first update
    assert out["feature_flags"] == {"flag_a": True}


@pytest.mark.asyncio
async def test_update_config_returns_merged_config(manager: TenantConfigManager):
    """update_config returns the merged result so callers don't
    need to re-query."""
    out = await manager.update_config("org-1", {"billing_plan": "enterprise"})
    assert out["billing_plan"] == "enterprise"
    assert out["feature_flags"] == {}  # default preserved


@pytest.mark.asyncio
async def test_update_config_overwrites_specified_keys(manager: TenantConfigManager):
    """Subsequent updates replace top-level keys (no deep merge —
    refactor must preserve this contract)."""
    await manager.update_config("org-1", {"feature_flags": {"a": True, "b": True}})
    await manager.update_config("org-1", {"feature_flags": {"c": True}})

    out = await manager.get_config("org-1")
    # Top-level overwrite — flags a and b gone:
    assert out["feature_flags"] == {"c": True}


@pytest.mark.asyncio
async def test_update_config_per_org_isolation(manager: TenantConfigManager):
    """Updating org A's config must NOT affect org B."""
    await manager.update_config("org-a", {"billing_plan": "enterprise"})
    await manager.update_config("org-b", {"billing_plan": "startup"})

    out_a = await manager.get_config("org-a")
    out_b = await manager.get_config("org-b")
    assert out_a["billing_plan"] == "enterprise"
    assert out_b["billing_plan"] == "startup"


@pytest.mark.asyncio
async def test_update_arbitrary_keys_persisted(manager: TenantConfigManager):
    """Non-canonical keys are also stored — clients can extend the
    config schema without code changes."""
    await manager.update_config("org-1", {"custom_key": "custom_value"})
    out = await manager.get_config("org-1")
    assert out["custom_key"] == "custom_value"


# =========================================================================
# get_model_preference
# =========================================================================


@pytest.mark.asyncio
async def test_get_model_preference_unset_returns_none(manager: TenantConfigManager):
    """No model preference configured → None (caller falls back to
    system default)."""
    out = await manager.get_model_preference("org-1", "estimating_agent")
    assert out is None


@pytest.mark.asyncio
async def test_get_model_preference_set_returns_value(manager: TenantConfigManager):
    """Preference set per-agent → returned."""
    await manager.update_config(
        "org-1",
        {"model_preferences": {"estimating_agent": "anthropic/claude-opus-4"}},
    )
    out = await manager.get_model_preference("org-1", "estimating_agent")
    assert out == "anthropic/claude-opus-4"


@pytest.mark.asyncio
async def test_get_model_preference_other_agent_unset(manager: TenantConfigManager):
    """Setting one agent's preference doesn't affect others."""
    await manager.update_config(
        "org-1",
        {"model_preferences": {"estimating_agent": "anthropic/claude-opus-4"}},
    )
    other = await manager.get_model_preference("org-1", "safety_agent")
    assert other is None


@pytest.mark.asyncio
async def test_get_model_preference_unknown_org_returns_none(
    manager: TenantConfigManager,
):
    out = await manager.get_model_preference("alien-org", "estimating_agent")
    assert out is None
