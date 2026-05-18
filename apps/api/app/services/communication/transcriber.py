"""Meeting transcription using faster-whisper."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Allowed base directories for audio file paths
_ALLOWED_AUDIO_DIRS = [
    Path(tempfile.gettempdir()).resolve(),
]


class MeetingTranscriber:
    """Transcribe meeting audio and extract action items."""

    def __init__(self, model_size: str = "base"):
        self._model = None
        self._model_size = model_size
        self._loaded = False

    def _ensure_model(self):
        """Lazy-load the whisper model."""
        if self._loaded:
            return
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size,
                device="cpu",
            )
            logger.info(
                "Whisper model loaded: %s",
                self._model_size,
            )
        except ImportError:
            logger.warning("faster-whisper not available, transcription disabled")
            self._model = None
        self._loaded = True

    async def transcribe(
        self,
        audio_path: str,
    ) -> dict:
        """Transcribe audio file and extract insights.

        Returns dict with transcript, summary,
        action_items, decisions, and duration_seconds.
        """
        # Validate audio_path to prevent path traversal
        resolved = Path(audio_path).resolve()
        if not any(
            str(resolved).startswith(str(allowed_dir)) for allowed_dir in _ALLOWED_AUDIO_DIRS
        ):
            raise ValueError(f"Audio path is outside allowed directories: {resolved}")

        self._ensure_model()

        if self._model is not None:
            return await self._model_transcribe(audio_path)
        return self._fallback()

    async def _model_transcribe(
        self,
        audio_path: str,
    ) -> dict:
        """Transcribe using faster-whisper model.

        Runs CPU-intensive transcription in a thread executor to avoid
        blocking the async event loop.
        """
        try:
            import asyncio

            if self._model is None:
                raise RuntimeError("Transcriber model not initialized — install faster-whisper")
            model = self._model  # narrow for mypy
            loop = asyncio.get_running_loop()
            segments, info = await loop.run_in_executor(
                None, lambda: model.transcribe(audio_path, beam_size=5)
            )
            full_text_parts = []
            for segment in segments:
                full_text_parts.append(segment.text.strip())

            transcript = " ".join(full_text_parts)

            # Extract action items (simplified pattern)
            action_items = _extract_action_items(transcript)
            decisions = _extract_decisions(transcript)

            agenda_items = _extract_agenda_items(transcript, action_items, decisions)

            return {
                "transcript": transcript,
                "summary": _summarize(transcript),
                "action_items": action_items,
                "decisions": decisions,
                "agenda_items": agenda_items,
                "duration_seconds": info.duration,
            }
        except Exception as exc:
            logger.warning(f"Whisper transcription failed, using fallback: {exc}", exc_info=True)
            return self._fallback()

    def _fallback(self) -> dict:
        """Return empty result when model unavailable."""
        return {
            "transcript": "",
            "summary": "Transcription unavailable",
            "action_items": [],
            "decisions": [],
            "agenda_items": [],
            "duration_seconds": 0.0,
        }


def _extract_action_items(text: str) -> list[dict]:
    """Extract action items from transcript text."""
    items = []
    keywords = [
        "action item",
        "we need to",
        "follow up",
        "will do",
        "assigned to",
        "take care of",
        "needs to",
        "should be done",
    ]
    sentences = text.split(".")
    for sentence in sentences:
        lower = sentence.lower().strip()
        if any(kw in lower for kw in keywords):
            items.append(
                {
                    "description": sentence.strip(),
                    "assignee": None,
                    "due_date": None,
                    "status": "pending",
                }
            )
    return items


def _extract_decisions(text: str) -> list[dict]:
    """Extract decisions from transcript text."""
    decisions = []
    keywords = [
        "decided",
        "agreed",
        "approved",
        "will proceed",
        "selected",
        "chosen",
    ]
    sentences = text.split(".")
    for sentence in sentences:
        lower = sentence.lower().strip()
        if any(kw in lower for kw in keywords):
            decisions.append(
                {
                    "description": sentence.strip(),
                }
            )
    return decisions


def _summarize(text: str) -> str:
    """Generate a basic summary of transcript."""
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if not sentences:
        return "No content to summarize."
    # Take first 3 sentences as summary
    summary_sentences = sentences[:3]
    return ". ".join(summary_sentences) + "."


def _extract_agenda_items(
    transcript: str,
    action_items: list[dict],
    decisions: list[dict],
) -> list[dict]:
    """Generate structured agenda items from transcript content.

    Groups sentences into topic blocks and cross-references with
    extracted action items and decisions.
    """
    if not transcript.strip():
        return []

    # Topic-indicator keywords (common in construction meetings)
    topic_keywords = [
        "next item",
        "moving on",
        "let's discuss",
        "regarding",
        "about the",
        "update on",
        "status of",
        "schedule",
        "budget",
        "safety",
        "rfi",
        "submittal",
        "change order",
        "punch list",
    ]

    sentences = [s.strip() for s in transcript.split(".") if s.strip()]
    if not sentences:
        return []

    # Group sentences into chunks — a new topic starts at topic keywords
    topics: list[list[str]] = []
    current_chunk: list[str] = []

    for sentence in sentences:
        lower = sentence.lower()
        is_topic_start = any(kw in lower for kw in topic_keywords)
        if is_topic_start and current_chunk:
            topics.append(current_chunk)
            current_chunk = []
        current_chunk.append(sentence)

    if current_chunk:
        topics.append(current_chunk)

    # If no topic splits found, treat entire transcript as one topic
    if not topics:
        topics = [sentences]

    # Build agenda items
    agenda_items: list[dict] = []
    decision_descs = {d.get("description", "").lower() for d in decisions}
    action_descs = {a.get("description", "").lower() for a in action_items}

    for chunk in topics:
        if not chunk:
            continue

        topic_title = chunk[0][:100]  # First sentence as topic
        discussion = ". ".join(chunk)

        # Find matching decision/action item
        matched_decision = None
        matched_action = None
        matched_responsible = None
        matched_due_date = None

        for sentence in chunk:
            lower = sentence.lower().strip()
            if lower in decision_descs:
                matched_decision = sentence
            if lower in action_descs:
                matched_action = sentence
                # Find the action item to get assignee/due_date
                for ai in action_items:
                    if ai.get("description", "").lower() == lower:
                        matched_responsible = ai.get("assignee")
                        matched_due_date = ai.get("due_date")
                        break

        agenda_items.append(
            {
                "topic": topic_title,
                "discussion": discussion,
                "decision": matched_decision,
                "action_item": matched_action,
                "responsible_party": matched_responsible,
                "due_date": matched_due_date,
            }
        )

    return agenda_items
