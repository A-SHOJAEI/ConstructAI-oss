"""Tests for LLMGateway pure helpers (cost estimation + config loaders).

The full gateway uses LiteLLM to call real LLM providers; these
tests pin the deterministic helpers — fallback model chain
configuration, pricing loader (env override + defaults), cost
estimation per model, and message-to-text concatenation for
cache keys.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from app.services.reliability.llm_gateway import (
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_LLM_CALLS,
    DEFAULT_MAX_COST_PER_REQUEST,
    DEFAULT_MAX_TOKENS_PER_REQUEST,
    LLMGateway,
    _load_fallback_models,
    _load_pricing,
)

# =========================================================================
# Constants — pin documented defaults
# =========================================================================


def test_default_llm_timeout_30_seconds():
    """Pin default LLM timeout — refactor changing it would shift
    user-visible response latency expectations."""
    assert DEFAULT_LLM_TIMEOUT_SECONDS == 30


def test_default_max_tokens_per_request():
    """4096 is the canonical max-tokens cap for LLM responses."""
    assert DEFAULT_MAX_TOKENS_PER_REQUEST == 4096


def test_default_max_cost_per_request():
    """[budget invariant] Single LLM request capped at $1.00 to
    prevent runaway costs from misconfigured prompts."""
    assert DEFAULT_MAX_COST_PER_REQUEST == 1.00


def test_default_max_concurrent_calls():
    """[memory bound] Concurrent LLM calls capped at 10 to prevent
    overwhelming downstream providers."""
    assert DEFAULT_MAX_CONCURRENT_LLM_CALLS == 10


# =========================================================================
# _load_fallback_models — chain configuration
# =========================================================================


def test_default_fallback_chain_length():
    """Local-only chain: 2 providers (Spark 1 vLLM 120B + Spark 2 Ollama 20B).

    Cloud chain (4 providers) restored when ``LLM_LEGACY_CLOUD_FALLBACK=1``.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_LEGACY_CLOUD_FALLBACK", None)
        chain = _load_fallback_models()
    assert len(chain) == 2


def test_default_fallback_chain_priorities_strict_ascending():
    """Local-only chain has priorities (1, 2)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_LEGACY_CLOUD_FALLBACK", None)
        chain = _load_fallback_models()
    priorities = [m["priority"] for m in chain]
    assert priorities == [1, 2]


def test_default_fallback_chain_includes_local_providers():
    """Both tiers must be local providers — vLLM Spark 1 (heavy reasoning)
    + Ollama Spark 2 (fast tier)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_LEGACY_CLOUD_FALLBACK", None)
        chain = _load_fallback_models()
    names = [m["name"] for m in chain]
    assert "local-vllm-spark1-120b" in names
    assert "local-ollama-spark2-fast" in names


def test_legacy_cloud_chain_restored_when_flag_set():
    """LLM_LEGACY_CLOUD_FALLBACK=1 restores the historical 4-tier cloud
    chain (anthropic → openai → gemini → ollama-legacy) for emergency
    recovery without a code rollback."""
    with patch.dict(os.environ, {"LLM_LEGACY_CLOUD_FALLBACK": "1"}):
        chain = _load_fallback_models()
    assert len(chain) == 4
    models = [m["model"] for m in chain]
    assert any("anthropic/" in m for m in models)
    assert any("openai/" in m for m in models)
    assert any("gemini/" in m for m in models)
    assert "ollama" in chain[-1]["model"]


def test_default_fallback_models_initialized_on_module_load():
    """Pin: DEFAULT_FALLBACK_MODELS exists at import time (local-only)."""
    assert isinstance(DEFAULT_FALLBACK_MODELS, list)
    assert len(DEFAULT_FALLBACK_MODELS) == 2


# =========================================================================
# _load_pricing — env override + defaults
# =========================================================================


def test_load_pricing_no_env_returns_defaults():
    """Without LLM_PRICING_JSON set, return documented defaults."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_PRICING_JSON", None)
        pricing = _load_pricing()
    assert "anthropic/claude-sonnet-4-20250514" in pricing
    # Anthropic Sonnet rates: $3/M input, $15/M output ($0.003/$0.015 per 1k):
    input_rate, output_rate = pricing["anthropic/claude-sonnet-4-20250514"]
    assert input_rate == 0.003
    assert output_rate == 0.015


def test_load_pricing_local_llama_zero_cost():
    """Ollama (local) is free — pin so a refactor can't accidentally
    bill local inference."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LLM_PRICING_JSON", None)
        pricing = _load_pricing()
    assert pricing["ollama/llama3.2:3b"] == (0.0, 0.0)


def test_load_pricing_env_override():
    """LLM_PRICING_JSON overrides defaults."""
    custom = {"openai/gpt-4o": [0.01, 0.03]}
    with patch.dict(os.environ, {"LLM_PRICING_JSON": json.dumps(custom)}):
        pricing = _load_pricing()
    assert pricing["openai/gpt-4o"] == (0.01, 0.03)


def test_load_pricing_invalid_json_falls_back_to_defaults(caplog):
    """Garbage JSON → log warning + fall back to defaults."""
    with patch.dict(os.environ, {"LLM_PRICING_JSON": "not valid json {{{"}):
        pricing = _load_pricing()
    # Defaults preserved:
    assert "anthropic/claude-sonnet-4-20250514" in pricing


def test_load_pricing_invalid_entry_skipped(caplog):
    """Malformed entry (not a 2-tuple) → skipped, others kept."""
    custom = {
        "openai/gpt-4o": [0.005, 0.015],
        "broken/model": "not a list",
    }
    with patch.dict(os.environ, {"LLM_PRICING_JSON": json.dumps(custom)}):
        pricing = _load_pricing()
    # Valid entry present:
    assert pricing["openai/gpt-4o"] == (0.005, 0.015)
    # Malformed entry NOT present:
    assert "broken/model" not in pricing


def test_load_pricing_partial_array_skipped():
    """Array with wrong length → skipped."""
    custom = {
        "openai/gpt-4o": [0.005, 0.015],
        "broken/model": [0.001],  # only 1 element
    }
    with patch.dict(os.environ, {"LLM_PRICING_JSON": json.dumps(custom)}):
        pricing = _load_pricing()
    assert "broken/model" not in pricing
    assert pricing["openai/gpt-4o"] == (0.005, 0.015)


# =========================================================================
# LLMGateway._estimate_cost
# =========================================================================


@pytest.fixture
def gateway() -> LLMGateway:
    return LLMGateway()


def test_estimate_cost_known_model_uses_pricing(gateway: LLMGateway):
    """1000 input tokens × $0.003/1k = $0.003. 500 output × $0.015/1k
    = $0.0075. Total = $0.0105."""
    cost = gateway._estimate_cost(
        model="anthropic/claude-sonnet-4-20250514",
        input_tokens=1000,
        output_tokens=500,
    )
    assert cost == pytest.approx(0.003 + 0.0075, abs=1e-6)


def test_estimate_cost_unknown_model_uses_default(gateway: LLMGateway):
    """Unknown model → default pricing (0.001/0.002 per 1k)."""
    cost = gateway._estimate_cost(
        model="alien/model-xyz",
        input_tokens=1000,
        output_tokens=1000,
    )
    # 1000 × 0.001/1000 + 1000 × 0.002/1000 = 0.001 + 0.002 = 0.003
    assert cost == pytest.approx(0.003)


def test_estimate_cost_zero_tokens_zero_cost(gateway: LLMGateway):
    cost = gateway._estimate_cost(
        model="anthropic/claude-sonnet-4-20250514",
        input_tokens=0,
        output_tokens=0,
    )
    assert cost == 0.0


def test_estimate_cost_input_only(gateway: LLMGateway):
    cost = gateway._estimate_cost(
        model="openai/gpt-4o",
        input_tokens=1000,
        output_tokens=0,
    )
    # gpt-4o input rate is $0.005/1k:
    assert cost == pytest.approx(0.005)


def test_estimate_cost_output_only(gateway: LLMGateway):
    cost = gateway._estimate_cost(
        model="openai/gpt-4o",
        input_tokens=0,
        output_tokens=1000,
    )
    # gpt-4o output rate is $0.015/1k:
    assert cost == pytest.approx(0.015)


def test_estimate_cost_local_llama_free(gateway: LLMGateway):
    """[budget invariant] Local Ollama always returns $0 cost — even
    for huge prompts, since inference is local."""
    cost = gateway._estimate_cost(
        model="ollama/llama3.2:3b",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == 0.0


def test_estimate_cost_scales_linearly_with_tokens(gateway: LLMGateway):
    """Doubling tokens → doubles cost."""
    cost_1k = gateway._estimate_cost("openai/gpt-4o", 1000, 1000)
    cost_2k = gateway._estimate_cost("openai/gpt-4o", 2000, 2000)
    assert cost_2k == pytest.approx(cost_1k * 2)


# =========================================================================
# _messages_to_text
# =========================================================================


def test_messages_to_text_concatenates(gateway: LLMGateway):
    """For semantic-cache key generation: messages joined with spaces."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello."},
    ]
    out = gateway._messages_to_text(messages)
    assert "You are helpful." in out
    assert "Hello." in out


def test_messages_to_text_empty_list(gateway: LLMGateway):
    assert gateway._messages_to_text([]) == ""


def test_messages_to_text_missing_content(gateway: LLMGateway):
    """Messages with no content field → empty string contribution
    (no KeyError)."""
    messages = [{"role": "user"}, {"role": "user", "content": "hi"}]
    out = gateway._messages_to_text(messages)
    assert "hi" in out
