"""Tests for the LLM-based document classifier.

Pin the documented document type set, the JSON-parse error fallback,
and the confidence clamp ([0, 0.95] — never fully trust LLM
self-scores).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.classifier import (
    CLASSIFICATION_PROMPT,
    classify_document,
)

# =========================================================================
# CLASSIFICATION_PROMPT — pin documented document types
# =========================================================================


def test_classification_prompt_includes_canonical_types():
    """Pin the 11 documented document types — refactor must not
    silently drop one (downstream code depends on these strings)."""
    canonical = [
        "specification",
        "drawing",
        "contract",
        "rfi",
        "submittal",
        "daily_log",
        "meeting_minutes",
        "change_order",
        "schedule",
        "bim_model",
        "other",
    ]
    for doc_type in canonical:
        assert doc_type in CLASSIFICATION_PROMPT


def test_classification_prompt_uses_user_document_breakout():
    """[security] Document text is wrapped in <user_document> tags
    to prevent prompt injection — pin the canonical breakout."""
    assert "<user_document>" in CLASSIFICATION_PROMPT
    assert "</user_document>" in CLASSIFICATION_PROMPT


def test_classification_prompt_lists_required_fields():
    """Output must include classified_type, csi_division, discipline,
    confidence."""
    for field in ("classified_type", "csi_division", "discipline", "confidence"):
        assert field in CLASSIFICATION_PROMPT


# =========================================================================
# classify_document — happy path
# =========================================================================


def _llm_response(text: str):
    """Build a fake LLM response object."""

    class FakeResponse:
        content = text

    return FakeResponse()


@pytest.mark.asyncio
async def test_classify_document_returns_required_keys():
    """Successful classification → all 5 documented keys present."""
    fake_response = _llm_response(
        json.dumps(
            {
                "classified_type": "specification",
                "csi_division": "03 - Concrete",
                "discipline": "structural",
                "confidence": 0.92,
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("section 03 30 00 cast in place concrete", "spec.pdf")

    assert "classified_type" in out
    assert "csi_division" in out
    assert "discipline" in out
    assert "confidence" in out
    assert "model_used" in out


@pytest.mark.asyncio
async def test_classify_document_passes_through_llm_output():
    """LLM output values reach the result dict."""
    fake_response = _llm_response(
        json.dumps(
            {
                "classified_type": "drawing",
                "csi_division": "23 - HVAC",
                "discipline": "mechanical",
                "confidence": 0.85,
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("HVAC plan", "M-101.pdf")

    assert out["classified_type"] == "drawing"
    assert out["csi_division"] == "23 - HVAC"
    assert out["discipline"] == "mechanical"
    assert out["confidence"] == 0.85


# =========================================================================
# Confidence clamp — [0.0, 0.95]
# =========================================================================


@pytest.mark.asyncio
async def test_classify_confidence_clamped_to_max_095():
    """[security/safety] LLM-claimed 1.0 confidence is rejected —
    we never fully trust the model self-score. Pin clamp at 0.95."""
    fake_response = _llm_response(json.dumps({"classified_type": "drawing", "confidence": 1.0}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")
    assert out["confidence"] == 0.95


@pytest.mark.asyncio
async def test_classify_confidence_clamped_to_min_zero():
    """Negative confidence (data error) clamped to 0.0."""
    fake_response = _llm_response(json.dumps({"classified_type": "drawing", "confidence": -0.5}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_classify_confidence_above_one_clamped():
    """Confidence > 1.0 (data error) clamped to 0.95."""
    fake_response = _llm_response(json.dumps({"classified_type": "drawing", "confidence": 5.0}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")
    assert out["confidence"] == 0.95


# =========================================================================
# Code fence stripping
# =========================================================================


@pytest.mark.asyncio
async def test_classify_strips_markdown_fences():
    """LLM output wrapped in ```json ... ``` must be unwrapped."""
    fake_response = _llm_response('```json\n{"classified_type": "rfi", "confidence": 0.8}\n```')
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("question about rebar", "rfi-001.txt")

    assert out["classified_type"] == "rfi"


@pytest.mark.asyncio
async def test_classify_strips_plain_fences():
    """Plain ``` without language marker also stripped."""
    fake_response = _llm_response('```\n{"classified_type": "submittal", "confidence": 0.7}\n```')
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")
    assert out["classified_type"] == "submittal"


# =========================================================================
# Error fallback paths
# =========================================================================


@pytest.mark.asyncio
async def test_classify_invalid_json_returns_other():
    """LLM produces unparseable text → safe "other" fallback."""
    fake_response = _llm_response("this is not JSON at all {{{")
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")

    assert out["classified_type"] == "other"
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_classify_llm_exception_returns_other():
    """Any exception from the LLM call → safe "other" fallback."""
    fake_invoke = AsyncMock(side_effect=RuntimeError("rate limit"))

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")

    assert out["classified_type"] == "other"
    assert out["csi_division"] is None
    assert out["discipline"] is None
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_classify_missing_classified_type_defaults_other():
    """LLM JSON missing classified_type field → defaults to "other"."""
    fake_response = _llm_response(json.dumps({"confidence": 0.5}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")

    assert out["classified_type"] == "other"


@pytest.mark.asyncio
async def test_classify_missing_optional_fields_default_none():
    """csi_division and discipline default to None when LLM omits them."""
    fake_response = _llm_response(json.dumps({"classified_type": "drawing", "confidence": 0.7}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")

    assert out["csi_division"] is None
    assert out["discipline"] is None


@pytest.mark.asyncio
async def test_classify_model_used_recorded():
    """The model name (gpt-4o-mini) must appear in result for audit."""
    fake_response = _llm_response(json.dumps({"classified_type": "drawing", "confidence": 0.8}))
    fake_invoke = AsyncMock(return_value=fake_response)

    with patch("app.services.agents.classifier.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await classify_document("x", "x.pdf")
    assert out["model_used"] == "gpt-4o-mini"
