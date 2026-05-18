"""Tests for communication LangGraph agent."""

from __future__ import annotations

from datetime import date

from app.services.agents.communication_agent import (
    build_communication_agent,
    generate_report_node,
    run_communication_agent,
    suggest_rfi_node,
    transcribe_meeting_node,
)


class TestCommunicationAgent:
    async def test_generate_report_node(self):
        state = {
            "project_id": "test-1",
            "report_date": "2024-06-15",
            "daily_log": {
                "crew_count": 30,
                "work_hours": "240",
                "activities_completed": [],
                "delays": [],
            },
            "evm_snapshot": None,
            "safety_events": [],
            "audio_path": None,
            "rfi_data": None,
            "report_results": None,
            "transcription_results": None,
            "rfi_results": None,
            "status": "processing",
            "error": None,
        }
        result = await generate_report_node(state)
        assert result["report_results"] is not None
        assert "content_markdown" in result["report_results"]

    async def test_transcribe_no_audio(self):
        state = {
            "project_id": "test-1",
            "report_date": "2024-06-15",
            "daily_log": None,
            "evm_snapshot": None,
            "safety_events": [],
            "audio_path": None,
            "rfi_data": None,
            "report_results": None,
            "transcription_results": None,
            "rfi_results": None,
            "status": "report_generated",
            "error": None,
        }
        result = await transcribe_meeting_node(state)
        assert result["transcription_results"] is None
        assert result["status"] == "no_audio"

    async def test_suggest_rfi_node(self):
        state = {
            "project_id": "test-1",
            "report_date": "2024-06-15",
            "daily_log": None,
            "evm_snapshot": None,
            "safety_events": [],
            "audio_path": None,
            "rfi_data": {
                "subject": "Steel connection",
                "question": "What bolt type?",
            },
            "report_results": None,
            "transcription_results": None,
            "rfi_results": None,
            "status": "transcribed",
            "error": None,
        }
        result = await suggest_rfi_node(state)
        assert result["rfi_results"] is not None

    async def test_suggest_rfi_no_data(self):
        state = {
            "project_id": "test-1",
            "report_date": "2024-06-15",
            "daily_log": None,
            "evm_snapshot": None,
            "safety_events": [],
            "audio_path": None,
            "rfi_data": None,
            "report_results": None,
            "transcription_results": None,
            "rfi_results": None,
            "status": "transcribed",
            "error": None,
        }
        result = await suggest_rfi_node(state)
        assert result["rfi_results"] is None

    async def test_build_graph(self):
        graph = build_communication_agent()
        assert graph is not None

    async def test_run_full_agent(self):
        result = await run_communication_agent(
            project_id="test-project-1",
            report_date=date(2024, 6, 15),
        )
        assert result["status"] == "completed"
        assert result["report_results"] is not None
