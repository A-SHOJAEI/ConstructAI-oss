"""RAGAS integration for RAG quality metrics."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RAGEvaluator:
    """RAGAS integration for RAG quality metrics.

    Metrics: faithfulness, context_precision, answer_relevancy
    """

    async def evaluate(
        self,
        test_queries: list[dict],
    ) -> dict:
        """Run RAGAS evaluation.

        Args:
            test_queries: List of dicts with keys:
                - question: str
                - expected_answer: str
                - context: str
                - actual_answer: str (optional)

        Returns dict with metric scores.
        """
        if not test_queries:
            return {
                "faithfulness": 0.0,
                "context_precision": 0.0,
                "answer_relevancy": 0.0,
                "num_queries": 0,
            }

        faithfulness_scores = []
        precision_scores = []
        relevancy_scores = []

        for query in test_queries:
            f_score = self._score_faithfulness(query)
            p_score = self._score_context_precision(query)
            r_score = self._score_answer_relevancy(query)

            faithfulness_scores.append(f_score)
            precision_scores.append(p_score)
            relevancy_scores.append(r_score)

        n = len(test_queries)
        return {
            "faithfulness": round(
                sum(faithfulness_scores) / n,
                4,
            ),
            "context_precision": round(
                sum(precision_scores) / n,
                4,
            ),
            "answer_relevancy": round(
                sum(relevancy_scores) / n,
                4,
            ),
            "num_queries": n,
        }

    def _score_faithfulness(self, query: dict) -> float:
        """Score faithfulness of answer to context.

        In production, uses RAGAS faithfulness metric.
        Here uses word overlap heuristic.
        """
        context = query.get("context", "").lower()
        answer = query.get(
            "actual_answer",
            query.get("expected_answer", ""),
        ).lower()

        if not context or not answer:
            return 0.0

        answer_words = set(answer.split())
        context_words = set(context.split())

        if not answer_words:
            return 0.0

        overlap = len(answer_words & context_words)
        return min(1.0, overlap / len(answer_words))

    def _score_context_precision(
        self,
        query: dict,
    ) -> float:
        """Score precision of retrieved context.

        Measures how relevant the context is to the question.
        """
        context = query.get("context", "").lower()
        question = query.get("question", "").lower()

        if not context or not question:
            return 0.0

        q_words = set(question.split())
        c_words = set(context.split())

        if not q_words:
            return 0.0

        overlap = len(q_words & c_words)
        return min(1.0, overlap / len(q_words))

    def _score_answer_relevancy(
        self,
        query: dict,
    ) -> float:
        """Score relevancy of answer to question."""
        question = query.get("question", "").lower()
        answer = query.get(
            "actual_answer",
            query.get("expected_answer", ""),
        ).lower()

        if not question or not answer:
            return 0.0

        q_words = set(question.split())
        a_words = set(answer.split())

        if not q_words:
            return 0.0

        overlap = len(q_words & a_words)
        return min(1.0, overlap / len(q_words))
