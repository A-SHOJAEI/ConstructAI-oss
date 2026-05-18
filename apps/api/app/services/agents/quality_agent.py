"""LangGraph agent for quality management workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.quality.compliance_checker import (
    check_project_compliance,
)
from app.services.quality.defect_classifier import (
    DefectClassifier,
)

logger = logging.getLogger(__name__)

_classifier = DefectClassifier()


class QualityAgentState(TypedDict):
    """State schema for the quality agent."""

    project_id: str
    inspection_data: dict
    images: list[bytes]
    defect_results: list[dict] | None
    compliance_results: list[dict] | None
    ncr_recommendations: list[dict] | None
    status: str
    error: str | None


async def classify_defects_node(
    state: QualityAgentState,
) -> dict:
    """Classify defects from inspection images."""
    try:
        images = state.get("images", [])
        if not images:
            return {
                "defect_results": [],
                "status": "no_images",
            }

        results = []
        for img_bytes in images:
            result = await _classifier.classify(img_bytes)
            results.append(result)

        return {
            "defect_results": results,
            "status": "defects_classified",
        }
    except Exception as exc:
        logger.error(
            "Defect classification failed: %s",
            exc,
        )
        return {
            "defect_results": None,
            "status": "classification_failed",
            "error": str(exc),
        }


async def check_compliance_node(
    state: QualityAgentState,
) -> dict:
    """Run compliance checks for the project."""
    try:
        project_data = state.get("inspection_data", {})
        results = await check_project_compliance(
            project_id=state["project_id"],
            project_data=project_data,
        )
        return {
            "compliance_results": results,
            "status": "compliance_checked",
        }
    except Exception as exc:
        logger.error("Compliance check failed: %s", exc)
        return {
            "compliance_results": None,
            "status": "compliance_failed",
            "error": str(exc),
        }


async def recommend_ncrs_node(
    state: QualityAgentState,
) -> dict:
    """Recommend NCRs based on defect and compliance."""
    try:
        defects = state.get("defect_results", [])
        compliance = state.get("compliance_results", [])

        recommendations = []

        # NCRs from critical defects
        for defect in defects or []:
            severity = defect.get(
                "severity_estimate",
                "minor",
            )
            if severity in ("critical", "major"):
                defect_type = defect.get(
                    "defect_type",
                    "unknown",
                )
                recommendations.append(
                    {
                        "source": "defect_detection",
                        "defect_type": defect_type,
                        "severity": severity,
                        "recommendation": (f"Issue NCR for {severity} defect: {defect_type}"),
                    }
                )

        # NCRs from compliance failures
        for check in compliance or []:
            if check.get("status") == "warning":
                reg_code = check.get(
                    "regulation_code",
                    "",
                )
                reg_title = check.get(
                    "regulation_title",
                    "",
                )
                recommendations.append(
                    {
                        "source": "compliance_check",
                        "regulation": reg_code,
                        "recommendation": (f"Review compliance with {reg_title}"),
                    }
                )

        return {
            "ncr_recommendations": recommendations,
            "status": "ncrs_recommended",
        }
    except Exception as exc:
        logger.error(
            "NCR recommendation failed: %s",
            exc,
        )
        return {
            "ncr_recommendations": None,
            "status": "ncr_failed",
            "error": str(exc),
        }


def build_quality_agent(checkpointer=None) -> CompiledStateGraph:
    """Build the quality management agent graph.

    Flow: classify_defects -> check_compliance
          -> recommend_ncrs -> END
    """
    workflow = StateGraph(QualityAgentState)

    workflow.add_node(
        "classify_defects",
        classify_defects_node,
    )
    workflow.add_node(
        "check_compliance",
        check_compliance_node,
    )
    workflow.add_node(
        "recommend_ncrs",
        recommend_ncrs_node,
    )

    workflow.set_entry_point("classify_defects")
    workflow.add_edge(
        "classify_defects",
        "check_compliance",
    )
    workflow.add_edge(
        "check_compliance",
        "recommend_ncrs",
    )
    workflow.add_edge("recommend_ncrs", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_quality_agent(
    project_id: str,
    inspection_data: dict | None = None,
    images: list[bytes] | None = None,
) -> dict:
    """Run the quality management agent."""
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_quality_agent(checkpointer=checkpointer)
    config = cast(RunnableConfig, {"configurable": {"thread_id": f"quality_{uuid.uuid4().hex}"}})

    initial_state: QualityAgentState = {
        "project_id": project_id,
        "inspection_data": inspection_data or {},
        "images": images or [],
        "defect_results": None,
        "compliance_results": None,
        "ncr_recommendations": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "quality"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Quality agent failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
