"""Tests for the pure helpers in services/communication/transcriber.

The MeetingTranscriber.transcribe path-traversal validation is also
pinned here — that's the security-critical surface (audio file path
must resolve under the OS temp dir or it's rejected).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.services.communication.transcriber import (
    MeetingTranscriber,
    _extract_action_items,
    _extract_agenda_items,
    _extract_decisions,
    _summarize,
)

# =========================================================================
# _extract_action_items
# =========================================================================


def test_extract_action_items_picks_up_each_keyword():
    """Every keyword in the canonical list should trigger a hit."""
    text = (
        "We need to call the supplier. "
        "Action item is to revise the spec. "
        "John will follow up on the MEP coordination. "
        "The mason team will do the rebar layout. "
        "This is assigned to Sarah by Friday. "
        "Mike will take care of the punch list. "
        "The scope needs to be revised. "
        "The form review should be done by Tuesday."
    )
    items = _extract_action_items(text)
    # 8 sentences, each containing a keyword → 8 items.
    assert len(items) == 8
    for item in items:
        assert item["status"] == "pending"
        assert item["assignee"] is None
        assert item["due_date"] is None


def test_extract_action_items_ignores_no_keyword():
    text = "The weather was nice. The crew arrived on schedule. The pour finished."
    assert _extract_action_items(text) == []


def test_extract_action_items_keyword_match_is_case_insensitive():
    text = "ACTION ITEM: order the steel."
    items = _extract_action_items(text)
    assert len(items) == 1


def test_extract_action_items_strips_whitespace():
    text = "    we need to finish the slab    "
    items = _extract_action_items(text)
    assert items[0]["description"] == "we need to finish the slab"


def test_extract_action_items_handles_empty_input():
    assert _extract_action_items("") == []


# =========================================================================
# _extract_decisions
# =========================================================================


def test_extract_decisions_picks_up_each_keyword():
    text = (
        "We decided to use 4000 psi mix. "
        "Everyone agreed on the new schedule. "
        "The change order was approved. "
        "We will proceed with the alternate. "
        "Vendor B was selected. "
        "The eastern site was chosen as the staging area."
    )
    decisions = _extract_decisions(text)
    assert len(decisions) == 6


def test_extract_decisions_ignores_no_keyword():
    text = "The estimator reviewed the bid. Costs went up. Materials shipped."
    assert _extract_decisions(text) == []


def test_extract_decisions_handles_empty_input():
    assert _extract_decisions("") == []


# =========================================================================
# _summarize
# =========================================================================


def test_summarize_takes_first_three_sentences():
    text = "Alpha. Bravo. Charlie. Delta. Echo."
    out = _summarize(text)
    assert "Alpha" in out
    assert "Bravo" in out
    assert "Charlie" in out
    assert "Delta" not in out


def test_summarize_with_fewer_than_three_sentences_uses_all():
    text = "Only one sentence here."
    out = _summarize(text)
    assert "Only one sentence here" in out


def test_summarize_empty_input():
    assert _summarize("") == "No content to summarize."


def test_summarize_whitespace_only_input():
    assert _summarize("   .   .   .   ") == "No content to summarize."


def test_summarize_ends_with_period():
    text = "First. Second."
    out = _summarize(text)
    assert out.endswith(".")


# =========================================================================
# _extract_agenda_items
# =========================================================================


def test_extract_agenda_items_empty_transcript_returns_empty():
    assert _extract_agenda_items("", [], []) == []


def test_extract_agenda_items_no_topic_keywords_treats_as_one_topic():
    """When no topic-indicator keywords appear, the helper bundles
    everything into a single agenda item."""
    transcript = "We poured concrete. The pour went well. Crew was efficient."
    out = _extract_agenda_items(transcript, [], [])
    assert len(out) == 1
    # First sentence becomes the topic title.
    assert out[0]["topic"].startswith("We poured concrete")


def test_extract_agenda_items_splits_on_topic_keywords():
    transcript = (
        "We poured concrete. The pour went well. "
        "Next item: rebar inspection. Inspection passed. "
        "Moving on to safety. No incidents this week."
    )
    out = _extract_agenda_items(transcript, [], [])
    # 3 distinct topic blocks (concrete pour, rebar, safety).
    assert len(out) == 3
    topics = " ".join(item["topic"].lower() for item in out)
    assert "concrete" in topics
    assert "rebar" in topics
    assert "safety" in topics


def test_extract_agenda_items_topic_title_truncated_to_100_chars():
    long_first = "A" * 200 + ". next item: short."
    out = _extract_agenda_items(long_first, [], [])
    assert all(len(item["topic"]) <= 100 for item in out)


def test_extract_agenda_items_matches_decision_into_block():
    transcript = "We agreed to use vendor B. The price was right."
    decisions = [{"description": "We agreed to use vendor B"}]
    out = _extract_agenda_items(transcript, [], decisions)
    assert len(out) == 1
    assert "vendor B" in (out[0]["decision"] or "")


def test_extract_agenda_items_matches_action_into_block_and_propagates_metadata():
    transcript = "We need to call the supplier. The crew arrived."
    action_items = [
        {
            "description": "We need to call the supplier",
            "assignee": "John",
            "due_date": "2026-05-01",
            "status": "pending",
        }
    ]
    out = _extract_agenda_items(transcript, action_items, [])
    assert len(out) == 1
    item = out[0]
    assert "supplier" in (item["action_item"] or "").lower()
    assert item["responsible_party"] == "John"
    assert item["due_date"] == "2026-05-01"


def test_extract_agenda_items_action_with_no_match_leaves_metadata_none():
    transcript = "We poured concrete. Crew was efficient."
    # action_items doesn't match anything in transcript:
    action_items = [{"description": "Send out the invoice", "assignee": "Tom"}]
    out = _extract_agenda_items(transcript, action_items, [])
    assert out[0]["action_item"] is None
    assert out[0]["responsible_party"] is None


# =========================================================================
# MeetingTranscriber — path validation
# =========================================================================


def test_transcribe_rejects_path_outside_temp_dir():
    """[security] Audio paths must resolve under the OS temp dir to
    block path-traversal — the transcriber will gladly load any file
    the OS will read otherwise (and Whisper will read raw audio data,
    so a malicious path could leak adjacent file content via timing
    or model behavior).
    """
    t = MeetingTranscriber(model_size="base")
    with pytest.raises(ValueError, match="outside allowed directories"):
        asyncio.run(t.transcribe("/etc/passwd"))


def test_transcribe_rejects_traversal_attempt():
    t = MeetingTranscriber(model_size="base")
    bad_path = str(Path(tempfile.gettempdir()) / ".." / ".." / "etc" / "passwd")
    with pytest.raises(ValueError, match="outside allowed directories"):
        asyncio.run(t.transcribe(bad_path))


def test_transcribe_falls_back_when_model_unavailable():
    """[default-deny] If faster-whisper isn't installed, ``_ensure_model``
    should mark the transcriber unloaded with model=None, and any call
    inside the temp dir should return the fallback dict (not crash)."""
    t = MeetingTranscriber(model_size="base")
    # Force the "no model" branch deterministically:
    t._model = None
    t._loaded = True

    # A path that resolves under the OS temp dir — passes validation.
    audio = Path(tempfile.gettempdir()) / "fake-audio.wav"
    out = asyncio.run(t.transcribe(str(audio)))
    assert out["transcript"] == ""
    assert out["summary"] == "Transcription unavailable"
    assert out["action_items"] == []
    assert out["decisions"] == []
    assert out["agenda_items"] == []
    assert out["duration_seconds"] == 0.0


def test_fallback_returns_complete_schema():
    """The fallback dict must match the schema of the success case so
    downstream code doesn't break on missing keys."""
    t = MeetingTranscriber()
    out = t._fallback()
    expected_keys = {
        "transcript",
        "summary",
        "action_items",
        "decisions",
        "agenda_items",
        "duration_seconds",
    }
    assert set(out.keys()) == expected_keys
