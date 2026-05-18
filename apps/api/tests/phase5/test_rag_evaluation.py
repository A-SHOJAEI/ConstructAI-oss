"""Tests for RAGAS metric evaluation."""

from __future__ import annotations

from app.services.evaluation.rag_evaluation import (
    RAGEvaluator,
)
from tests.fixtures.sample_evaluation_data import (
    SAMPLE_RAG_QUERIES,
)


class TestRAGEvaluator:
    async def test_evaluate_queries(self):
        evaluator = RAGEvaluator()
        result = await evaluator.evaluate(SAMPLE_RAG_QUERIES)
        assert "faithfulness" in result
        assert "context_precision" in result
        assert "answer_relevancy" in result
        assert result["num_queries"] == 3

    async def test_scores_in_range(self):
        evaluator = RAGEvaluator()
        result = await evaluator.evaluate(SAMPLE_RAG_QUERIES)
        assert 0.0 <= result["faithfulness"] <= 1.0
        assert 0.0 <= result["context_precision"] <= 1.0
        assert 0.0 <= result["answer_relevancy"] <= 1.0

    async def test_empty_queries(self):
        evaluator = RAGEvaluator()
        result = await evaluator.evaluate([])
        assert result["num_queries"] == 0
        assert result["faithfulness"] == 0.0

    async def test_faithfulness_with_context(self):
        evaluator = RAGEvaluator()
        queries = [
            {
                "question": "What is the strength?",
                "context": "Concrete strength is 4000 PSI.",
                "expected_answer": "4000 PSI",
                "actual_answer": "The concrete strength is 4000 PSI.",
            }
        ]
        result = await evaluator.evaluate(queries)
        assert result["faithfulness"] > 0.0
