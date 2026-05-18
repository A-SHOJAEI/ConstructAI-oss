"""LangGraph agent for Bid/No-Bid decision intelligence.

Sequential flow: load_context → score_opportunity → generate_reasoning → END

The agent wraps the pure-computation BidDecisionEngine with database
context loading and LLM-powered narrative reasoning.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class BidDecisionState(TypedDict):
    opportunity_id: str
    org_id: str
    opportunity: dict
    org_context: dict
    factor_scores: dict | None
    composite_score: int | None
    recommendation: str | None
    win_probability: float | None
    reasoning: str | None
    status: str
    errors: list[str]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def load_context_node(state: BidDecisionState) -> dict:
    """Load organizational context for scoring.

    Queries historical bids, office location, current backlog, etc.
    from the database to build the org_context dict.
    """
    org_id = state["org_id"]
    errors = list(state.get("errors") or [])

    org_context: dict = {
        "bid_count": 0,
        "win_count": 0,
        "bids_by_type": {},
        "bids_by_method": {},
        "previous_owners": {},
    }

    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models.bid import BidOpportunity

        engine = create_async_engine(settings.DATABASE_URL)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as db:
            # Get all completed bids for this org
            stmt = select(BidOpportunity).where(
                BidOpportunity.org_id == uuid.UUID(org_id),
                BidOpportunity.outcome.in_(["won", "lost"]),
            )
            result = await db.execute(stmt)
            past_bids = result.scalars().all()

            bid_count = len(past_bids)
            win_count = sum(1 for b in past_bids if b.outcome == "won")

            # Group by type and method
            bids_by_type: dict[str, dict] = {}
            bids_by_method: dict[str, dict] = {}
            previous_owners: dict[str, dict] = {}
            values: list[float] = []

            for bid in past_bids:
                # By type
                ptype = bid.project_type or "unknown"
                if ptype not in bids_by_type:
                    bids_by_type[ptype] = {"total": 0, "won": 0}
                bids_by_type[ptype]["total"] += 1
                if bid.outcome == "won":
                    bids_by_type[ptype]["won"] += 1

                # By method
                method = bid.delivery_method or "unknown"
                if method not in bids_by_method:
                    bids_by_method[method] = {"total": 0, "won": 0}
                bids_by_method[method]["total"] += 1
                if bid.outcome == "won":
                    bids_by_method[method]["won"] += 1

                # Owners
                owner = bid.owner_name
                if owner:
                    if owner not in previous_owners:
                        previous_owners[owner] = {"projects": 0, "satisfaction": 0.7}
                    previous_owners[owner]["projects"] += 1

                if bid.estimated_value:
                    values.append(float(bid.estimated_value))

            org_context = {
                "bid_count": bid_count,
                "win_count": win_count,
                "bids_by_type": bids_by_type,
                "bids_by_method": bids_by_method,
                "previous_owners": previous_owners,
                "avg_project_size": sum(values) / len(values) if values else 0,
                "min_project_size": min(values) if values else 0,
                "max_project_size": max(values) if values else 0,
            }

        await engine.dispose()

    except Exception as exc:
        logger.error("Failed to load org context for %s: %s", org_id, exc)
        errors.append(f"Context load failed: {exc}")

    return {"org_context": org_context, "errors": errors, "status": "context_loaded"}


async def score_opportunity_node(state: BidDecisionState) -> dict:
    """Score the opportunity using BidDecisionEngine."""
    errors = list(state.get("errors") or [])

    try:
        from app.services.estimating.bid_decision_engine import BidDecisionEngine

        engine = BidDecisionEngine()
        result = engine.score_opportunity(
            opportunity=state["opportunity"],
            org_context=state["org_context"],
        )

        return {
            "factor_scores": result["factor_scores"],
            "composite_score": result["composite_score"],
            "recommendation": result["recommendation"],
            "win_probability": result["win_probability"],
            "status": "scored",
            "errors": errors,
        }
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        errors.append(f"Scoring failed: {exc}")
        return {
            "factor_scores": {},
            "composite_score": 50,
            "recommendation": "conditional",
            "win_probability": 0.18,
            "status": "scoring_failed",
            "errors": errors,
        }


async def generate_reasoning_node(state: BidDecisionState) -> dict:
    """Generate LLM-powered narrative reasoning for the bid decision."""
    errors = list(state.get("errors") or [])

    opp = state["opportunity"]
    score = state.get("composite_score", 50)
    recommendation = state.get("recommendation") or "conditional"
    factor_scores: dict = state.get("factor_scores") or {}
    win_prob = state.get("win_probability", 0.18)

    # Build top factors summary
    sorted_factors = sorted(
        factor_scores.items(),
        key=lambda x: abs(x[1].get("weighted_score", 0)),
        reverse=True,
    )
    top_factors = "\n".join(
        f"- {name}: {data.get('score', 50):.0f}/100 (weight {data.get('weight', 0):.0%}) — {data.get('reasoning', '')}"
        for name, data in sorted_factors[:6]
    )

    prompt = f"""You are a senior construction estimating manager advising on a bid/no-bid decision.

<user_data>
PROJECT: {sanitize_for_prompt(str(opp.get("name", "Unknown")), max_length=200)}
Type: {sanitize_for_prompt(str(opp.get("project_type", "N/A")), max_length=100)}
Delivery: {sanitize_for_prompt(str(opp.get("delivery_method", "N/A")), max_length=100)}
Value: ${opp.get("estimated_value", 0):,.0f}
Location: {sanitize_for_prompt(str(opp.get("location", "N/A")), max_length=200)}
Owner: {sanitize_for_prompt(str(opp.get("owner_name", "N/A")), max_length=200)}
</user_data>

AI SCORE: {score}/100 → {recommendation.upper().replace("_", " ")}
Win Probability: {win_prob:.0%}

TOP SCORING FACTORS:
{top_factors}

Write a concise 3-4 paragraph analysis explaining:
1. Why this opportunity scores as it does
2. The key strengths and risks
3. Your recommendation and the 2-3 most important conditions for success

Use direct, professional language appropriate for a construction executive audience.
Do not use bullet points. Write in paragraph form."""

    reasoning = f"Score: {score}/100 — Recommendation: {recommendation}"

    try:
        from app.services.reliability.llm_gateway import get_llm_gateway

        gateway = await get_llm_gateway()
        llm_response = await gateway.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name="bid_decision_agent",
        )

        if llm_response and llm_response.get("content"):
            reasoning = llm_response["content"]

    except Exception as exc:
        logger.error("LLM reasoning generation failed: %s", exc)
        errors.append(f"LLM reasoning failed: {exc}")
        # Fallback: build from factor data
        top_3 = sorted_factors[:3]
        reasoning = (
            f"Composite score: {score}/100 ({recommendation}). "
            f"Win probability: {win_prob:.0%}. "
            f"Key factors: "
            + "; ".join(f"{name} ({data.get('score', 50):.0f}/100)" for name, data in top_3)
            + "."
        )

    return {"reasoning": reasoning, "status": "complete", "errors": errors}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_bid_decision_agent(checkpointer=None):
    """Build the LangGraph workflow for bid decision scoring.

    Sequential flow: load_context → score_bid → reason_bid → END
    """
    workflow = StateGraph(BidDecisionState)

    workflow.add_node("load_context", load_context_node)
    workflow.add_node("score_bid", score_opportunity_node)
    workflow.add_node("reason_bid", generate_reasoning_node)

    workflow.set_entry_point("load_context")
    workflow.add_edge("load_context", "score_bid")
    workflow.add_edge("score_bid", "reason_bid")
    workflow.add_edge("reason_bid", END)

    return workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def score_bid_opportunity(
    opportunity_id: str,
    opportunity: dict,
    org_id: str,
) -> dict:
    """Run the bid decision agent end-to-end.

    Args:
        opportunity_id: UUID string of the bid opportunity.
        opportunity: Dict of opportunity data.
        org_id: Organization UUID string.

    Returns:
        Dict with composite_score, recommendation, win_probability,
        factor_scores, reasoning, and errors.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    agent = build_bid_decision_agent(checkpointer=checkpointer)

    initial_state: BidDecisionState = {
        "opportunity_id": opportunity_id,
        "org_id": org_id,
        "opportunity": opportunity,
        "org_context": {},
        "factor_scores": None,
        "composite_score": None,
        "recommendation": None,
        "win_probability": None,
        "reasoning": None,
        "status": "started",
        "errors": [],
    }

    config = cast(RunnableConfig, {"configurable": {"thread_id": f"bid_{uuid.uuid4().hex}"}})
    try:
        final_state = await asyncio.wait_for(
            agent.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "bid_decision"})
        return {"error": "Agent execution timed out", "status": "timeout"}

    return {
        "composite_score": final_state.get("composite_score", 50),
        "recommendation": final_state.get("recommendation", "conditional"),
        "win_probability": final_state.get("win_probability", 0.18),
        "factor_scores": final_state.get("factor_scores", {}),
        "reasoning": final_state.get("reasoning", ""),
        "errors": final_state.get("errors", []),
    }
