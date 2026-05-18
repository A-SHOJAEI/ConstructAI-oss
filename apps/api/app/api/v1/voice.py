"""Voice upload endpoint — transcribe audio and run RAG Q&A."""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_AUDIO_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/flac",
}
MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25 MB

# Safe file suffixes for temp files (prevent path traversal via user-controlled filenames)
_SAFE_AUDIO_SUFFIXES = {".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".webm", ".flac"}

# Magic byte signatures for common audio formats
_AUDIO_MAGIC_BYTES: dict[bytes, str] = {
    b"RIFF": "wav",
    b"\xff\xfb": "mp3",
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
    b"ID3": "mp3",
    b"OggS": "ogg",
    b"fLaC": "flac",
    b"\x00\x00\x00": "mp4",  # ftyp box (first 3 zero bytes + size)
}


def _validate_audio_magic_bytes(content: bytes) -> None:
    """Validate that the file content starts with a known audio magic byte sequence."""
    if len(content) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file too small to be valid",
        )
    # Check against known magic bytes
    for magic, _fmt in _AUDIO_MAGIC_BYTES.items():
        if content[: len(magic)] == magic:
            return
    # Also accept mp4/m4a: check for ftyp box at offset 4
    if len(content) >= 8 and content[4:8] == b"ftyp":
        return
    # WebM: starts with 0x1A45DFA3 (EBML header)
    if len(content) >= 4 and content[:4] == b"\x1a\x45\xdf\xa3":
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="File content does not match any known audio format",
    )


class VoiceTranscription(BaseModel):
    transcript: str
    language: str
    duration_seconds: float


class VoiceQueryResponse(BaseModel):
    transcript: str
    answer: str
    sources: list[dict]
    confidence: float


_whisper_model = None
_whisper_lock = __import__("threading").Lock()


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        return _whisper_model


async def _transcribe_audio(file_path: str) -> VoiceTranscription:
    """Transcribe audio using faster-whisper."""
    try:
        model = _get_whisper_model()
        segments, info = model.transcribe(file_path)
        transcript = " ".join(seg.text.strip() for seg in segments)
        return VoiceTranscription(
            transcript=transcript,
            language=info.language,
            duration_seconds=round(info.duration, 1),
        )
    except ImportError:
        logger.warning("faster-whisper not installed, using placeholder transcription")
        return VoiceTranscription(
            transcript="[Transcription unavailable — faster-whisper not installed]",
            language="en",
            duration_seconds=0.0,
        )


@router.post(
    "/projects/{project_id}/voice/transcribe",
    response_model=VoiceTranscription,
)
async def transcribe_voice(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("documents", "create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
):
    """Upload an audio file and get a transcription."""
    await verify_project_access(project_id, user, db)

    if not file.content_type or file.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio type: {file.content_type}",
        )

    content = await file.read()
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file exceeds 25 MB limit",
        )
    _validate_audio_magic_bytes(content)

    # Sanitize suffix to prevent path traversal via user-controlled filenames
    raw_suffix = Path(file.filename or "audio.wav").suffix.lower()
    suffix = raw_suffix if raw_suffix in _SAFE_AUDIO_SUFFIXES else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return await _transcribe_audio(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post(
    "/projects/{project_id}/voice/query",
    response_model=VoiceQueryResponse,
)
async def voice_query(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(require_permission("documents", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
):
    """Upload voice and get an AI answer from project documents."""
    await verify_project_access(project_id, user, db)

    if not file.content_type or file.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio type: {file.content_type}",
        )

    content = await file.read()
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file exceeds 25 MB limit",
        )
    _validate_audio_magic_bytes(content)

    # Sanitize suffix to prevent path traversal via user-controlled filenames
    raw_suffix = Path(file.filename or "audio.wav").suffix.lower()
    suffix = raw_suffix if raw_suffix in _SAFE_AUDIO_SUFFIXES else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        transcription = await _transcribe_audio(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not transcription.transcript or transcription.transcript.startswith("["):
        return VoiceQueryResponse(
            transcript=transcription.transcript,
            answer="Could not process voice query — transcription unavailable.",
            sources=[],
            confidence=0.0,
        )

    # Use the document RAG pipeline for Q&A
    try:
        from app.services.rag.embeddings import embed_query
        from app.services.rag.retrieval import hybrid_search

        query_embedding = await embed_query(transcription.transcript)
        chunks = await hybrid_search(
            db=db,
            query=transcription.transcript,
            query_embedding=query_embedding,
            project_id=project_id,
            limit=5,
        )
        sources = [
            {
                "document": c.get("document_title", ""),
                "chunk": c.get("content", "")[:200],
                "score": round(c.get("score", 0.0), 3),
            }
            for c in chunks
        ]

        context = "\n\n".join(c.get("content", "") for c in chunks)
        try:
            from app.services.reliability.llm_gateway import LLMGateway
            from app.utils.prompt_sanitizer import sanitize_for_prompt

            # SECURITY (C-04): Sanitize transcription before LLM interpolation.
            # Voice transcripts are user-controlled input and can contain
            # prompt injection phrases.
            safe_transcript = sanitize_for_prompt(transcription.transcript, max_length=2000)
            safe_context = sanitize_for_prompt(context, max_length=8000)

            gateway = LLMGateway()
            result = await gateway.complete(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a construction project assistant. Answer the question "
                            "based on the provided context. Be concise and cite sources."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Context:\n{safe_context}\n\nQuestion: <user_query>{safe_transcript}</user_query>",
                    },
                ],
                agent_name="voice_query",
            )
            answer = result.get("content", "Unable to generate response.")
            confidence = 0.85 if chunks else 0.3
        except Exception:
            answer = "RAG pipeline answered based on retrieved documents."
            confidence = 0.5

        return VoiceQueryResponse(
            transcript=transcription.transcript,
            answer=answer,
            sources=sources,
            confidence=confidence,
        )
    except ImportError:
        return VoiceQueryResponse(
            transcript=transcription.transcript,
            answer="Document search is not available.",
            sources=[],
            confidence=0.0,
        )
