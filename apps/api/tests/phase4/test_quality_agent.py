"""Tests for quality management LangGraph agent."""

from __future__ import annotations

from app.services.agents.quality_agent import (
    build_quality_agent,
    check_compliance_node,
    classify_defects_node,
    recommend_ncrs_node,
    run_quality_agent,
)


class TestQualityAgent:
    async def test_classify_defects_no_images(self):
        state = {
            "project_id": "test-1",
            "inspection_data": {},
            "images": [],
            "defect_results": None,
            "compliance_results": None,
            "ncr_recommendations": None,
            "status": "processing",
            "error": None,
        }
        result = await classify_defects_node(state)
        assert result["defect_results"] == []

    async def test_check_compliance_node(self):
        state = {
            "project_id": "test-1",
            "inspection_data": {},
            "images": [],
            "defect_results": [],
            "compliance_results": None,
            "ncr_recommendations": None,
            "status": "defects_classified",
            "error": None,
        }
        result = await check_compliance_node(state)
        assert result["compliance_results"] is not None
        assert len(result["compliance_results"]) > 0

    async def test_recommend_ncrs_node(self):
        state = {
            "project_id": "test-1",
            "inspection_data": {},
            "images": [],
            "defect_results": [
                {
                    "defect_type": "crack_structural",
                    "severity_estimate": "critical",
                },
            ],
            "compliance_results": [
                {
                    "regulation_code": "1926.501",
                    "status": "warning",
                    "regulation_title": "Fall Protection",
                },
            ],
            "ncr_recommendations": None,
            "status": "compliance_checked",
            "error": None,
        }
        result = await recommend_ncrs_node(state)
        recs = result["ncr_recommendations"]
        assert len(recs) >= 2

    async def test_build_graph(self):
        graph = build_quality_agent()
        assert graph is not None

    async def test_run_full_agent(self):
        result = await run_quality_agent(
            project_id="test-project-1",
        )
        assert result["status"] == "completed"
        assert result["compliance_results"] is not None
