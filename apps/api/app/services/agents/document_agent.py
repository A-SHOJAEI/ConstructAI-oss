"""LangGraph Document Agent for orchestrating document analysis workflows."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.agents.classifier import classify_document
from app.services.agents.entity_extractor import extract_entities
from app.services.agents.risk_detector import detect_risks

logger = logging.getLogger(__name__)


class DocumentAgentState(TypedDict):
    """State schema for the document analysis agent graph."""

    document_id: str
    text_content: str
    filename: str
    classification: dict | None
    entities: list
    risks: list
    status: str
    error: str | None


async def classify_node(state: DocumentAgentState) -> dict:
    """Run document classification and update state."""
    try:
        classification = await classify_document(
            text_sample=state["text_content"],
            filename=state["filename"],
        )
        return {"classification": classification, "status": "classified"}
    except Exception as exc:
        logger.error("Classification node failed for %s: %s", state["document_id"], exc)
        return {
            "classification": None,
            "status": "classification_failed",
            "error": str(exc),
        }


async def extract_entities_node(state: DocumentAgentState) -> dict:
    """Run entity extraction and update state."""
    try:
        entities = await extract_entities(text=state["text_content"])
        return {"entities": entities}
    except Exception as exc:
        logger.error("Entity extraction node failed for %s: %s", state["document_id"], exc)
        return {"entities": [], "error": str(exc)}


async def detect_risks_node(state: DocumentAgentState) -> dict:
    """Run risk detection and update state."""
    try:
        risks = await detect_risks(text=state["text_content"])
        return {"risks": risks}
    except Exception as exc:
        logger.error("Risk detection node failed for %s: %s", state["document_id"], exc)
        return {"risks": [], "error": str(exc)}


def build_document_agent(checkpointer=None) -> CompiledStateGraph:
    """Build and compile the LangGraph document analysis workflow.

    The graph executes classification first, then runs entity extraction
    and risk detection (which can proceed after classification completes).

    Returns:
        A compiled LangGraph ``StateGraph``.
    """
    workflow = StateGraph(DocumentAgentState)

    # Add processing nodes
    workflow.add_node("classify", classify_node)
    workflow.add_node("extract_entities", extract_entities_node)
    workflow.add_node("detect_risks", detect_risks_node)

    # Define edges: classify runs first, then fan out to extraction and risk detection
    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "extract_entities")
    workflow.add_edge("classify", "detect_risks")
    workflow.add_edge("extract_entities", END)
    workflow.add_edge("detect_risks", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_document_agent(
    document_id: str,
    text_content: str,
    filename: str,
) -> dict:
    """Build and invoke the document analysis agent.

    Args:
        document_id: UUID string of the document being processed.
        text_content: Full or representative text content of the document.
        filename: Original filename of the document.

    Returns:
        The final agent state as a dict containing classification, entities,
        risks, status, and any error information.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_document_agent(checkpointer=checkpointer)
    config = cast(RunnableConfig, {"configurable": {"thread_id": f"document_{uuid.uuid4().hex}"}})

    initial_state: DocumentAgentState = {
        "document_id": document_id,
        "text_content": text_content,
        "filename": filename,
        "classification": None,
        "entities": [],
        "risks": [],
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
        logger.error("Agent timed out after 300s", extra={"agent": "document"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error("Document agent failed for %s: %s", document_id, exc)
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
