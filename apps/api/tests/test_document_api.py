"""API-level integration tests for document endpoints.

Tests for listing, retrieving, searching, and asking questions about documents.
All external service calls (RAG pipeline, LLM) are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from app.models.document import Document
from app.models.project import Project

from tests.fixtures.mock_responses import (
    MOCK_LLM_ANSWER_RESPONSE,
    MOCK_VOYAGE_EMBEDDING,
)


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for document API tests."""
    project = Project(name="Doc API Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def test_document(db_session, test_project, test_user):
    """Create a test document record in the database."""
    doc = Document(
        project_id=test_project.id,
        type="specification",
        title="Test Concrete Specification",
        original_filename="concrete_spec.pdf",
        s3_key="documents/concrete_spec.pdf",
        file_size_bytes=102400,
        processing_status="complete",
        uploaded_by=test_user.id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)
    return doc


class TestDocumentApi:
    """Integration tests for document API endpoints."""

    async def test_list_documents(self, client, auth_headers, test_project, test_document):
        """GET /api/v1/documents/ should return a list of documents for the project."""
        response = await client.get(
            f"/api/v1/documents/?project_id={test_project.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

        # Find our test document in the list.
        doc_ids = [d["id"] for d in data["data"]]
        assert str(test_document.id) in doc_ids, (
            f"Test document {test_document.id} not found in response"
        )

    async def test_get_document(self, client, auth_headers, test_document):
        """GET /api/v1/documents/{id} should return the document details."""
        response = await client.get(
            f"/api/v1/documents/{test_document.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["id"] == str(test_document.id)
        assert data["title"] == "Test Concrete Specification"
        assert data["original_filename"] == "concrete_spec.pdf"
        assert data["processing_status"] == "complete"

    async def test_get_document_not_found(self, client, auth_headers):
        """GET /api/v1/documents/{nonexistent_id} should return 404."""
        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/documents/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404, (
            f"Expected 404, got {response.status_code}: {response.text}"
        )

    @patch("app.services.rag.embeddings._get_voyage_client")
    @patch("app.services.rag.retrieval.vector_search")
    @patch("app.services.rag.retrieval.bm25_search")
    @patch("app.services.rag.reranker.rerank", new_callable=AsyncMock)
    async def test_search_documents(
        self,
        mock_rerank,
        mock_bm25,
        mock_vector,
        mock_voyage_client,
        client,
        auth_headers,
        test_project,
        test_document,
    ):
        """POST /api/v1/documents/search should return search results."""
        # Mock the Voyage client for query embedding.
        mock_client = AsyncMock()
        mock_embed_response = MagicMock()
        mock_embed_response.embeddings = [MOCK_VOYAGE_EMBEDDING]
        mock_client.embed = AsyncMock(return_value=mock_embed_response)
        mock_voyage_client.return_value = mock_client

        # Mock vector and BM25 search results.
        mock_result = {
            "chunk_id": str(uuid.uuid4()),
            "content": "Concrete strength 4000 psi at 28 days",
            "document_id": str(test_document.id),
            "document_title": test_document.title,
            "page_number": 1,
            "section_hierarchy": ["Section 03 30 00"],
            "csi_section": "03 30 00",
            "score": 0.92,
        }
        mock_vector.return_value = [mock_result]
        mock_bm25.return_value = [mock_result]
        mock_rerank.return_value = [mock_result]

        response = await client.post(
            "/api/v1/documents/search",
            json={
                "query": "concrete strength requirements",
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "results" in data
        assert "query" in data
        assert isinstance(data["results"], list)

    @patch("langchain_openai.ChatOpenAI")
    @patch("app.services.rag.embeddings._get_voyage_client")
    @patch("app.services.rag.retrieval.vector_search")
    @patch("app.services.rag.retrieval.bm25_search")
    @patch("app.services.rag.reranker.rerank", new_callable=AsyncMock)
    async def test_ask_question(
        self,
        mock_rerank,
        mock_bm25,
        mock_vector,
        mock_voyage_client,
        mock_chat_class,
        client,
        auth_headers,
        test_project,
        test_document,
    ):
        """POST /api/v1/documents/ask should return an answer with sources."""
        # Mock Voyage client.
        mock_client = AsyncMock()
        mock_embed_response = MagicMock()
        mock_embed_response.embeddings = [MOCK_VOYAGE_EMBEDDING]
        mock_client.embed = AsyncMock(return_value=mock_embed_response)
        mock_voyage_client.return_value = mock_client

        # Mock retrieval results.
        mock_result = {
            "chunk_id": str(uuid.uuid4()),
            "content": "Concrete strength 4000 psi at 28 days per ASTM C39",
            "document_id": str(test_document.id),
            "document_title": test_document.title,
            "page_number": 1,
            "section_hierarchy": ["Section 03 30 00"],
            "csi_section": "03 30 00",
            "score": 0.92,
        }
        mock_vector.return_value = [mock_result]
        mock_bm25.return_value = [mock_result]
        mock_rerank.return_value = [mock_result]

        # Mock LLM.
        mock_llm_instance = AsyncMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = MOCK_LLM_ANSWER_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_llm_response)
        mock_chat_class.return_value = mock_llm_instance

        response = await client.post(
            "/api/v1/documents/ask",
            json={
                "question": "What is the minimum concrete compressive strength?",
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "answer" in data
        assert len(data["answer"]) > 0
        assert "confidence" in data
        assert "sources" in data
        assert "model_used" in data
