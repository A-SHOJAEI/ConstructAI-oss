"""
Create 3 change orders at different stages:

CO-001: "Add rooftop terrace" - approved, $350K, +12 days
CO-002: "Foundation redesign" - impact analysis complete, $280K, awaiting approval
CO-003: "Electrical panel upgrade" - newly submitted, $95K, pending analysis
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.database import async_session
from app.models import ChangeOrder

NOW = datetime.now(timezone.utc)


async def seed_change_orders(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    pm_id = ctx["pm_user_id"]
    owner_id = ctx["owner_user_id"]

    async with async_session() as db:
        co_defs = [
            {
                "co_number": "CO-001",
                "title": "Add Rooftop Terrace - Owner Request",
                "description": (
                    "Owner requests addition of a 2,500 SF rooftop terrace on Level 5 "
                    "with outdoor kitchen, fire pit, pergola, and landscape planters. "
                    "Includes waterproofing upgrades, structural reinforcement for "
                    "additional dead load, electrical and plumbing rough-in, and "
                    "railing/guardrail installation per IBC requirements."
                ),
                "status": "approved",
                "change_type": "owner_request",
                "requested_by": owner_id,
                "cost_impact": Decimal("350000.00"),
                "schedule_impact_days": 12,
                "risk_score": Decimal("3.20"),
                "ai_analysis": {
                    "cost_breakdown": {
                        "structural_reinforcement": 85000,
                        "waterproofing": 45000,
                        "hardscape_landscape": 95000,
                        "mep_rough_in": 55000,
                        "railings_guardrails": 35000,
                        "general_conditions": 35000,
                    },
                    "schedule_impact": {
                        "critical_path_affected": True,
                        "activities_impacted": ["A055", "A045", "A080"],
                        "delay_drivers": ["Structural reinforcement at roof level", "Waterproofing complexity"],
                    },
                    "risk_assessment": {
                        "weather_risk": "High - rooftop work in winter months",
                        "coordination_risk": "Medium - additional MEP routing required",
                        "quality_risk": "Low - standard construction methods",
                    },
                    "recommendation": "Approve with weather contingency plan for winter rooftop work",
                },
                "submitted_at": NOW - timedelta(days=30),
                "resolved_at": NOW - timedelta(days=15),
            },
            {
                "co_number": "CO-002",
                "title": "Foundation Redesign - Unforeseen Soil Conditions",
                "description": (
                    "Geotechnical investigation during excavation revealed clay lens at "
                    "elevation -15 feet not identified in original borings. Requires "
                    "redesigned spread footings with increased bearing area and additional "
                    "soil stabilization. Structural engineer has completed revised design."
                ),
                "status": "impact_analysis_complete",
                "change_type": "unforeseen_conditions",
                "requested_by": pm_id,
                "cost_impact": Decimal("280000.00"),
                "schedule_impact_days": 18,
                "risk_score": Decimal("4.10"),
                "ai_analysis": {
                    "cost_breakdown": {
                        "additional_excavation": 45000,
                        "soil_stabilization": 65000,
                        "enlarged_footings_concrete": 85000,
                        "additional_rebar": 35000,
                        "dewatering_extension": 25000,
                        "engineering_redesign": 25000,
                    },
                    "schedule_impact": {
                        "critical_path_affected": True,
                        "activities_impacted": ["A022", "A030", "A031", "A032"],
                        "delay_drivers": [
                            "Soil stabilization curing time (14 days)",
                            "Enlarged footing formwork fabrication",
                        ],
                    },
                    "risk_assessment": {
                        "geotechnical_risk": "High - additional clay lenses may be encountered",
                        "schedule_risk": "High - on critical path, limited float",
                        "cost_risk": "Medium - quantities relatively well-defined",
                    },
                    "recommendation": "Approve urgently - work is on critical path and delay costs $15K/day",
                },
                "submitted_at": NOW - timedelta(days=20),
                "resolved_at": None,
            },
            {
                "co_number": "CO-003",
                "title": "Electrical Panel Upgrade - Code Change",
                "description": (
                    "NEC 2026 adoption by local jurisdiction requires arc-fault circuit "
                    "interrupter (AFCI) protection on additional circuits in commercial "
                    "spaces (Section 210.12). Main electrical panel LP-1A requires upgrade "
                    "from 42-circuit to 54-circuit to accommodate AFCI breakers."
                ),
                "status": "pending",
                "change_type": "code_change",
                "requested_by": pm_id,
                "cost_impact": Decimal("95000.00"),
                "schedule_impact_days": 5,
                "risk_score": None,
                "ai_analysis": {},
                "submitted_at": NOW - timedelta(days=3),
                "resolved_at": None,
            },
        ]

        for co_def in co_defs:
            co = ChangeOrder(
                project_id=project_id,
                co_number=co_def["co_number"],
                title=co_def["title"],
                description=co_def["description"],
                status=co_def["status"],
                change_type=co_def["change_type"],
                requested_by=co_def["requested_by"],
                cost_impact=co_def["cost_impact"],
                schedule_impact_days=co_def["schedule_impact_days"],
                risk_score=co_def["risk_score"],
                ai_analysis=co_def["ai_analysis"],
                submitted_at=co_def["submitted_at"],
                resolved_at=co_def["resolved_at"],
            )
            db.add(co)

        await db.commit()

    return {}
