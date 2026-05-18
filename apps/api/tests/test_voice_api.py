"""Tests for the voice upload / query API endpoints.

All external dependencies (Whisper, LLM, RAG) are mocked.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# WAV magic bytes: "RIFF" header
_WAV_HEADER = (
    b"RIFF"
    + (100).to_bytes(4, "little")  # file size placeholder
    + b"WAVE"
    + b"fmt "
    + (16).to_bytes(4, "little")  # chunk size
    + (1).to_bytes(2, "little")  # PCM
    + (1).to_bytes(2, "little")  # mono
    + (16000).to_bytes(4, "little")  # sample rate
    + (32000).to_bytes(4, "little")  # byte rate
    + (2).to_bytes(2, "little")  # block align
    + (16).to_bytes(2, "little")  # bits per sample
    + b"data"
    + (64).to_bytes(4, "little")
    + b"\x00" * 64
)

# Not audio -- a PNG header
_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_project(client, auth_headers):
    """Helper coroutine: create a project and return its ID."""

    async def _create():
        resp = await client.post(
            "/api/v1/projects/",
            json={"name": "Voice Test Project"},
            headers=auth_headers,
        )
        return resp.json()["id"]

    return _create()


# ---------------------------------------------------------------------------
# Transcribe endpoint
# ---------------------------------------------------------------------------


class TestVoiceTranscribe:
    """POST /projects/{pid}/voice/transcribe"""

    @pytest.mark.asyncio
    async def test_valid_audio_accepted(self, client, auth_headers):
        """A valid WAV upload should return a transcription object."""
        project_id = await _make_project(client, auth_headers)

        with patch(
            "app.api.v1.voice._transcribe_audio",
            new_callable=AsyncMock,
        ) as mock_transcribe:
            mock_transcribe.return_value = MagicMock(
                transcript="hello world",
                language="en",
                duration_seconds=1.5,
                # Make pydantic serialization work
                model_dump=lambda **_: {
                    "transcript": "hello world",
                    "language": "en",
                    "duration_seconds": 1.5,
                },
            )

            resp = await client.post(
                f"/api/v1/projects/{project_id}/voice/transcribe",
                files={"file": ("test.wav", io.BytesIO(_WAV_HEADER), "audio/wav")},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "transcript" in data

    @pytest.mark.asyncio
    async def test_invalid_content_type_rejected(self, client, auth_headers):
        """Non-audio content types should be rejected with 400."""
        project_id = await _make_project(client, auth_headers)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/voice/transcribe",
            files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
            headers=auth_headers,
        )

        assert resp.status_code == 400
        assert "unsupported audio type" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self, client, auth_headers):
        """Files exceeding 25 MB should be rejected with 413."""
        project_id = await _make_project(client, auth_headers)

        # Build a >25 MB payload with a valid WAV header
        big_payload = _WAV_HEADER + b"\x00" * (26 * 1024 * 1024)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/voice/transcribe",
            files={"file": ("big.wav", io.BytesIO(big_payload), "audio/wav")},
            headers=auth_headers,
        )

        assert resp.status_code == 413
        assert "25 mb" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_magic_byte_validation_rejects_non_audio(self, client, auth_headers):
        """Files that claim audio/wav but have PNG magic bytes should be rejected."""
        project_id = await _make_project(client, auth_headers)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/voice/transcribe",
            files={"file": ("fake.wav", io.BytesIO(_PNG_HEADER), "audio/wav")},
            headers=auth_headers,
        )

        assert resp.status_code == 400
        assert "does not match" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Voice query endpoint
# ---------------------------------------------------------------------------


class TestVoiceQuery:
    """POST /projects/{pid}/voice/query"""

    @pytest.mark.asyncio
    async def test_voice_query_calls_sanitizer_before_llm(self, client, auth_headers):
        """The transcription text must be sanitized before interpolation into
        the LLM prompt to mitigate prompt injection."""
        project_id = await _make_project(client, auth_headers)

        fake_transcription = MagicMock(
            transcript="What is the concrete spec?",
            language="en",
            duration_seconds=2.0,
        )

        mock_transcribe = patch(
            "app.api.v1.voice._transcribe_audio",
            new_callable=AsyncMock,
            return_value=fake_transcription,
        )
        mock_embed_q = patch(
            "app.services.rag.embeddings.embed_query",
            new_callable=AsyncMock,
            return_value=[0.1] * 1024,
        )
        mock_hs = patch(
            "app.services.rag.retrieval.hybrid_search",
            new_callable=AsyncMock,
            return_value=[
                {"document_title": "Spec", "content": "Concrete spec text", "score": 0.9},
            ],
        )
        mock_gw_patch = patch(
            "app.services.reliability.llm_gateway.LLMGateway",
        )
        mock_san = patch(
            "app.utils.prompt_sanitizer.sanitize_for_prompt",
            side_effect=lambda t, **kw: t,
        )

        with (
            mock_transcribe,
            mock_embed_q,
            mock_hs,
            mock_gw_patch as mock_gw_cls,
            mock_san as mock_sanitize,
        ):
            mock_gw = MagicMock()
            mock_gw.complete = AsyncMock(return_value={"content": "Here is the answer."})
            mock_gw_cls.return_value = mock_gw

            resp = await client.post(
                f"/api/v1/projects/{project_id}/voice/query",
                files={"file": ("q.wav", io.BytesIO(_WAV_HEADER), "audio/wav")},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["transcript"] == "What is the concrete spec?"
        assert "answer" in data
        # sanitize_for_prompt must have been called at least once (for the transcript)
        assert mock_sanitize.call_count >= 1

    @pytest.mark.asyncio
    async def test_voice_query_calls_hybrid_search(self, client, auth_headers):
        """hybrid_search must be called with the embedded query."""
        project_id = await _make_project(client, auth_headers)

        fake_transcription = MagicMock(
            transcript="Where are the safety barriers?",
            language="en",
            duration_seconds=1.0,
        )
        fake_embedding = [0.5] * 1024

        p_transcribe = patch(
            "app.api.v1.voice._transcribe_audio",
            new_callable=AsyncMock,
            return_value=fake_transcription,
        )
        p_embed = patch(
            "app.services.rag.embeddings.embed_query",
            new_callable=AsyncMock,
            return_value=fake_embedding,
        )
        p_search = patch(
            "app.services.rag.retrieval.hybrid_search",
            new_callable=AsyncMock,
            return_value=[],
        )
        p_gw = patch(
            "app.services.reliability.llm_gateway.LLMGateway",
        )
        p_sanitize = patch(
            "app.utils.prompt_sanitizer.sanitize_for_prompt",
            side_effect=lambda t, **kw: t,
        )

        with (
            p_transcribe,
            p_embed,
            p_search as mock_search,
            p_gw as mock_gw_cls,
            p_sanitize,
        ):
            mock_gw = MagicMock()
            mock_gw.complete = AsyncMock(return_value={"content": "No results."})
            mock_gw_cls.return_value = mock_gw

            resp = await client.post(
                f"/api/v1/projects/{project_id}/voice/query",
                files={"file": ("q.wav", io.BytesIO(_WAV_HEADER), "audio/wav")},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        # hybrid_search should have been called with the embedding
        mock_search.assert_awaited_once()
        call_kwargs = mock_search.call_args
        kw = call_kwargs.kwargs or call_kwargs[1]
        assert kw.get("query_embedding") == fake_embedding

    @pytest.mark.asyncio
    async def test_voice_query_without_auth_returns_401(self, client):
        """Unauthenticated POST is rejected by the CSRF middleware before
        the auth dependency runs (no Bearer header, no CSRF cookie/token).
        """
        fake_pid = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/projects/{fake_pid}/voice/query",
            files={"file": ("q.wav", io.BytesIO(_WAV_HEADER), "audio/wav")},
        )
        assert resp.status_code == 403
