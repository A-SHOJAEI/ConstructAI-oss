"""LangGraph agent for construction procurement workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.procurement.contract_risk import score_contract_risk
from app.services.procurement.price_forecaster import (
    forecast_prices,
    get_bls_ppi_series,
)
from app.services.procurement.vendor_manager import score_vendor

logger = logging.getLogger(__name__)


class ProcurementAgentState(TypedDict):
    """State schema for the procurement agent graph."""

    project_id: str
    materials: list
    price_forecasts: dict | None
    vendor_scores: list
    contract_risk: dict | None
    recommendations: list
    status: str
    error: str | None


async def forecast_prices_node(state: ProcurementAgentState) -> dict:
    """Run price forecasting for required materials."""
    try:
        materials = state.get("materials", [])
        if not materials:
            # Default material categories for general forecasting
            materials = [
                {"category": "concrete", "series_id": "WPUIP2300001"},
                {"category": "structural_steel", "series_id": "WPU101"},
                {"category": "lumber", "series_id": "WPU0811"},
            ]

        forecasts: dict[str, dict] = {}

        for material in materials:
            category = material.get("category", "general")
            series_id = material.get("series_id", "WPUIP2300001")

            # Fetch historical data
            historical = await get_bls_ppi_series(series_id)

            # Run forecast
            forecast = await forecast_prices(
                historical_data=historical,
                horizon_months=6,
                material_category=category,
            )

            forecasts[category] = forecast

        logger.info(
            "Price forecasts generated for project %s: %d material categories",
            state["project_id"],
            len(forecasts),
        )
        return {"price_forecasts": forecasts, "status": "prices_forecast"}

    except Exception as exc:
        logger.error(
            "Price forecasting failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "price_forecasts": None,
            "status": "forecast_failed",
            "error": str(exc),
        }


async def score_vendors_node(state: ProcurementAgentState) -> dict:
    """Evaluate and score vendors for procurement."""
    try:
        materials = state.get("materials", [])

        # Extract vendor data from materials or use defaults
        vendor_list = []
        for material in materials:
            vendors = material.get("vendors", [])
            vendor_list.extend(vendors)

        if not vendor_list:
            # Default vendor data for demonstration
            vendor_list = [
                {
                    "name": "Pacific Steel Fabricators",
                    "vendor_id": "v-001",
                    "past_projects": 45,
                    "on_time_delivery_pct": 92.5,
                    "quality_rating": 4.3,
                    "safety_record": 0.82,
                    "financial_stability": "strong",
                    "references": 12,
                    "bonding_capacity": 25_000_000,
                    "price_competitiveness": 0.75,
                },
                {
                    "name": "Central Ready Mix",
                    "vendor_id": "v-002",
                    "past_projects": 120,
                    "on_time_delivery_pct": 88.0,
                    "quality_rating": 4.1,
                    "safety_record": 0.95,
                    "financial_stability": "strong",
                    "references": 20,
                    "bonding_capacity": 50_000_000,
                    "price_competitiveness": 0.82,
                },
                {
                    "name": "Southwest Rebar Supply",
                    "vendor_id": "v-003",
                    "past_projects": 28,
                    "on_time_delivery_pct": 85.0,
                    "quality_rating": 3.8,
                    "safety_record": 1.15,
                    "financial_stability": "moderate",
                    "references": 8,
                    "bonding_capacity": 10_000_000,
                    "price_competitiveness": 0.90,
                },
                {
                    "name": "BuildMat Distributors",
                    "vendor_id": "v-004",
                    "past_projects": 65,
                    "on_time_delivery_pct": 95.0,
                    "quality_rating": 4.5,
                    "safety_record": 0.78,
                    "financial_stability": "strong",
                    "references": 15,
                    "bonding_capacity": 30_000_000,
                    "price_competitiveness": 0.68,
                },
            ]

        scored_vendors: list[dict] = []
        for vendor in vendor_list:
            score_result = await score_vendor(vendor)
            scored_vendors.append(
                {
                    "vendor_name": vendor.get("name", "Unknown"),
                    **score_result,
                }
            )

        # Sort by overall score descending
        scored_vendors.sort(key=lambda v: v.get("overall_score", 0), reverse=True)

        logger.info(
            "Scored %d vendors for project %s",
            len(scored_vendors),
            state["project_id"],
        )
        return {"vendor_scores": scored_vendors, "status": "vendors_scored"}

    except Exception as exc:
        logger.error(
            "Vendor scoring failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"vendor_scores": [], "status": "scoring_failed", "error": str(exc)}


async def assess_contracts_node(state: ProcurementAgentState) -> dict:
    """Assess contract risk for procurement agreements."""
    try:
        materials = state.get("materials", [])

        # Look for contract text in materials data
        contract_text = ""
        for material in materials:
            ct = material.get("contract_text", "")
            if ct:
                contract_text = ct
                break

        if not contract_text:
            # Use a representative contract excerpt for demonstration
            contract_text = (
                "ARTICLE 5 - PAYMENT TERMS\n"
                "5.1 Progress payments shall be made monthly based on work completed. "
                "Owner shall pay within 45 days of receipt of approved invoice. "
                "Retainage of 10% shall be withheld until substantial completion.\n\n"
                "ARTICLE 7 - CHANGES IN THE WORK\n"
                "7.1 The Owner may order changes in the Work. Contractor shall not "
                "proceed with changed work until receiving written authorization. "
                "No adjustment to contract sum or time shall be made without "
                "written Change Order signed by both parties.\n\n"
                "ARTICLE 9 - INDEMNIFICATION\n"
                "9.1 Contractor shall indemnify and hold harmless the Owner from "
                "any and all claims, damages, losses and expenses arising out of "
                "or resulting from performance of the Work, provided that such "
                "claim is attributable to bodily injury or property damage caused "
                "in whole or in part by negligent acts of the Contractor.\n\n"
                "ARTICLE 11 - LIQUIDATED DAMAGES\n"
                "11.1 Time is of the essence. Contractor shall pay liquidated damages "
                "of $5,000 per calendar day for each day of delay beyond the "
                "contract completion date."
            )

        risk_assessment = await score_contract_risk(
            contract_text=contract_text,
            project_type="commercial",
        )

        logger.info(
            "Contract risk assessed for project %s: score=%.1f",
            state["project_id"],
            risk_assessment.get("overall_risk_score", 0),
        )
        return {"contract_risk": risk_assessment, "status": "contracts_assessed"}

    except Exception as exc:
        logger.error(
            "Contract risk assessment failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "contract_risk": None,
            "status": "contract_assessment_failed",
            "error": str(exc),
        }


async def compile_recommendations_node(state: ProcurementAgentState) -> dict:
    """Generate comprehensive procurement strategy recommendations."""
    try:
        price_forecasts = state.get("price_forecasts", {})
        vendor_scores = state.get("vendor_scores", [])
        contract_risk = state.get("contract_risk")

        recommendations: list[dict] = []

        # Price-based recommendations
        if price_forecasts:
            for category, forecast in price_forecasts.items():
                trend = forecast.get("trend", "stable")
                if trend == "rising":
                    recommendations.append(
                        {
                            "type": "price_action",
                            "priority": "high",
                            "category": category,
                            "recommendation": (
                                f"Lock in {category} prices now - forecast shows rising trend. "
                                f"Consider forward contracts or bulk purchasing."
                            ),
                            "potential_savings": "5-15% vs. spot pricing in 6 months",
                        }
                    )
                elif trend == "falling":
                    recommendations.append(
                        {
                            "type": "price_action",
                            "priority": "low",
                            "category": category,
                            "recommendation": (
                                f"Defer {category} procurement if schedule allows - "
                                f"prices showing downward trend."
                            ),
                            "potential_savings": "3-8% if deferred 2-3 months",
                        }
                    )
                else:
                    recommendations.append(
                        {
                            "type": "price_action",
                            "priority": "medium",
                            "category": category,
                            "recommendation": (
                                f"Prices for {category} are stable. Proceed with standard "
                                f"procurement timeline."
                            ),
                            "potential_savings": "Standard market pricing",
                        }
                    )

        # Vendor recommendations
        if vendor_scores:
            top_vendor = vendor_scores[0] if vendor_scores else None
            if top_vendor:
                recommendations.append(
                    {
                        "type": "vendor_selection",
                        "priority": "high",
                        "recommendation": (
                            f"Primary recommendation: {top_vendor.get('vendor_name', 'Unknown')} "
                            f"(score: {top_vendor.get('overall_score', 0):.1f}/100, "
                            f"{top_vendor.get('recommendation', 'recommended')})"
                        ),
                        "risk_flags": top_vendor.get("risk_flags", []),
                    }
                )

            # Flag any vendors with risk concerns
            for vendor in vendor_scores:
                flags = vendor.get("risk_flags", [])
                if flags:
                    recommendations.append(
                        {
                            "type": "vendor_risk",
                            "priority": "medium",
                            "recommendation": (
                                f"Risk flags for {vendor.get('vendor_name', 'Unknown')}: "
                                f"{'; '.join(flags)}"
                            ),
                        }
                    )

        # Contract risk recommendations
        if contract_risk:
            risk_score = contract_risk.get("overall_risk_score", 0)
            if risk_score >= 70:
                recommendations.append(
                    {
                        "type": "contract_risk",
                        "priority": "critical",
                        "recommendation": (
                            f"High contract risk score ({risk_score:.0f}/100). "
                            f"Legal review required before execution."
                        ),
                        "details": contract_risk.get("recommendations", []),
                    }
                )
            elif risk_score >= 40:
                recommendations.append(
                    {
                        "type": "contract_risk",
                        "priority": "medium",
                        "recommendation": (
                            f"Moderate contract risk ({risk_score:.0f}/100). "
                            f"Review highlighted clauses with legal counsel."
                        ),
                        "details": contract_risk.get("recommendations", []),
                    }
                )
            else:
                recommendations.append(
                    {
                        "type": "contract_risk",
                        "priority": "low",
                        "recommendation": (
                            f"Contract risk is acceptable ({risk_score:.0f}/100). "
                            f"Standard review recommended."
                        ),
                    }
                )

        # General procurement strategy
        recommendations.append(
            {
                "type": "strategy",
                "priority": "medium",
                "recommendation": (
                    "Implement procurement schedule aligned with project milestones. "
                    "Pre-qualify vendors for long-lead items at least 90 days before need date."
                ),
            }
        )

        logger.info(
            "Compiled %d procurement recommendations for project %s",
            len(recommendations),
            state["project_id"],
        )
        return {"recommendations": recommendations, "status": "recommendations_compiled"}

    except Exception as exc:
        logger.error(
            "Recommendation compilation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "recommendations": [],
            "status": "compilation_failed",
            "error": str(exc),
        }


def build_procurement_agent(checkpointer=None) -> CompiledStateGraph:
    """Build and compile the LangGraph procurement workflow.

    Graph flow::

        forecast_prices -> score_vendors -> assess_contracts
        -> compile_recommendations -> END

    Returns
    -------
    A compiled LangGraph ``StateGraph``.
    """
    workflow = StateGraph(ProcurementAgentState)

    workflow.add_node("forecast_prices", forecast_prices_node)
    workflow.add_node("score_vendors", score_vendors_node)
    workflow.add_node("assess_contracts", assess_contracts_node)
    workflow.add_node("compile_recommendations", compile_recommendations_node)

    workflow.set_entry_point("forecast_prices")
    workflow.add_edge("forecast_prices", "score_vendors")
    workflow.add_edge("score_vendors", "assess_contracts")
    workflow.add_edge("assess_contracts", "compile_recommendations")
    workflow.add_edge("compile_recommendations", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_procurement_agent(
    project_id: str,
    materials: list | None = None,
) -> dict:
    """Build and invoke the procurement agent.

    Parameters
    ----------
    project_id:
        UUID string of the project.
    materials:
        List of material dicts with category, series_id, vendors, and
        optionally contract_text.

    Returns
    -------
    The final agent state as a dict containing price forecasts, vendor scores,
    contract risk assessment, recommendations, status, and any error information.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_procurement_agent(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"procurement_{uuid.uuid4().hex}"}}
    )

    initial_state: ProcurementAgentState = {
        "project_id": project_id,
        "materials": materials or [],
        "price_forecasts": None,
        "vendor_scores": [],
        "contract_risk": None,
        "recommendations": [],
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
        logger.error("Agent timed out after 300s", extra={"agent": "procurement"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error("Procurement agent failed for %s: %s", project_id, exc)
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
