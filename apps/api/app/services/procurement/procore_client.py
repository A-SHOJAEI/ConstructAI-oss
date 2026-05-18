"""Procore API mock client for construction project management integration."""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


class ProcoreClient:
    """Client for interacting with Procore API.

    In production, this would use OAuth2 and real API calls.
    Currently returns mock data for development and testing.
    """

    def __init__(
        self,
        base_url: str = "https://api.procore.com",
        token: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self._client = None

    async def get_projects(self, company_id: str) -> list[dict]:
        """Get projects from Procore.

        Parameters
        ----------
        company_id:
            The Procore company identifier.

        Returns
        -------
        List of project dicts with id, name, status, and metadata.
        """
        logger.info("Fetching projects for company %s", company_id)

        today = date.today()
        return [
            {
                "id": "proj-001",
                "name": "Downtown Office Tower",
                "company_id": company_id,
                "status": "active",
                "project_type": "commercial",
                "address": "123 Main St, Austin, TX 78701",
                "start_date": (today - timedelta(days=120)).isoformat(),
                "estimated_completion": (today + timedelta(days=240)).isoformat(),
                "total_budget": 45_000_000.00,
                "percent_complete": 33.5,
                "superintendent": "Mike Johnson",
                "project_manager": "Sarah Chen",
            },
            {
                "id": "proj-002",
                "name": "Riverside Mixed-Use Development",
                "company_id": company_id,
                "status": "active",
                "project_type": "mixed_use",
                "address": "456 River Rd, Austin, TX 78702",
                "start_date": (today - timedelta(days=60)).isoformat(),
                "estimated_completion": (today + timedelta(days=420)).isoformat(),
                "total_budget": 78_500_000.00,
                "percent_complete": 12.8,
                "superintendent": "Carlos Rivera",
                "project_manager": "Jennifer Walsh",
            },
            {
                "id": "proj-003",
                "name": "Industrial Warehouse Expansion",
                "company_id": company_id,
                "status": "planning",
                "project_type": "industrial",
                "address": "789 Commerce Dr, Round Rock, TX 78664",
                "start_date": (today + timedelta(days=30)).isoformat(),
                "estimated_completion": (today + timedelta(days=300)).isoformat(),
                "total_budget": 12_750_000.00,
                "percent_complete": 0.0,
                "superintendent": "Tom Bradley",
                "project_manager": "Lisa Nguyen",
            },
        ]

    async def get_rfis(self, project_id: str) -> list[dict]:
        """Get RFIs for a project.

        Parameters
        ----------
        project_id:
            The Procore project identifier.

        Returns
        -------
        List of RFI dicts with id, subject, status, and details.
        """
        logger.info("Fetching RFIs for project %s", project_id)

        today = date.today()
        return [
            {
                "id": "rfi-101",
                "project_id": project_id,
                "number": 1,
                "subject": "Foundation Reinforcement Detail Clarification",
                "status": "open",
                "priority": "high",
                "assignee": "Structural Engineer",
                "created_date": (today - timedelta(days=5)).isoformat(),
                "due_date": (today + timedelta(days=2)).isoformat(),
                "ball_in_court": "Architect",
                "cost_impact": "potential",
                "schedule_impact_days": 3,
            },
            {
                "id": "rfi-102",
                "project_id": project_id,
                "number": 2,
                "subject": "MEP Routing Conflict at Level 3",
                "status": "answered",
                "priority": "medium",
                "assignee": "MEP Coordinator",
                "created_date": (today - timedelta(days=12)).isoformat(),
                "due_date": (today - timedelta(days=5)).isoformat(),
                "ball_in_court": "Contractor",
                "cost_impact": "none",
                "schedule_impact_days": 0,
            },
            {
                "id": "rfi-103",
                "project_id": project_id,
                "number": 3,
                "subject": "Exterior Cladding Material Substitution",
                "status": "open",
                "priority": "medium",
                "assignee": "Architect",
                "created_date": (today - timedelta(days=3)).isoformat(),
                "due_date": (today + timedelta(days=7)).isoformat(),
                "ball_in_court": "Architect",
                "cost_impact": "potential",
                "schedule_impact_days": 5,
            },
            {
                "id": "rfi-104",
                "project_id": project_id,
                "number": 4,
                "subject": "Concrete Mix Design Approval",
                "status": "closed",
                "priority": "high",
                "assignee": "Structural Engineer",
                "created_date": (today - timedelta(days=20)).isoformat(),
                "due_date": (today - timedelta(days=13)).isoformat(),
                "ball_in_court": "Closed",
                "cost_impact": "none",
                "schedule_impact_days": 0,
            },
        ]

    async def get_submittals(self, project_id: str) -> list[dict]:
        """Get submittals for a project.

        Parameters
        ----------
        project_id:
            The Procore project identifier.

        Returns
        -------
        List of submittal dicts with id, title, status, and details.
        """
        logger.info("Fetching submittals for project %s", project_id)

        today = date.today()
        return [
            {
                "id": "sub-201",
                "project_id": project_id,
                "number": "03-001",
                "title": "Concrete Mix Design - 4000 PSI",
                "spec_section": "03 30 00",
                "status": "approved",
                "submitted_date": (today - timedelta(days=15)).isoformat(),
                "approved_date": (today - timedelta(days=8)).isoformat(),
                "vendor": "Central Ready Mix",
                "reviewer": "Structural Engineer",
            },
            {
                "id": "sub-202",
                "project_id": project_id,
                "number": "05-001",
                "title": "Structural Steel Shop Drawings",
                "spec_section": "05 12 00",
                "status": "under_review",
                "submitted_date": (today - timedelta(days=7)).isoformat(),
                "approved_date": None,
                "vendor": "Pacific Steel Fabricators",
                "reviewer": "Structural Engineer",
            },
            {
                "id": "sub-203",
                "project_id": project_id,
                "number": "08-001",
                "title": "Curtain Wall System",
                "spec_section": "08 44 00",
                "status": "revise_and_resubmit",
                "submitted_date": (today - timedelta(days=20)).isoformat(),
                "approved_date": None,
                "vendor": "GlasCraft Systems",
                "reviewer": "Architect",
            },
            {
                "id": "sub-204",
                "project_id": project_id,
                "number": "23-001",
                "title": "HVAC Rooftop Units",
                "spec_section": "23 74 00",
                "status": "pending",
                "submitted_date": None,
                "approved_date": None,
                "vendor": "Carrier Commercial",
                "reviewer": "Mechanical Engineer",
            },
        ]

    async def get_change_orders(self, project_id: str) -> list[dict]:
        """Get change orders for a project.

        Parameters
        ----------
        project_id:
            The Procore project identifier.

        Returns
        -------
        List of change order dicts with id, title, status, and cost data.
        """
        logger.info("Fetching change orders for project %s", project_id)

        today = date.today()
        return [
            {
                "id": "co-301",
                "project_id": project_id,
                "number": 1,
                "title": "Additional Foundation Piers - Unforeseen Soil Conditions",
                "status": "approved",
                "created_date": (today - timedelta(days=30)).isoformat(),
                "approved_date": (today - timedelta(days=20)).isoformat(),
                "cost_impact": 185_000.00,
                "schedule_impact_days": 8,
                "change_reason": "unforeseen_conditions",
                "description": (
                    "Geotechnical investigation revealed soft soil stratum "
                    "requiring 12 additional drilled piers at the northwest "
                    "corner of the building."
                ),
            },
            {
                "id": "co-302",
                "project_id": project_id,
                "number": 2,
                "title": "Owner-Requested Lobby Upgrade",
                "status": "pending",
                "created_date": (today - timedelta(days=10)).isoformat(),
                "approved_date": None,
                "cost_impact": 320_000.00,
                "schedule_impact_days": 12,
                "change_reason": "owner_request",
                "description": (
                    "Upgrade lobby finishes from standard tile to imported "
                    "marble with custom lighting fixtures per owner directive."
                ),
            },
            {
                "id": "co-303",
                "project_id": project_id,
                "number": 3,
                "title": "Fire Sprinkler System Redesign",
                "status": "draft",
                "created_date": (today - timedelta(days=3)).isoformat(),
                "approved_date": None,
                "cost_impact": 95_000.00,
                "schedule_impact_days": 5,
                "change_reason": "design_error",
                "description": (
                    "Sprinkler system redesign required due to conflict "
                    "with structural beam locations not shown on original "
                    "MEP coordination drawings."
                ),
            },
        ]

    async def get_budget(self, project_id: str) -> dict:
        """Get budget data for a project.

        Parameters
        ----------
        project_id:
            The Procore project identifier.

        Returns
        -------
        Budget summary dict with cost codes, original budget, revisions,
        and current status.
        """
        logger.info("Fetching budget for project %s", project_id)

        return {
            "project_id": project_id,
            "original_budget": 45_000_000.00,
            "approved_changes": 185_000.00,
            "pending_changes": 415_000.00,
            "revised_budget": 45_185_000.00,
            "committed_costs": 32_400_000.00,
            "actual_costs": 15_200_000.00,
            "projected_cost": 45_850_000.00,
            "variance": -665_000.00,
            "cost_codes": [
                {
                    "code": "02",
                    "description": "Existing Conditions",
                    "budget": 1_200_000.00,
                    "committed": 1_150_000.00,
                    "actual": 980_000.00,
                    "projected": 1_180_000.00,
                },
                {
                    "code": "03",
                    "description": "Concrete",
                    "budget": 6_500_000.00,
                    "committed": 6_200_000.00,
                    "actual": 3_100_000.00,
                    "projected": 6_450_000.00,
                },
                {
                    "code": "05",
                    "description": "Metals",
                    "budget": 8_200_000.00,
                    "committed": 7_800_000.00,
                    "actual": 2_400_000.00,
                    "projected": 8_350_000.00,
                },
                {
                    "code": "07",
                    "description": "Thermal & Moisture Protection",
                    "budget": 3_100_000.00,
                    "committed": 2_900_000.00,
                    "actual": 850_000.00,
                    "projected": 3_050_000.00,
                },
                {
                    "code": "08",
                    "description": "Openings",
                    "budget": 4_500_000.00,
                    "committed": 4_200_000.00,
                    "actual": 1_200_000.00,
                    "projected": 4_600_000.00,
                },
                {
                    "code": "09",
                    "description": "Finishes",
                    "budget": 5_800_000.00,
                    "committed": 3_500_000.00,
                    "actual": 800_000.00,
                    "projected": 5_900_000.00,
                },
                {
                    "code": "23",
                    "description": "HVAC",
                    "budget": 7_200_000.00,
                    "committed": 6_650_000.00,
                    "actual": 2_870_000.00,
                    "projected": 7_320_000.00,
                },
                {
                    "code": "26",
                    "description": "Electrical",
                    "budget": 5_500_000.00,
                    "committed": 0.00,
                    "actual": 0.00,
                    "projected": 5_500_000.00,
                },
                {
                    "code": "31",
                    "description": "Earthwork",
                    "budget": 3_000_000.00,
                    "committed": 0.00,
                    "actual": 3_000_000.00,
                    "projected": 3_500_000.00,
                },
            ],
        }

    async def sync_cost_data(self, project_id: str) -> dict:
        """Sync cost data from Procore.

        Simulates pulling the latest cost data from Procore and returning
        a summary of what was synced.

        Parameters
        ----------
        project_id:
            The Procore project identifier.

        Returns
        -------
        Sync summary dict with counts of synced items and timestamp.
        """
        logger.info("Syncing cost data for project %s", project_id)

        budget = await self.get_budget(project_id)
        change_orders = await self.get_change_orders(project_id)

        return {
            "project_id": project_id,
            "sync_timestamp": date.today().isoformat(),
            "status": "completed",
            "items_synced": {
                "cost_codes": len(budget.get("cost_codes", [])),
                "change_orders": len(change_orders),
                "committed_contracts": 14,
                "direct_costs": 87,
            },
            "budget_summary": {
                "original_budget": budget["original_budget"],
                "revised_budget": budget["revised_budget"],
                "actual_costs": budget["actual_costs"],
                "projected_cost": budget["projected_cost"],
            },
        }
