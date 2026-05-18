"""Tests for the LLM risk clause detector.

Pin documented risk types, severity validation (clamp invalid values
to "medium"), confidence cap at 0.95, error fallback to empty list.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.risk_detector import (
    RISK_DETECTION_PROMPT,
    detect_risks,
)

# =========================================================================
# RISK_DETECTION_PROMPT — pin documented risk types
# =========================================================================


def test_prompt_lists_canonical_risk_types():
    """Pin the 6 documented risk types — refactor must not silently
    drop one."""
    canonical = [
        "liability",
        "indemnification",
        "liquidated_damages",
        "warranty",
        "insurance_requirement",
        "safety_hazard",
    ]
    for risk_type in canonical:
        assert risk_type in RISK_DETECTION_PROMPT


def test_prompt_uses_user_document_breakout():
    """[security] User text wrapped in <user_document> tags to
    prevent prompt injection."""
    assert "<user_document>" in RISK_DETECTION_PROMPT
    assert "</user_document>" in RISK_DETECTION_PROMPT


def test_prompt_lists_severity_levels():
    """Pin: low/medium/high/critical as documented severities."""
    for severity in ("low", "medium", "high", "critical"):
        assert severity in RISK_DETECTION_PROMPT


# =========================================================================
# detect_risks — happy path
# =========================================================================


def _llm_response(text: str):
    """Build a fake LLM response object."""

    class FakeResponse:
        content = text

    return FakeResponse()


@pytest.mark.asyncio
async def test_detect_risks_returns_list():
    fake_response = _llm_response(
        json.dumps(
            {
                "risks": [
                    {
                        "risk_type": "liability",
                        "description": "Contractor accepts unlimited liability",
                        "section_reference": "Section 9.4",
                        "severity": "high",
                        "confidence": 0.9,
                    }
                ]
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("contract text")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["risk_type"] == "liability"
    assert out[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_detect_risks_no_risks_returns_empty_list():
    fake_response = _llm_response(json.dumps({"risks": []}))
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("clean contract")
    assert out == []


@pytest.mark.asyncio
async def test_detect_risks_filters_empty_descriptions():
    """Risks without description are filtered out (data hygiene)."""
    fake_response = _llm_response(
        json.dumps(
            {
                "risks": [
                    {"risk_type": "liability", "description": "real risk", "severity": "high"},
                    {"risk_type": "warranty", "description": ""},  # filtered
                    {"risk_type": "insurance_requirement"},  # also filtered (no desc)
                ]
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("text")
    assert len(out) == 1
    assert out[0]["risk_type"] == "liability"


# =========================================================================
# Severity normalization
# =========================================================================


@pytest.mark.asyncio
async def test_detect_risks_invalid_severity_defaults_to_medium():
    """Severity outside the documented set → "medium" fallback. Pin
    so a refactor can't silently accept arbitrary severity strings
    (would corrupt downstream priority sorting)."""
    fake_response = _llm_response(
        json.dumps(
            {
                "risks": [
                    {
                        "risk_type": "liability",
                        "description": "x",
                        "severity": "catastrophic",  # invalid
                    }
                ]
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["severity"] == "medium"


@pytest.mark.asyncio
async def test_detect_risks_missing_severity_defaults_to_medium():
    fake_response = _llm_response(
        json.dumps({"risks": [{"risk_type": "warranty", "description": "x"}]})
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["severity"] == "medium"


@pytest.mark.asyncio
async def test_detect_risks_each_valid_severity_passes_through():
    """All 4 documented severities pass through unchanged."""
    for severity in ("low", "medium", "high", "critical"):
        fake_response = _llm_response(
            json.dumps(
                {"risks": [{"risk_type": "warranty", "description": "x", "severity": severity}]}
            )
        )
        fake_invoke = AsyncMock(return_value=fake_response)
        with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
            fake_client.return_value.ainvoke = fake_invoke
            out = await detect_risks("x")
        assert out[0]["severity"] == severity


# =========================================================================
# Confidence clamp
# =========================================================================


@pytest.mark.asyncio
async def test_detect_risks_confidence_clamped_at_095():
    """[security/safety] Same as classifier — never fully trust LLM
    self-score. Cap at 0.95."""
    fake_response = _llm_response(
        json.dumps(
            {
                "risks": [
                    {"risk_type": "liability", "description": "x", "confidence": 1.0},
                ]
            }
        )
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["confidence"] == 0.95


@pytest.mark.asyncio
async def test_detect_risks_negative_confidence_clamped_zero():
    fake_response = _llm_response(
        json.dumps({"risks": [{"risk_type": "x", "description": "x", "confidence": -1.0}]})
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["confidence"] == 0.0


# =========================================================================
# Defaults for missing fields
# =========================================================================


@pytest.mark.asyncio
async def test_detect_risks_missing_risk_type_defaults_unknown():
    fake_response = _llm_response(json.dumps({"risks": [{"description": "vague clause"}]}))
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["risk_type"] == "unknown"


@pytest.mark.asyncio
async def test_detect_risks_missing_section_reference_is_none():
    fake_response = _llm_response(
        json.dumps({"risks": [{"risk_type": "liability", "description": "x"}]})
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out[0]["section_reference"] is None


# =========================================================================
# Code fences and error fallback
# =========================================================================


@pytest.mark.asyncio
async def test_detect_risks_strips_markdown_fences():
    fake_response = _llm_response(
        '```json\n{"risks": [{"risk_type": "liability", "description": "x"}]}\n```'
    )
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_detect_risks_invalid_json_returns_empty_list():
    fake_response = _llm_response("not valid json {{{")
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out == []


@pytest.mark.asyncio
async def test_detect_risks_llm_exception_returns_empty_list():
    fake_invoke = AsyncMock(side_effect=RuntimeError("rate limit"))
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out == []


@pytest.mark.asyncio
async def test_detect_risks_missing_risks_key_returns_empty():
    """LLM JSON without "risks" key → empty list (not crash)."""
    fake_response = _llm_response(json.dumps({"other_field": "x"}))
    fake_invoke = AsyncMock(return_value=fake_response)
    with patch("app.services.agents.risk_detector.ChatOpenAI") as fake_client:
        fake_client.return_value.ainvoke = fake_invoke
        out = await detect_risks("x")
    assert out == []
