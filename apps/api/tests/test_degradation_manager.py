"""Tests for the 5-level degradation manager.

Pin every documented capability matrix and the
provider-health-to-level transition logic.
"""

from __future__ import annotations

import pytest

from app.services.reliability.degradation_manager import (
    DEGRADATION_LEVELS,
    DegradationManager,
)

# =========================================================================
# DEGRADATION_LEVELS — pin canonical capability matrix
# =========================================================================


def test_degradation_levels_canonical_count():
    """Pin: 6 documented levels (0-5)."""
    assert set(DEGRADATION_LEVELS.keys()) == {0, 1, 2, 3, 4, 5}


def test_each_level_has_required_fields():
    for level, info in DEGRADATION_LEVELS.items():
        assert "name" in info, f"level {level} missing name"
        assert "description" in info, f"level {level} missing description"
        assert "capabilities" in info, f"level {level} missing capabilities"


def test_level_0_full_cloud_all_enabled():
    """Level 0: full cloud — all capabilities should be available."""
    caps = DEGRADATION_LEVELS[0]["capabilities"]
    for cap in (
        "cloud_llm",
        "local_llm",
        "vision_models",
        "rag_search",
        "real_time_alerts",
        "report_generation",
        "transcription",
    ):
        assert caps[cap] is True, f"level 0 should have {cap} enabled"


def test_level_3_offline_no_cloud():
    """Level 3: full offline — cloud_llm and rag_search disabled,
    local_llm + vision still work."""
    caps = DEGRADATION_LEVELS[3]["capabilities"]
    assert caps["cloud_llm"] is False
    assert caps["rag_search"] is False
    assert caps["local_llm"] is True
    assert caps["vision_models"] is True
    assert caps["real_time_alerts"] is True


def test_level_4_low_power_rules_only():
    """Level 4: rules engine only — even local LLM disabled."""
    caps = DEGRADATION_LEVELS[4]["capabilities"]
    assert caps["cloud_llm"] is False
    assert caps["local_llm"] is False
    assert caps["vision_models"] is False
    assert caps["real_time_alerts"] is True  # still alert via rules


def test_level_5_emergency_all_disabled():
    """Level 5: emergency — even real-time alerts off (graceful
    shutdown)."""
    caps = DEGRADATION_LEVELS[5]["capabilities"]
    for cap in caps.values():
        assert cap is False


def test_levels_have_descriptive_names():
    """Each level's name is documented in the manager class
    docstring — pin the canonical text."""
    expected_names = {
        0: "Full cloud",
        1: "Provider failover",
        2: "Intermittent",
        3: "Full offline",
        4: "Low power",
        5: "Emergency",
    }
    for level, name in expected_names.items():
        assert DEGRADATION_LEVELS[level]["name"] == name


# =========================================================================
# DegradationManager — initial state
# =========================================================================


@pytest.fixture
def mgr() -> DegradationManager:
    return DegradationManager()


def test_initial_level_is_zero(mgr: DegradationManager):
    """Manager starts in full-cloud mode."""
    assert mgr.current_level == 0


# =========================================================================
# evaluate_health — provider state → level
# =========================================================================


@pytest.mark.asyncio
async def test_evaluate_health_no_state_returns_current(mgr: DegradationManager):
    """Without provider states, return current level (no change)."""
    out = await mgr.evaluate_health(None)
    assert out == 0


@pytest.mark.asyncio
async def test_evaluate_health_empty_dict_resets_to_zero(mgr: DegradationManager):
    """Empty dict — no providers tracked → level 0."""
    out = await mgr.evaluate_health({})
    assert out == 0


@pytest.mark.asyncio
async def test_evaluate_health_all_closed_level_zero(mgr: DegradationManager):
    """All providers healthy → level 0."""
    out = await mgr.evaluate_health({"openai": "closed", "anthropic": "closed", "voyage": "closed"})
    assert out == 0


@pytest.mark.asyncio
async def test_evaluate_health_some_half_open_level_one(mgr: DegradationManager):
    """One provider recovering, others healthy → level 1 (failover)."""
    out = await mgr.evaluate_health({"openai": "closed", "anthropic": "half_open"})
    assert out == 1


@pytest.mark.asyncio
async def test_evaluate_health_some_open_some_closed_level_one(mgr: DegradationManager):
    """Some providers down but not all → level 1 (failover)."""
    out = await mgr.evaluate_health({"openai": "open", "anthropic": "closed"})
    assert out == 1


@pytest.mark.asyncio
async def test_evaluate_health_all_open_level_three_offline(mgr: DegradationManager):
    """All providers down → level 3 (full offline mode)."""
    out = await mgr.evaluate_health({"openai": "open", "anthropic": "open", "voyage": "open"})
    assert out == 3


@pytest.mark.asyncio
async def test_evaluate_health_mixed_open_half_open_level_one(mgr: DegradationManager):
    """[documented quirk] Mixed open + half_open with `open < total`
    → level 1 (failover). The else-branch level 2 is in practice
    unreachable from evaluate_health (would require open==total and
    half_open>0 simultaneously, which is impossible). Pin so a
    refactor doesn't accidentally promote/demote without intent."""
    out = await mgr.evaluate_health({"openai": "open", "anthropic": "half_open"})
    assert out == 1


@pytest.mark.asyncio
async def test_evaluate_health_level_two_via_manual_set(mgr: DegradationManager):
    """Level 2 (Intermittent) — only reachable via set_level since the
    evaluate_health else branch is in practice unreachable."""
    await mgr.set_level(2)
    assert mgr.current_level == 2
    caps = await mgr.get_available_capabilities()
    assert caps["name"] == "Intermittent"


@pytest.mark.asyncio
async def test_evaluate_health_persists_level(mgr: DegradationManager):
    """After evaluate_health, current_level reflects the new level."""
    await mgr.evaluate_health({"openai": "open", "anthropic": "open"})
    assert mgr.current_level == 3


# =========================================================================
# set_level — manual override
# =========================================================================


@pytest.mark.asyncio
async def test_set_level_valid_changes_state(mgr: DegradationManager):
    await mgr.set_level(2)
    assert mgr.current_level == 2


@pytest.mark.asyncio
async def test_set_level_invalid_raises(mgr: DegradationManager):
    """Levels outside 0-5 must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid degradation level"):
        await mgr.set_level(99)


@pytest.mark.asyncio
async def test_set_level_negative_raises(mgr: DegradationManager):
    with pytest.raises(ValueError, match="Invalid degradation level"):
        await mgr.set_level(-1)


# =========================================================================
# get_available_capabilities
# =========================================================================


@pytest.mark.asyncio
async def test_get_capabilities_includes_level_metadata(mgr: DegradationManager):
    out = await mgr.get_available_capabilities()
    assert out["level"] == 0
    assert out["name"] == "Full cloud"
    assert "capabilities" in out


@pytest.mark.asyncio
async def test_get_capabilities_after_level_change(mgr: DegradationManager):
    await mgr.set_level(3)
    out = await mgr.get_available_capabilities()
    assert out["level"] == 3
    assert out["capabilities"]["cloud_llm"] is False


# =========================================================================
# is_capability_available
# =========================================================================


def test_is_capability_available_at_level_zero(mgr: DegradationManager):
    """Level 0 — all canonical capabilities available."""
    for cap in (
        "cloud_llm",
        "local_llm",
        "vision_models",
        "rag_search",
        "real_time_alerts",
    ):
        assert mgr.is_capability_available(cap) is True


@pytest.mark.asyncio
async def test_is_capability_unavailable_at_level_3(mgr: DegradationManager):
    await mgr.set_level(3)
    assert mgr.is_capability_available("cloud_llm") is False
    assert mgr.is_capability_available("local_llm") is True


def test_is_capability_unknown_returns_false(mgr: DegradationManager):
    """Unknown capability → False (safest default)."""
    assert mgr.is_capability_available("alien_capability_xyz") is False


@pytest.mark.asyncio
async def test_is_capability_at_level_5_all_false(mgr: DegradationManager):
    """Emergency level — every capability unavailable."""
    await mgr.set_level(5)
    for cap in (
        "cloud_llm",
        "local_llm",
        "vision_models",
        "rag_search",
        "real_time_alerts",
        "report_generation",
        "transcription",
    ):
        assert mgr.is_capability_available(cap) is False
