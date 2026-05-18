"""Tests for the ingestion embedding helpers.

Pin the M-17 sanitization (NUL stripping + 32KB cap), batch
processing, the C-10 dimension-consistency check, and the
chunks/embeddings length-mismatch guard in store_chunk_embeddings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ingestion.embedder import (
    _MAX_CHARS_PER_CHUNK,
    BATCH_SIZE,
    _sanitize_for_embedding,
    embed_chunks,
    store_chunk_embeddings,
)

# =========================================================================
# Constants
# =========================================================================


def test_batch_size_is_128():
    """[contract] BATCH_SIZE pinned at 128 — Voyage AI's recommended
    batch size. Refactor must not silently change."""
    assert BATCH_SIZE == 128


def test_max_chars_per_chunk_32k():
    """[M-17] 32_000 char cap (~8k BPE tokens worst case)."""
    assert _MAX_CHARS_PER_CHUNK == 32_000


# =========================================================================
# _sanitize_for_embedding — M-17 protections
# =========================================================================


def test_sanitize_empty_string():
    assert _sanitize_for_embedding("") == ""


def test_sanitize_strips_whitespace():
    assert _sanitize_for_embedding("  hello world  ") == "hello world"


def test_sanitize_replaces_null_bytes():
    """[M-17] NUL bytes break Voyage AI's JSON encoding -> replaced
    with spaces."""
    out = _sanitize_for_embedding("hello\x00world")
    assert "\x00" not in out
    assert "hello" in out
    assert "world" in out


def test_sanitize_truncates_at_32k():
    """[M-17] Pathological chunks above 32KB get truncated. Pin
    that the cap fires (Voyage AI silently truncates above ~128K, we
    cap conservatively at 32K)."""
    huge = "a" * 50_000
    out = _sanitize_for_embedding(huge)
    assert len(out) == _MAX_CHARS_PER_CHUNK


def test_sanitize_under_cap_unchanged():
    """Below cap -> only stripped, no truncation."""
    text = "x" * 100
    assert _sanitize_for_embedding(text) == text


def test_sanitize_none_returns_empty():
    """[edge case] None/falsy input -> empty string (not crash)."""
    assert _sanitize_for_embedding(None) == ""


# =========================================================================
# embed_chunks — batching + sanitization
# =========================================================================


@pytest.mark.asyncio
async def test_embed_chunks_empty_list_returns_empty():
    out = await embed_chunks([])
    assert out == []


@pytest.mark.asyncio
async def test_embed_chunks_single_batch():
    """Below batch size -> one call to generate_embeddings."""
    fake_gen = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024])
    with patch("app.services.ingestion.embedder.generate_embeddings", fake_gen):
        out = await embed_chunks(["text 1", "text 2"])

    fake_gen.assert_called_once()
    assert len(out) == 2
    assert len(out[0]) == 1024


@pytest.mark.asyncio
async def test_embed_chunks_respects_batch_size():
    """[contract] Inputs > batch_size are split into multiple calls.
    Pin to catch a refactor that silently sends one giant request."""
    fake_gen = AsyncMock(side_effect=lambda texts: [[0.0] * 1024 for _ in texts])
    # 5 texts with batch_size=2 -> 3 calls (2+2+1):
    with patch("app.services.ingestion.embedder.generate_embeddings", fake_gen):
        out = await embed_chunks(["t"] * 5, batch_size=2)

    assert fake_gen.call_count == 3
    assert len(out) == 5


@pytest.mark.asyncio
async def test_embed_chunks_sanitizes_before_provider_call():
    """[M-17] Sanitization happens BEFORE the provider call, not
    after. Pin so a pathological chunk never reaches the API."""
    captured = []

    async def fake_gen(texts):
        captured.extend(texts)
        return [[0.0] * 1024 for _ in texts]

    with patch("app.services.ingestion.embedder.generate_embeddings", fake_gen):
        await embed_chunks(["  hello\x00world  ", "x" * 50_000])

    # NUL stripped, whitespace stripped:
    assert captured[0] == "hello world"
    # Long text truncated to cap:
    assert len(captured[1]) == _MAX_CHARS_PER_CHUNK


@pytest.mark.asyncio
async def test_embed_chunks_preserves_order():
    """[contract] Output embeddings align with input order."""

    async def fake_gen(texts):
        # Encode index in first dimension so we can verify ordering:
        return [[float(i)] + [0.0] * 1023 for i, _t in enumerate(texts)]

    with patch("app.services.ingestion.embedder.generate_embeddings", fake_gen):
        out = await embed_chunks(["a", "b", "c"], batch_size=10)

    # Single batch -> indexes 0, 1, 2 in order:
    assert out[0][0] == 0.0
    assert out[1][0] == 1.0
    assert out[2][0] == 2.0


# =========================================================================
# store_chunk_embeddings — C-10 invariants
# =========================================================================


def _make_chunk(chunk_id: str = "11111111-1111-1111-1111-111111111111"):
    """Build a minimal DocumentChunk-like object."""
    chunk = MagicMock()
    chunk.id = chunk_id
    return chunk


@pytest.mark.asyncio
async def test_store_chunk_embeddings_length_mismatch_raises():
    """[invariant] chunks length != embeddings length -> ValueError.
    Pin: silent zip truncation would write some chunks without
    embeddings."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.flush = AsyncMock(return_value=None)
    chunks = [_make_chunk()]
    embeddings = [[0.0] * 1024, [0.1] * 1024]  # 2 embeddings, 1 chunk

    with pytest.raises(ValueError, match="length mismatch"):
        await store_chunk_embeddings(db, chunks, embeddings)


@pytest.mark.asyncio
async def test_store_chunk_embeddings_mixed_dimensions_raises():
    """[C-10] Mixed embedding dimensions -> ValueError. Pin: pgvector
    column is fixed-dim; refactor must NOT silently insert mismatched
    rows."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.flush = AsyncMock(return_value=None)
    chunks = [_make_chunk("a"), _make_chunk("b")]
    embeddings = [[0.0] * 1024, [0.0] * 768]  # different dims

    with pytest.raises(ValueError, match="mixed dimensions"):
        await store_chunk_embeddings(db, chunks, embeddings)


@pytest.mark.asyncio
async def test_store_chunk_embeddings_returns_count():
    """Returns the number of rows inserted."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.flush = AsyncMock(return_value=None)

    chunks = [_make_chunk(f"id-{i}") for i in range(3)]
    embeddings = [[float(i)] * 1024 for i in range(3)]

    out = await store_chunk_embeddings(db, chunks, embeddings, model_name="test-model")
    assert out == 3
    assert db.execute.await_count == 3
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_chunk_embeddings_uses_active_model_when_none():
    """[fallback] model_name=None -> active model from rag.embeddings."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.flush = AsyncMock(return_value=None)

    captured_models = []

    async def capture_execute(stmt, params):
        captured_models.append(params["model_name"])

    db.execute = capture_execute

    chunks = [_make_chunk("a")]
    embeddings = [[0.0] * 1024]

    with patch(
        "app.services.ingestion.embedder.get_active_model_name",
        return_value="active-bge-large",
    ):
        await store_chunk_embeddings(db, chunks, embeddings)

    assert captured_models == ["active-bge-large"]


@pytest.mark.asyncio
async def test_store_chunk_embeddings_formats_vector_as_pgvector_string():
    """[contract] Embedding written as '[v1, v2, ...]' for pgvector
    column. Pin so a refactor doesn't silently change to JSON or
    array literal."""
    captured_params = []

    async def capture_execute(stmt, params):
        captured_params.append(params)

    db = MagicMock()
    db.execute = capture_execute
    db.flush = AsyncMock(return_value=None)

    chunks = [_make_chunk("a")]
    embeddings = [[0.1, 0.2, 0.3]]

    await store_chunk_embeddings(db, chunks, embeddings, model_name="m")

    vec = captured_params[0]["embedding"]
    assert vec.startswith("[")
    assert vec.endswith("]")
    assert "0.1" in vec and "0.2" in vec and "0.3" in vec


@pytest.mark.asyncio
async def test_store_chunk_embeddings_records_dimension():
    """[C-10] embedding_dim is stored alongside the vector."""
    captured_params = []

    async def capture_execute(stmt, params):
        captured_params.append(params)

    db = MagicMock()
    db.execute = capture_execute
    db.flush = AsyncMock(return_value=None)

    chunks = [_make_chunk("a")]
    embeddings = [[0.0] * 1024]

    await store_chunk_embeddings(db, chunks, embeddings, model_name="m")
    assert captured_params[0]["embedding_dim"] == 1024


@pytest.mark.asyncio
async def test_store_chunk_embeddings_empty_lists():
    """Empty chunks AND empty embeddings -> 0 inserted (don't crash)."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.flush = AsyncMock(return_value=None)

    out = await store_chunk_embeddings(db, [], [], model_name="m")
    assert out == 0
    assert db.execute.await_count == 0
