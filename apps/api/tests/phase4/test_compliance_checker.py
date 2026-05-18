"""Tests for compliance checking service."""

from __future__ import annotations

from app.services.quality.compliance_checker import (
    OSHA_STANDARDS,
    check_project_compliance,
)


class TestComplianceChecker:
    async def test_check_all_standards(self):
        results = await check_project_compliance(
            project_id="test-project-1",
        )
        # Results include OSHA standards plus IBC checks when project_type is commercial
        assert len(results) >= len(OSHA_STANDARDS)
        for result in results:
            assert "regulation_code" in result
            assert "status" in result

    async def test_check_specific_regulations(self):
        results = await check_project_compliance(
            project_id="test-project-1",
            regulations=["1926.451", "1926.100"],
        )
        assert len(results) == 2

    async def test_unknown_regulation(self):
        results = await check_project_compliance(
            project_id="test-project-1",
            regulations=["9999.999"],
        )
        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    async def test_compliance_with_project_data(self):
        project_data = {
            "safety_measures": [
                {"type": "fall_protection"},
                {"type": "ppe"},
            ],
            "active_zones": ["zone-1"],
        }
        results = await check_project_compliance(
            project_id="test-project-1",
            regulations=["1926.501", "1926.100"],
            project_data=project_data,
        )
        fall_result = next(r for r in results if r["regulation_code"] == "1926.501")
        assert fall_result["status"] == "pass"

    async def test_compliance_warning(self):
        results = await check_project_compliance(
            project_id="test-project-1",
            regulations=["1926.501"],
            project_data={"active_zones": ["zone-1"]},
        )
        assert results[0]["status"] == "warning"

    def test_osha_standards_structure(self):
        for _code, standard in OSHA_STANDARDS.items():
            assert "title" in standard
            assert "category" in standard
