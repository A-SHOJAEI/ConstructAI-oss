"""Six-stage guardrails validation pipeline."""

from __future__ import annotations

import logging
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)


class GuardrailState(TypedDict):
    agent_name: str
    raw_output: str
    parsed_output: dict | None
    validation_errors: list[dict]
    confidence_score: float | None
    routing_decision: str | None
    passed: bool
    latency_ms: int
    # SECURITY [M-14]: Flag set when parse fails; forces routing to human_review
    parse_failed: bool


async def parse_structured_output(state: GuardrailState) -> dict:
    """Stage 1: Parse raw output into structured format."""
    from app.services.guardrails.structured_parser import parse_output

    start = time.monotonic()
    result = await parse_output(state["raw_output"], state["agent_name"])
    elapsed = int((time.monotonic() - start) * 1000)
    errors = state.get("validation_errors", [])
    if result.get("error"):
        errors = [*errors, {"stage": "parse", "message": result["error"]}]
    return {
        "parsed_output": result.get("data"),
        "validation_errors": errors,
        "passed": result.get("data") is not None,
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


async def validate_schema(state: GuardrailState) -> dict:
    """Stage 2: Pydantic field validation."""
    from app.services.guardrails.schema_validator import validate_fields

    start = time.monotonic()
    result = await validate_fields(
        state.get("parsed_output") or {},
        state["agent_name"],
    )
    elapsed = int((time.monotonic() - start) * 1000)
    errors = state.get("validation_errors", [])
    if result.get("errors"):
        errors = [*errors, *result["errors"]]
    return {
        "validation_errors": errors,
        "passed": len(result.get("errors", [])) == 0,
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


async def check_domain_rules(state: GuardrailState) -> dict:
    """Stage 3: CSI, OSHA, RSMeans domain validation."""
    from app.services.guardrails.domain_rules import validate_domain

    start = time.monotonic()
    result = await validate_domain(
        state.get("parsed_output") or {},
        state["agent_name"],
    )
    elapsed = int((time.monotonic() - start) * 1000)
    errors = state.get("validation_errors", [])
    if result.get("violations"):
        errors = [*errors, *result["violations"]]
    return {
        "validation_errors": errors,
        "passed": len(result.get("violations", [])) == 0,
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


async def verify_knowledge_base(state: GuardrailState) -> dict:
    """Stage 4: RAG cross-reference verification."""
    from app.services.guardrails.knowledge_verifier import verify

    start = time.monotonic()
    result = await verify(
        state.get("parsed_output") or {},
        state["agent_name"],
    )
    elapsed = int((time.monotonic() - start) * 1000)
    errors = state.get("validation_errors", [])
    if result.get("warnings"):
        errors = [*errors, *result["warnings"]]
    return {
        "validation_errors": errors,
        "passed": state.get("passed", True),
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


async def score_confidence(state: GuardrailState) -> dict:
    """Stage 5: UQLM confidence scoring."""
    from app.services.guardrails.confidence_scorer import (
        ConfidenceScorer,
    )

    start = time.monotonic()
    scorer = ConfidenceScorer()
    result = await scorer.score(
        state.get("parsed_output") or {},
        state["agent_name"],
    )
    elapsed = int((time.monotonic() - start) * 1000)
    return {
        "confidence_score": result.get("overall_confidence", 0.0),
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


async def decide_routing(state: GuardrailState) -> dict:
    """Stage 6: Routing decision based on confidence.

    SECURITY [M-14]: If parsing failed (stage 1), the routing decision is
    forced to 'human_review' regardless of confidence score. This prevents
    unparseable output from bypassing validation while still being routed
    for auto-approval.
    """
    # SECURITY [M-14] / M-6: Check if parse failed — force human_review
    # and explicitly mark passed=False so downstream audit logs don't
    # claim success on unparseable output.
    parse_errors = [e for e in state.get("validation_errors", []) if e.get("stage") == "parse"]
    if parse_errors or not state.get("passed", True):
        elapsed = 0
        return {
            "routing_decision": "human_review",
            "parse_failed": True,
            "passed": False,
            "latency_ms": state.get("latency_ms", 0) + elapsed,
        }

    from app.services.guardrails.routing_decision import decide_route

    start = time.monotonic()
    decision = decide_route(
        state.get("confidence_score") or 0.0,
        state["agent_name"],
        state.get("validation_errors", []),
    )
    elapsed = int((time.monotonic() - start) * 1000)
    return {
        "routing_decision": decision,
        "latency_ms": state.get("latency_ms", 0) + elapsed,
    }


def build_guardrails_pipeline() -> object:
    """Build the six-stage guardrails pipeline."""
    pipeline = StateGraph(GuardrailState)
    pipeline.add_node("parse_structured", parse_structured_output)
    pipeline.add_node("validate_schema", validate_schema)
    pipeline.add_node("check_domain_rules", check_domain_rules)
    pipeline.add_node("verify_knowledge", verify_knowledge_base)
    pipeline.add_node("score_confidence", score_confidence)
    pipeline.add_node("decide_routing", decide_routing)

    pipeline.add_edge(START, "parse_structured")
    pipeline.add_edge("parse_structured", "validate_schema")
    pipeline.add_edge("validate_schema", "check_domain_rules")
    pipeline.add_edge("check_domain_rules", "verify_knowledge")
    pipeline.add_edge("verify_knowledge", "score_confidence")
    pipeline.add_edge("score_confidence", "decide_routing")
    pipeline.add_edge("decide_routing", END)

    return pipeline.compile()


async def _persist_guardrail_result(
    db, agent_name: str, result: dict, input_hash: str | None
) -> None:
    """H-8: Persist guardrail pipeline result to the DB.

    Called only when a session is supplied so the pipeline stays usable in
    pure-function unit tests.
    """
    if db is None:
        return
    try:
        from app.models.guardrail_log import GuardrailLog

        # Map routing_decision to stage name for audit readability
        db.add(
            GuardrailLog(
                agent_name=agent_name,
                stage=str(result.get("routing_decision") or "completed"),
                input_hash=input_hash,
                passed=bool(result.get("passed", False)),
                parse_failed=bool(result.get("parse_failed", False)),
                violations=result.get("validation_errors") or [],
                confidence_score=result.get("confidence_score"),
                routing_decision=result.get("routing_decision"),
                latency_ms=int(result.get("latency_ms") or 0),
            )
        )
        await db.flush()
    except Exception as exc:
        # Telemetry write failures must never fail the pipeline call
        logger.warning("Failed to persist guardrail log: %s", exc)


async def run_guardrails(
    raw_output: str,
    agent_name: str,
    *,
    db=None,
) -> dict:
    """Run the full guardrails pipeline.

    Args:
        raw_output: Agent output text to validate.
        agent_name: Name of the agent that produced ``raw_output``.
        db: Optional async SQLAlchemy session — when supplied, the result
            (including ``parse_failed``) is persisted to ``guardrail_logs``.
    """
    pipeline = build_guardrails_pipeline()
    initial_state: GuardrailState = {
        "agent_name": agent_name,
        "raw_output": raw_output,
        "parsed_output": None,
        "validation_errors": [],
        "confidence_score": None,
        "routing_decision": None,
        "passed": True,
        "latency_ms": 0,
        "parse_failed": False,
    }
    result = await pipeline.ainvoke(initial_state)  # type: ignore[attr-defined]
    logger.info(
        "Guardrails complete for %s: passed=%s routing=%s parse_failed=%s latency=%dms",
        agent_name,
        result.get("passed"),
        result.get("routing_decision"),
        result.get("parse_failed"),
        result.get("latency_ms", 0),
    )
    if db is not None:
        import hashlib

        input_hash = hashlib.sha256(raw_output.encode("utf-8", errors="ignore")).hexdigest()[:64]
        await _persist_guardrail_result(db, agent_name, result, input_hash)
    return result
