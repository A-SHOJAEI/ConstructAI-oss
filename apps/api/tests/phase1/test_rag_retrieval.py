"""Phase 1: RAG retrieval pipeline tests.

Tests for hybrid search (vector + BM25), Reciprocal Rank Fusion, Cohere
reranking, answer generation with citations, and graceful no-context handling.
All external API calls (Voyage AI, Cohere, OpenAI) are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rag.retrieval import _reciprocal_rank_fusion
from tests.fixtures.mock_responses import (
    MOCK_LLM_ANSWER_RESPONSE,
    MOCK_VOYAGE_EMBEDDING,
    MockCohereRerankResponse,
)


def _make_result(chunk_id: str, content: str, score: float, **extra) -> dict:
    """Create a minimal retrieval result dict for testing."""
    return {
        "chunk_id": chunk_id,
        "content": content,
        "document_id": str(uuid.uuid4()),
        "document_title": "Test Spec",
        "page_number": 1,
        "section_hierarchy": ["Section 03 30 00"],
        "csi_section": "03 30 00",
        "score": score,
        **extra,
    }


class TestRagRetrieval:
    """Tests for the RAG retrieval pipeline."""

    @patch("app.services.rag.retrieval.bm25_search")
    @patch("app.services.rag.retrieval.vector_search")
    async def test_hybrid_search_returns_relevant_results(
        self, mock_vector_search, mock_bm25_search, db_session
    ):
        """hybrid_search should combine vector and BM25 results and return ranked output."""
        from app.services.rag.retrieval import hybrid_search

        project_id = uuid.uuid4()

        # Set up vector search mock results
        mock_vector_search.return_value = [
            _make_result("chunk-v1", "Concrete strength 4000 psi", 0.92),
            _make_result("chunk-v2", "Portland cement Type I/II", 0.85),
            _make_result("chunk-v3", "ASTM C150 specification", 0.78),
        ]

        # Set up BM25 search mock results (partially overlapping)
        mock_bm25_search.return_value = [
            _make_result("chunk-v1", "Concrete strength 4000 psi", 0.88),
            _make_result("chunk-b1", "Aggregate ASTM C33", 0.75),
            _make_result("chunk-v2", "Portland cement Type I/II", 0.70),
        ]

        query_embedding = MOCK_VOYAGE_EMBEDDING
        results = await hybrid_search(
            db_session, "concrete strength", query_embedding, project_id, limit=5
        )

        assert len(results) >= 1, "Should return at least one result"
        assert len(results) <= 5, "Should respect limit"

        # All results should have required keys.
        for r in results:
            assert "chunk_id" in r
            assert "content" in r
            assert "rrf_score" in r

        # chunk-v1 appears in both lists so it should be ranked highly.
        chunk_ids = [r["chunk_id"] for r in results]
        assert "chunk-v1" in chunk_ids, (
            "Chunk appearing in both vector and BM25 results should be in merged output"
        )

    def test_rrf_combines_rankings_correctly(self):
        """Reciprocal Rank Fusion should merge two ranked lists with correct scoring."""
        list_a = [
            _make_result("c1", "doc A rank 1", 0.95),
            _make_result("c2", "doc A rank 2", 0.80),
            _make_result("c3", "doc A rank 3", 0.65),
        ]

        list_b = [
            _make_result("c3", "doc B rank 1", 0.90),
            _make_result("c1", "doc B rank 2", 0.75),
            _make_result("c4", "doc B rank 3", 0.60),
        ]

        merged = _reciprocal_rank_fusion([list_a, list_b], k=60)

        assert len(merged) >= 1
        # Every result should have an rrf_score.
        for r in merged:
            assert "rrf_score" in r
            assert r["rrf_score"] > 0

        # c1 and c3 appear in both lists so they should have higher RRF scores
        # than c2 and c4 which appear in only one list.
        scores_by_id = {r["chunk_id"]: r["rrf_score"] for r in merged}

        # c1: rank 1 in A (1/(60+1)) + rank 2 in B (1/(60+2))
        # c2: rank 2 in A only (1/(60+2))
        # c3: rank 3 in A (1/(60+3)) + rank 1 in B (1/(60+1))
        # c4: rank 3 in B only (1/(60+3))
        assert scores_by_id["c1"] > scores_by_id["c2"], (
            "c1 (in both lists) should score higher than c2 (in one list)"
        )
        assert scores_by_id["c3"] > scores_by_id["c4"], (
            "c3 (in both lists) should score higher than c4 (in one list)"
        )

    @patch("app.services.rag.reranker._get_cohere_client")
    async def test_reranker_improves_precision(self, mock_get_client):
        """Cohere reranker should reorder results by relevance score."""
        from app.services.rag.reranker import rerank
        from tests.fixtures.mock_responses import _MockRerankResult

        # Original order: c1, c2, c3
        results = [
            _make_result("c1", "Irrelevant content about painting", 0.7),
            _make_result("c2", "Slightly relevant concrete content", 0.6),
            _make_result("c3", "Highly relevant concrete strength 4000 psi", 0.5),
        ]

        # Mock reranker to place c3 first (index 2), then c1 (index 0)
        mock_client = AsyncMock()
        mock_client.rerank = AsyncMock(
            return_value=MockCohereRerankResponse(
                results=[
                    _MockRerankResult(index=2, relevance_score=0.95),
                    _MockRerankResult(index=0, relevance_score=0.60),
                    _MockRerankResult(index=1, relevance_score=0.30),
                ]
            )
        )
        mock_get_client.return_value = mock_client

        reranked = await rerank("concrete compressive strength", results, top_n=3)

        assert len(reranked) >= 1
        # After reranking, the most relevant chunk should be first.
        assert reranked[0]["chunk_id"] == "c3", (
            f"Expected c3 at top after reranking, got {reranked[0]['chunk_id']}"
        )
        # Each reranked result should have a rerank_score.
        for r in reranked:
            assert "rerank_score" in r
            assert 0.0 <= r["rerank_score"] <= 1.0

    @patch("app.services.rag.generator.ChatOpenAI")
    async def test_rag_answer_includes_citations(self, mock_chat_class):
        """Generated answer should include source citations."""
        from app.services.rag.generator import generate_answer

        # Configure mock LLM to return a valid JSON answer.
        mock_llm_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ANSWER_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_chat_class.return_value = mock_llm_instance

        context_chunks = [
            {
                "content": "Concrete strength 4000 psi at 28 days per ASTM C39",
                "document_title": "Test Construction Specification",
                "page_number": 1,
                "section_hierarchy": ["Section 03 30 00"],
                "csi_section": "03 30 00",
            }
        ]

        result = await generate_answer("What is the concrete strength?", context_chunks)

        assert "answer" in result
        assert len(result["answer"]) > 0
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert "sources" in result
        assert len(result["sources"]) >= 1, "Answer should include at least one source citation"
        assert "model_used" in result

    async def test_no_context_returns_graceful_message(self):
        """When no context chunks are available, generate_answer should return
        a graceful 'no information' message rather than hallucinating."""
        from app.services.rag.generator import generate_answer

        result = await generate_answer("What is the rebar spacing?", [])

        assert "answer" in result
        assert result["confidence"] == 0.0
        assert result["sources"] == []
        # The answer should explicitly indicate insufficient information.
        answer_lower = result["answer"].lower()
        assert any(
            phrase in answer_lower
            for phrase in ["don't have enough", "no information", "not enough", "no relevant"]
        ), f"Expected a 'no information' message, got: {result['answer']}"
