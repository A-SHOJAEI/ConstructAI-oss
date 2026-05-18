"""Tests for the pure helpers in services/procurement/contract_risk.

Pin the weighted score, chunking strategy, and code-fence stripping —
all the deterministic pieces of contract risk analysis.
"""

from __future__ import annotations

from app.services.procurement.contract_risk import (
    _RISK_TYPE_WEIGHTS,
    _SEVERITY_SCORES,
    _calculate_weighted_score,
    _split_into_chunks,
    _strip_code_fences,
)

# =========================================================================
# Risk weight / severity invariants
# =========================================================================


def test_risk_type_weights_canonical():
    """Pin the documented risk types — refactor must not silently
    drop one."""
    expected = {
        "indemnification",
        "liquidated_damages",
        "insurance_requirements",
        "payment_terms",
        "change_order_process",
        "scope_creep",
        "warranty",
        "dispute_resolution",
    }
    assert set(_RISK_TYPE_WEIGHTS.keys()) == expected


def test_indemnification_highest_weight():
    """Indemnification is the highest-weight risk type — pin so a
    refactor doesn't accidentally demote it."""
    indem = _RISK_TYPE_WEIGHTS["indemnification"]
    for risk_type, weight in _RISK_TYPE_WEIGHTS.items():
        if risk_type != "indemnification":
            assert weight <= indem


def test_severity_scores_ordered():
    """critical > high > medium > low."""
    assert _SEVERITY_SCORES["critical"] > _SEVERITY_SCORES["high"]
    assert _SEVERITY_SCORES["high"] > _SEVERITY_SCORES["medium"]
    assert _SEVERITY_SCORES["medium"] > _SEVERITY_SCORES["low"]


# =========================================================================
# _calculate_weighted_score
# =========================================================================


def test_weighted_score_empty_zero():
    assert _calculate_weighted_score([]) == 0.0


def test_weighted_score_single_low_risk():
    """Single low-severity scope_creep: 5 × 1.0 = 5."""
    out = _calculate_weighted_score([{"risk_type": "scope_creep", "severity": "low"}])
    assert out == 5.0


def test_weighted_score_high_severity_indemnification():
    """High indemnification: 18 × 1.5 = 27."""
    out = _calculate_weighted_score([{"risk_type": "indemnification", "severity": "high"}])
    assert out == 27.0


def test_weighted_score_critical_indemnification():
    """Critical indemnification: 25 × 1.5 = 37.5."""
    out = _calculate_weighted_score([{"risk_type": "indemnification", "severity": "critical"}])
    assert out == 37.5


def test_weighted_score_aggregates_multiple_items():
    """Score sums across items."""
    items = [
        {"risk_type": "indemnification", "severity": "high"},  # 27
        {"risk_type": "scope_creep", "severity": "medium"},  # 10
    ]
    assert _calculate_weighted_score(items) == 37.0


def test_weighted_score_capped_at_100():
    """Stack 10 critical/indemnification items — must NOT exceed 100."""
    items = [{"risk_type": "indemnification", "severity": "critical"} for _ in range(10)]
    assert _calculate_weighted_score(items) == 100.0


def test_weighted_score_unknown_risk_type_uses_default_weight():
    """Unknown risk type defaults to weight 1.0."""
    out = _calculate_weighted_score([{"risk_type": "unknown_type", "severity": "high"}])
    # 18 (high) × 1.0 (default) = 18
    assert out == 18.0


def test_weighted_score_unknown_severity_uses_default():
    """Unknown severity defaults to medium (10)."""
    out = _calculate_weighted_score([{"risk_type": "scope_creep", "severity": "unknown"}])
    assert out == 10.0


def test_weighted_score_missing_keys_use_defaults():
    """No risk_type / severity → defaults applied."""
    out = _calculate_weighted_score([{}])
    # Default risk_type=scope_creep (weight 1.0), severity=medium (10)
    assert out == 10.0


# =========================================================================
# _split_into_chunks
# =========================================================================


def test_split_into_chunks_short_text_single_chunk():
    """Text shorter than chunk_size → single chunk."""
    text = "short text"
    out = _split_into_chunks(text, chunk_size=100)
    assert out == [text]


def test_split_into_chunks_at_paragraph_boundary():
    """Long text with double-newlines → splits at boundaries."""
    para1 = "A" * 50
    para2 = "B" * 50
    text = f"{para1}\n\n{para2}"
    out = _split_into_chunks(text, chunk_size=70)
    # Should split at the \n\n, leaving para1 in chunk 0 and para2 in
    # chunk 1 (without consuming the separator):
    assert len(out) == 2
    assert para1 in out[0]
    assert para2 in out[1]


def test_split_into_chunks_at_single_newline_when_no_double():
    text = "X" * 60 + "\n" + "Y" * 60
    out = _split_into_chunks(text, chunk_size=80)
    assert len(out) >= 2


def test_split_into_chunks_hard_split_when_no_newline():
    """No paragraph boundaries at all — falls back to hard splits."""
    text = "X" * 200
    out = _split_into_chunks(text, chunk_size=50)
    assert len(out) >= 4
    # Total length matches input:
    assert sum(len(c) for c in out) == 200


def test_split_into_chunks_empty_text():
    assert _split_into_chunks("") == [""]


def test_split_into_chunks_each_chunk_under_limit():
    """No chunk should exceed chunk_size + slop (boundary may push
    just past, but not by much)."""
    text = ("paragraph block.\n\n" * 100).strip()
    out = _split_into_chunks(text, chunk_size=200)
    for chunk in out:
        # Allow some slack for paragraph-boundary-aligned splitting:
        assert len(chunk) <= 250


# =========================================================================
# _strip_code_fences
# =========================================================================


def test_strip_code_fences_with_lang_marker():
    """LLM responses commonly wrap JSON in ```json ... ``` — must strip."""
    text = '```json\n{"key": "value"}\n```'
    out = _strip_code_fences(text)
    assert out == '{"key": "value"}'


def test_strip_code_fences_without_lang():
    """Plain ``` ... ``` also stripped."""
    text = '```\n{"key": "value"}\n```'
    out = _strip_code_fences(text)
    assert out == '{"key": "value"}'


def test_strip_code_fences_no_fences_unchanged():
    text = '{"key": "value"}'
    assert _strip_code_fences(text) == text


def test_strip_code_fences_only_opening_fence():
    """Opening fence but no closing — strip just the opener."""
    text = '```json\n{"key": "value"}'
    out = _strip_code_fences(text)
    assert out == '{"key": "value"}'


def test_strip_code_fences_inline_open_close():
    """``` followed immediately by content (no newline) — special
    case."""
    text = "```{}```"
    out = _strip_code_fences(text)
    # The function strips the leading ``` (no newline) and trailing ```:
    assert "{}" in out
