"""Tests for the communication agent LangGraph nodes.

Pin per-node behavior: the optional-audio short-circuit
(no_audio status, NOT a failure), the optional-RFI short-circuit
(no_rfi status), date parsing from ISO string, and per-node
error isolation.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.communication_agent import (
    build_communication_agent,
    generate_report_node,
    suggest_rfi_node,
    transcribe_meeting_node,
)

# =========================================================================
# generate_report_node
# =========================================================================


@pytest.mark.asyncio
async def test_generate_report_passes_iso_date_as_date_object():
    """[contract] State stores report_date as ISO string; node MUST
    convert to ``datetime.date`` before passing to the report
    generator (the generator's signature requires a date)."""
    captured = {}

    async def fake_gen(*, project_id, report_date, daily_log, evm_snapshot, safety_events):
        captured.update(
            {
                "project_id": project_id,
                "report_date": report_date,
                "daily_log": daily_log,
                "evm_snapshot": evm_snapshot,
                "safety_events": safety_events,
            }
        )
        return {"report_id": "r-1", "status": "ok"}

    state = {
        "project_id": "p-1",
        "report_date": "2026-04-26",
        "daily_log": {"weather": "sunny"},
        "evm_snapshot": {"cpi": 0.95},
        "safety_events": [{"id": "e-1"}],
    }
    with patch(
        "app.services.agents.communication_agent.generate_daily_report",
        fake_gen,
    ):
        out = await generate_report_node(state)

    assert isinstance(captured["report_date"], date)
    assert captured["report_date"] == date(2026, 4, 26)
    assert captured["daily_log"] == {"weather": "sunny"}
    assert captured["safety_events"] == [{"id": "e-1"}]
    assert out["status"] == "report_generated"
    assert out["report_results"]["report_id"] == "r-1"


@pytest.mark.asyncio
async def test_generate_report_default_safety_events_empty_list():
    """Missing safety_events -> [] (don't pass None to generator)."""
    captured = {}

    async def fake_gen(*, safety_events, **_kwargs):
        captured["safety_events"] = safety_events
        return {}

    state = {"project_id": "p-1", "report_date": "2026-04-26"}
    with patch(
        "app.services.agents.communication_agent.generate_daily_report",
        fake_gen,
    ):
        await generate_report_node(state)

    assert captured["safety_events"] == []


@pytest.mark.asyncio
async def test_generate_report_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("template missing")

    state = {"project_id": "p-1", "report_date": "2026-04-26"}
    with patch(
        "app.services.agents.communication_agent.generate_daily_report",
        boom,
    ):
        out = await generate_report_node(state)

    assert out["report_results"] is None
    assert out["status"] == "report_failed"
    assert "template missing" in out["error"]


@pytest.mark.asyncio
async def test_generate_report_invalid_iso_date_failure_isolated():
    """Bad ISO string -> ValueError caught, status='report_failed'."""
    state = {"project_id": "p-1", "report_date": "not-a-date"}
    out = await generate_report_node(state)
    assert out["status"] == "report_failed"
    assert out["error"]


# =========================================================================
# transcribe_meeting_node
# =========================================================================


@pytest.mark.asyncio
async def test_transcribe_no_audio_short_circuits():
    """[edge case] No audio_path -> status='no_audio',
    transcription_results=None. NOT a failure — audio is optional."""
    out = await transcribe_meeting_node({"project_id": "p-1"})
    assert out["transcription_results"] is None
    assert out["status"] == "no_audio"


@pytest.mark.asyncio
async def test_transcribe_with_audio_calls_transcriber():
    fake = AsyncMock(return_value={"text": "Meeting notes...", "duration_s": 1800})
    with patch(
        "app.services.agents.communication_agent._transcriber.transcribe",
        fake,
    ):
        out = await transcribe_meeting_node({"project_id": "p-1", "audio_path": "/tmp/meeting.wav"})

    fake.assert_called_once_with("/tmp/meeting.wav")
    assert out["status"] == "transcribed"
    assert out["transcription_results"]["text"] == "Meeting notes..."


@pytest.mark.asyncio
async def test_transcribe_failure_isolated():
    fake = AsyncMock(side_effect=RuntimeError("whisper crashed"))
    with patch(
        "app.services.agents.communication_agent._transcriber.transcribe",
        fake,
    ):
        out = await transcribe_meeting_node({"project_id": "p-1", "audio_path": "/tmp/x.wav"})

    assert out["transcription_results"] is None
    assert out["status"] == "transcription_failed"
    assert "whisper crashed" in out["error"]


# =========================================================================
# suggest_rfi_node
# =========================================================================


@pytest.mark.asyncio
async def test_suggest_rfi_no_data_short_circuits():
    """[edge case] No rfi_data -> status='no_rfi'. RFI input is
    optional, missing it isn't a failure."""
    out = await suggest_rfi_node({"project_id": "p-1"})
    assert out["rfi_results"] is None
    assert out["status"] == "no_rfi"


@pytest.mark.asyncio
async def test_suggest_rfi_passes_subject_question_context():
    captured = {}

    async def fake_suggest(*, subject, question, project_context):
        captured.update(
            {"subject": subject, "question": question, "project_context": project_context}
        )
        return {"answer": "See spec section 03 30 00", "confidence": 0.85}

    state = {
        "project_id": "p-1",
        "rfi_data": {
            "subject": "Concrete strength",
            "question": "What PSI for foundation walls?",
            "context": {"project_type": "commercial"},
        },
    }
    with patch(
        "app.services.agents.communication_agent.suggest_rfi_response",
        fake_suggest,
    ):
        out = await suggest_rfi_node(state)

    assert captured["subject"] == "Concrete strength"
    assert captured["question"] == "What PSI for foundation walls?"
    assert captured["project_context"] == {"project_type": "commercial"}
    assert out["status"] == "rfi_suggested"


@pytest.mark.asyncio
async def test_suggest_rfi_empty_dict_short_circuits_no_rfi():
    """[edge case] Empty rfi_data dict is falsy -> short-circuit to
    no_rfi (NOT a failure — empty dict is treated like missing
    rfi_data)."""
    out = await suggest_rfi_node({"project_id": "p-1", "rfi_data": {}})
    assert out["status"] == "no_rfi"
    assert out["rfi_results"] is None


@pytest.mark.asyncio
async def test_suggest_rfi_partial_data_uses_empty_string_defaults():
    """[edge case] rfi_data truthy but missing subject/question keys
    -> helper receives empty strings (don't pass None — helper
    expects str)."""
    captured = {}

    async def fake_suggest(*, subject, question, project_context):
        captured.update(
            {"subject": subject, "question": question, "project_context": project_context}
        )
        return {}

    # Truthy dict (has 'context' key) but missing subject/question:
    state = {"project_id": "p-1", "rfi_data": {"context": {}}}
    with patch(
        "app.services.agents.communication_agent.suggest_rfi_response",
        fake_suggest,
    ):
        await suggest_rfi_node(state)

    assert captured["subject"] == ""
    assert captured["question"] == ""


@pytest.mark.asyncio
async def test_suggest_rfi_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("rfi helper down")

    state = {"project_id": "p-1", "rfi_data": {"subject": "x"}}
    with patch(
        "app.services.agents.communication_agent.suggest_rfi_response",
        boom,
    ):
        out = await suggest_rfi_node(state)

    assert out["rfi_results"] is None
    assert out["status"] == "rfi_failed"
    assert "rfi helper down" in out["error"]


# =========================================================================
# Graph build
# =========================================================================


def test_build_communication_agent_returns_compiled_graph():
    graph = build_communication_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {"generate_report", "transcribe_meeting", "suggest_rfi"} <= nodes


def test_build_communication_agent_has_documented_flow():
    """[contract] generate_report -> transcribe_meeting -> suggest_rfi
    sequential. Refactor must NOT skip nodes when their input is
    missing — the optional-input pattern is handled INSIDE each node
    (no_audio, no_rfi statuses)."""
    graph = build_communication_agent()
    g = graph.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("generate_report", "transcribe_meeting") in edges
    assert ("transcribe_meeting", "suggest_rfi") in edges
