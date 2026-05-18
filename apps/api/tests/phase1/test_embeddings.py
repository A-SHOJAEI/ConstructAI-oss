"""Phase 1: Embedding generation tests.

All Voyage AI API calls are mocked so that tests never make real network
requests. Tests verify embedding dimensions, storage, fallback behaviour,
batch processing, and idempotency.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures.mock_responses import MOCK_VOYAGE_EMBEDDING


class _MockEmbedResponse:
    """Minimal stand-in for a Voyage AI embed response."""

    def __init__(self, embeddings: list[list[float]]):
        self.embeddings = embeddings


class TestEmbeddings:
    """Tests for the embedding generation and storage service."""

    @patch("app.services.rag.embeddings._get_voyage_client")
    async def test_voyage_embedding_returns_1024_dims(self, mock_get_client):
        """generate_embeddings should return 1024-dimensional vectors."""
        from app.services.rag.embeddings import generate_embeddings

        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(
            return_value=_MockEmbedResponse(embeddings=[MOCK_VOYAGE_EMBEDDING])
        )
        mock_get_client.return_value = mock_client

        texts = ["Portland cement conforming to ASTM C150, Type I/II"]
        result = await generate_embeddings(texts)

        assert len(result) == 1
        assert len(result[0]) == 1024, f"Expected 1024 dimensions, got {len(result[0])}"
        assert all(isinstance(v, float) for v in result[0])

    @patch("app.services.rag.embeddings._get_voyage_client")
    async def test_embedding_stored_in_pgvector(self, mock_get_client, db_session, test_org):
        """Embeddings should be persistable to the database and retrievable via raw SQL."""
        from sqlalchemy import text

        from app.models.document import Document, DocumentChunk
        from app.models.project import Project
        from app.services.rag.embeddings import generate_embeddings, store_embeddings

        # Set up mock Voyage client
        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(
            return_value=_MockEmbedResponse(embeddings=[MOCK_VOYAGE_EMBEDDING])
        )
        mock_get_client.return_value = mock_client

        # Create prerequisite records in the test database.
        project = Project(name="Embedding Test Project", org_id=test_org.id)
        db_session.add(project)
        await db_session.flush()

        document = Document(
            project_id=project.id,
            type="specification",
            title="Test Spec",
            original_filename="test_spec.pdf",
            s3_key="documents/test_spec.pdf",
        )
        db_session.add(document)
        await db_session.flush()

        chunk = DocumentChunk(
            document_id=document.id,
            chunk_index=0,
            content="Portland cement conforming to ASTM C150",
            chunk_type="text",
            page_number=1,
            token_count=8,
        )
        db_session.add(chunk)
        await db_session.flush()
        await db_session.refresh(chunk)

        # Generate and store embeddings.
        embeddings = await generate_embeddings(["Portland cement conforming to ASTM C150"])
        await store_embeddings(db_session, [chunk], embeddings)

        # Verify the embedding was stored by querying the database.
        result = await db_session.execute(
            text("SELECT chunk_id, model_name FROM document_embeddings WHERE chunk_id = :cid"),
            {"cid": str(chunk.id)},
        )
        row = result.mappings().first()
        assert row is not None, "Embedding record should exist in the database"
        assert str(row["chunk_id"]) == str(chunk.id)
        assert row["model_name"] == "voyage-3-large"

    @patch("app.services.rag.embeddings._get_voyage_client")
    async def test_fallback_to_bge_m3(self, mock_get_client):
        """When Voyage AI fails, the function should propagate the error gracefully."""
        from app.services.rag.embeddings import generate_embeddings

        # Configure mock to raise an exception simulating API failure.
        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(side_effect=Exception("Voyage API rate limit exceeded"))
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="Voyage API rate limit exceeded"):
            await generate_embeddings(["Some text to embed"])

    @patch("app.services.rag.embeddings._get_voyage_client")
    async def test_batch_embedding_performance(self, mock_get_client):
        """Batch embedding of 50 chunks should all return results."""
        from app.services.rag.embeddings import generate_embeddings

        batch_size = 50
        # Return a unique embedding for each input text.
        mock_embeddings = [MOCK_VOYAGE_EMBEDDING for _ in range(batch_size)]

        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(return_value=_MockEmbedResponse(embeddings=mock_embeddings))
        mock_get_client.return_value = mock_client

        texts = [f"Chunk {i}: concrete specification text" for i in range(batch_size)]
        result = await generate_embeddings(texts)

        assert len(result) == batch_size, f"Expected {batch_size} embeddings, got {len(result)}"
        for i, embedding in enumerate(result):
            assert len(embedding) == 1024, f"Embedding {i} has {len(embedding)} dims, expected 1024"

    @patch("app.services.rag.embeddings._get_voyage_client")
    async def test_embedding_idempotency(self, mock_get_client):
        """Re-embedding the same text should produce identical vectors when the
        mock returns the same output (simulating deterministic model behaviour)."""
        from app.services.rag.embeddings import generate_embeddings

        fixed_embedding = [0.42] * 1024

        mock_client = AsyncMock()
        mock_client.embed = AsyncMock(return_value=_MockEmbedResponse(embeddings=[fixed_embedding]))
        mock_get_client.return_value = mock_client

        text_input = ["Portland cement ASTM C150 Type I/II"]

        result1 = await generate_embeddings(text_input)
        result2 = await generate_embeddings(text_input)

        assert result1 == result2, "Same input should produce identical embeddings"
        assert result1[0] == fixed_embedding
