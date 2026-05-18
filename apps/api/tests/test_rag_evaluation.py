"""Tests for the RAGAS-style RAG quality evaluator.

Pin the 3 documented metrics (faithfulness / context_precision /
answer_relevancy), the 4-decimal rounding, the empty-input
short-circuit, and the actual_answer/expected_answer fallback.
"""

from __future__ import annotations

import pytest

from app.services.evaluation.rag_evaluation import RAGEvaluator


@pytest.fixture
def evaluator() -> RAGEvaluator:
    return RAGEvaluator()


# =========================================================================
# evaluate() — empty input
# =========================================================================


@pytest.mark.asyncio
async def test_evaluate_empty_queries_returns_zeros(evaluator):
    """Empty list short-circuits to all-zeros + num_queries=0
    (NOT a crash, NOT NaN from div-by-zero)."""
    out = await evaluator.evaluate([])
    assert out == {
        "faithfulness": 0.0,
        "context_precision": 0.0,
        "answer_relevancy": 0.0,
        "num_queries": 0,
    }


@pytest.mark.asyncio
async def test_evaluate_returns_canonical_metric_keys(evaluator):
    """[contract] Pin the 3 documented metric keys + num_queries.
    Refactor must NOT silently rename or drop a metric — downstream
    dashboards depend on these names."""
    out = await evaluator.evaluate([{"question": "x", "context": "x", "expected_answer": "x"}])
    assert set(out) == {"faithfulness", "context_precision", "answer_relevancy", "num_queries"}


@pytest.mark.asyncio
async def test_evaluate_rounds_to_4_decimals(evaluator):
    """Metric scores are rounded to 4 decimal places (consistent
    precision across dashboards)."""
    queries = [
        {"question": "q one two three", "context": "c", "expected_answer": "a"},
        {"question": "q one two three", "context": "c", "expected_answer": "a"},
        {"question": "q one two three", "context": "c", "expected_answer": "a"},
    ]
    out = await evaluator.evaluate(queries)
    # Each score is round(x, 4), so trailing-decimal pinning:
    for key in ("faithfulness", "context_precision", "answer_relevancy"):
        # Must be a float and either == 0.0 or have <=4 decimal places:
        s = out[key]
        # 4-decimal: max abs(s - round(s, 4)) < 1e-9:
        assert abs(s - round(s, 4)) < 1e-9


@pytest.mark.asyncio
async def test_evaluate_num_queries_matches_input(evaluator):
    queries = [{"question": "a", "context": "a", "expected_answer": "a"} for _ in range(5)]
    out = await evaluator.evaluate(queries)
    assert out["num_queries"] == 5


# =========================================================================
# _score_faithfulness — answer/context overlap
# =========================================================================


def test_faithfulness_full_overlap_one(evaluator):
    """All answer words in context -> 1.0."""
    q = {"context": "concrete is strong durable", "actual_answer": "concrete strong"}
    assert evaluator._score_faithfulness(q) == 1.0


def test_faithfulness_no_overlap_zero(evaluator):
    q = {"context": "concrete strong", "actual_answer": "vinyl flexible"}
    assert evaluator._score_faithfulness(q) == 0.0


def test_faithfulness_partial_overlap(evaluator):
    """3 unique answer words, 1 in context -> 1/3."""
    q = {"context": "alpha beta gamma", "actual_answer": "alpha delta epsilon"}
    score = evaluator._score_faithfulness(q)
    # 1 / 3 = 0.333...
    assert abs(score - 1 / 3) < 1e-6


def test_faithfulness_empty_context_zero(evaluator):
    """Empty context -> 0.0 (no comparison possible)."""
    q = {"context": "", "actual_answer": "concrete"}
    assert evaluator._score_faithfulness(q) == 0.0


def test_faithfulness_empty_answer_zero(evaluator):
    q = {"context": "concrete", "actual_answer": ""}
    assert evaluator._score_faithfulness(q) == 0.0


def test_faithfulness_uses_actual_answer_over_expected(evaluator):
    """[contract] When both actual_answer and expected_answer are
    set, actual_answer wins (we evaluate the model's response, not
    the ground truth)."""
    q = {
        "context": "concrete strong",
        "actual_answer": "concrete",  # in context -> high score
        "expected_answer": "vinyl",  # NOT in context -> would be 0
    }
    score = evaluator._score_faithfulness(q)
    assert score == 1.0  # because 'concrete' is in context


def test_faithfulness_falls_back_to_expected_when_actual_missing(evaluator):
    """[fallback] No actual_answer -> use expected_answer."""
    q = {"context": "concrete strong", "expected_answer": "concrete"}
    score = evaluator._score_faithfulness(q)
    assert score == 1.0


def test_faithfulness_case_insensitive(evaluator):
    """Words compared case-insensitively (CONCRETE == concrete)."""
    q = {"context": "CONCRETE STRONG", "actual_answer": "concrete strong"}
    assert evaluator._score_faithfulness(q) == 1.0


def test_faithfulness_clamps_to_one(evaluator):
    """[invariant] Score never exceeds 1.0 even when answer has
    duplicate words that all appear in context."""
    q = {"context": "alpha beta", "actual_answer": "alpha alpha alpha"}
    # set('alpha alpha alpha'.split()) = {'alpha'} -> 1 word, 1 in context -> 1.0
    assert evaluator._score_faithfulness(q) == 1.0


# =========================================================================
# _score_context_precision — question/context overlap
# =========================================================================


def test_context_precision_full_overlap_one(evaluator):
    q = {"question": "what is concrete", "context": "what is concrete strong durable"}
    score = evaluator._score_context_precision(q)
    assert score == 1.0


def test_context_precision_no_overlap_zero(evaluator):
    q = {"question": "what is concrete", "context": "vinyl flexible plastic"}
    assert evaluator._score_context_precision(q) == 0.0


def test_context_precision_empty_question_zero(evaluator):
    q = {"question": "", "context": "concrete"}
    assert evaluator._score_context_precision(q) == 0.0


def test_context_precision_empty_context_zero(evaluator):
    q = {"question": "what", "context": ""}
    assert evaluator._score_context_precision(q) == 0.0


# =========================================================================
# _score_answer_relevancy — question/answer overlap
# =========================================================================


def test_answer_relevancy_full_overlap_one(evaluator):
    q = {"question": "concrete strength", "actual_answer": "concrete strength is high"}
    assert evaluator._score_answer_relevancy(q) == 1.0


def test_answer_relevancy_partial(evaluator):
    """2 unique question words, 1 in answer -> 0.5."""
    q = {"question": "alpha beta", "actual_answer": "alpha gamma delta"}
    assert evaluator._score_answer_relevancy(q) == 0.5


def test_answer_relevancy_empty_question_zero(evaluator):
    q = {"question": "", "actual_answer": "concrete"}
    assert evaluator._score_answer_relevancy(q) == 0.0


def test_answer_relevancy_uses_actual_over_expected(evaluator):
    """Same fallback as faithfulness — actual_answer wins when present."""
    q = {
        "question": "what is concrete",
        "actual_answer": "concrete is strong",
        "expected_answer": "vinyl",
    }
    score = evaluator._score_answer_relevancy(q)
    # Question words: {what, is, concrete} (3)
    # actual_answer words: {concrete, is, strong} -> overlap {is, concrete} = 2
    # 2/3 ≈ 0.6667
    assert abs(score - 2 / 3) < 1e-6


# =========================================================================
# Integration via evaluate()
# =========================================================================


@pytest.mark.asyncio
async def test_evaluate_aggregates_per_query_scores(evaluator):
    """Aggregate is the mean of per-query scores."""
    queries = [
        # query 1: faithfulness=1.0 (full overlap), precision=1.0, relevancy=1.0
        {"question": "alpha beta", "context": "alpha beta gamma", "actual_answer": "alpha beta"},
        # query 2: faithfulness=0.0 (no overlap), precision=0.0, relevancy=0.0
        {"question": "delta", "context": "epsilon", "actual_answer": "zeta"},
    ]
    out = await evaluator.evaluate(queries)
    # Mean of [1.0, 0.0] = 0.5:
    assert out["faithfulness"] == 0.5
    assert out["context_precision"] == 0.5
    assert out["answer_relevancy"] == 0.5
    assert out["num_queries"] == 2
