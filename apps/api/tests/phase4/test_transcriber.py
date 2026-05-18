"""Tests for meeting transcription service."""

from __future__ import annotations

from app.services.communication.transcriber import (
    MeetingTranscriber,
    _extract_action_items,
    _extract_decisions,
    _summarize,
)


class TestTranscriber:
    async def test_fallback_transcription(self):
        # Use a path inside the OS temp dir so the path-traversal validator
        # accepts it. The file doesn't need to exist when the model isn't
        # loaded — the no-model fallback returns a synthetic result.
        import tempfile

        tmp_path = f"{tempfile.gettempdir()}/fake.wav"
        transcriber = MeetingTranscriber()
        result = await transcriber.transcribe(tmp_path)
        assert "transcript" in result
        assert "summary" in result
        assert "action_items" in result
        assert "decisions" in result
        assert "duration_seconds" in result

    def test_extract_action_items(self):
        text = (
            "We discussed the schedule. "
            "Action item: review the foundation design. "
            "Bob will do the concrete inspection."
        )
        items = _extract_action_items(text)
        assert len(items) >= 1
        assert items[0]["assignee"] is None

    def test_extract_decisions(self):
        text = (
            "After discussion, we decided to proceed "
            "with option B. The team agreed on the "
            "revised schedule."
        )
        decisions = _extract_decisions(text)
        assert len(decisions) >= 1

    def test_summarize(self):
        text = (
            "First point discussed. "
            "Second topic covered. "
            "Third item reviewed. "
            "Fourth matter addressed."
        )
        summary = _summarize(text)
        assert len(summary) > 0
        assert summary.endswith(".")

    def test_summarize_empty(self):
        assert _summarize("") == "No content to summarize."

    def test_extract_no_action_items(self):
        items = _extract_action_items("The weather was nice today.")
        assert len(items) == 0
