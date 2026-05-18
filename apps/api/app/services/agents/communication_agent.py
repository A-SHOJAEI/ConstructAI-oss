"""LangGraph agent for communication and reporting."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.communication.report_generator import (
    generate_daily_report,
)
from app.services.communication.rfi_helper import (
    suggest_rfi_response,
)
from app.services.communication.transcriber import (
    MeetingTranscriber,
)

logger = logging.getLogger(__name__)

_transcriber = MeetingTranscriber()


class CommunicationAgentState(TypedDict):
    """State schema for the communication agent."""

    project_id: str
    report_date: str  # ISO date string
    daily_log: dict | None
    evm_snapshot: dict | None
    safety_events: list[dict]
    audio_path: str | None
    rfi_data: dict | None
    report_results: dict | None
    transcription_results: dict | None
    rfi_results: dict | None
    status: str
    error: str | None


async def generate_report_node(
    state: CommunicationAgentState,
) -> dict:
    """Generate the daily construction report."""
    try:
        report_date = date.fromisoformat(
            state.get(
                "report_date",
                date.today().isoformat(),
            )
        )
        result = await generate_daily_report(
            project_id=state["project_id"],
            report_date=report_date,
            daily_log=state.get("daily_log"),
            evm_snapshot=state.get("evm_snapshot"),
            safety_events=state.get(
                "safety_events",
                [],
            ),
        )
        return {
            "report_results": result,
            "status": "report_generated",
        }
    except Exception as exc:
        logger.error(
            "Report generation failed: %s",
            exc,
        )
        return {
            "report_results": None,
            "status": "report_failed",
            "error": str(exc),
        }


async def transcribe_meeting_node(
    state: CommunicationAgentState,
) -> dict:
    """Transcribe meeting audio if provided."""
    try:
        audio_path = state.get("audio_path")
        if not audio_path:
            return {
                "transcription_results": None,
                "status": "no_audio",
            }

        result = await _transcriber.transcribe(
            audio_path,
        )
        return {
            "transcription_results": result,
            "status": "transcribed",
        }
    except Exception as exc:
        logger.error("Transcription failed: %s", exc)
        return {
            "transcription_results": None,
            "status": "transcription_failed",
            "error": str(exc),
        }


async def suggest_rfi_node(
    state: CommunicationAgentState,
) -> dict:
    """Suggest RFI responses if RFI data provided."""
    try:
        rfi = state.get("rfi_data")
        if not rfi:
            return {
                "rfi_results": None,
                "status": "no_rfi",
            }

        result = await suggest_rfi_response(
            subject=rfi.get("subject", ""),
            question=rfi.get("question", ""),
            project_context=rfi.get("context"),
        )
        return {
            "rfi_results": result,
            "status": "rfi_suggested",
        }
    except Exception as exc:
        logger.error(
            "RFI suggestion failed: %s",
            exc,
        )
        return {
            "rfi_results": None,
            "status": "rfi_failed",
            "error": str(exc),
        }


def build_communication_agent(checkpointer=None) -> CompiledStateGraph:
    """Build the communication agent graph.

    Flow: generate_report -> transcribe_meeting
          -> suggest_rfi -> END
    """
    workflow = StateGraph(CommunicationAgentState)

    workflow.add_node(
        "generate_report",
        generate_report_node,
    )
    workflow.add_node(
        "transcribe_meeting",
        transcribe_meeting_node,
    )
    workflow.add_node("suggest_rfi", suggest_rfi_node)

    workflow.set_entry_point("generate_report")
    workflow.add_edge(
        "generate_report",
        "transcribe_meeting",
    )
    workflow.add_edge(
        "transcribe_meeting",
        "suggest_rfi",
    )
    workflow.add_edge("suggest_rfi", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_communication_agent(
    project_id: str,
    report_date: date | None = None,
    daily_log: dict | None = None,
    evm_snapshot: dict | None = None,
    safety_events: list[dict] | None = None,
    audio_path: str | None = None,
    rfi_data: dict | None = None,
) -> dict:
    """Run the communication and reporting agent."""
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_communication_agent(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"communication_{uuid.uuid4().hex}"}}
    )

    initial_state: CommunicationAgentState = {
        "project_id": project_id,
        "report_date": ((report_date or date.today()).isoformat()),
        "daily_log": daily_log,
        "evm_snapshot": evm_snapshot,
        "safety_events": safety_events or [],
        "audio_path": audio_path,
        "rfi_data": rfi_data,
        "report_results": None,
        "transcription_results": None,
        "rfi_results": None,
        "status": "processing",
        "error": None,
    }

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        if final_state.get("error") is None:
            final_state["status"] = "completed"
        return final_state
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "communication"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Communication agent failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
