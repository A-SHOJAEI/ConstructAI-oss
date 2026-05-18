"""Tests for the compliance report generator.

Pin section construction (safety / quality / schedule), the
compliance_rate property, and the multi-section report assembly
including recommendations.
"""

from __future__ import annotations

import pytest

from app.services.reporting.compliance_report import (
    ComplianceItem,
    ComplianceReport,
    ComplianceSection,
    generate_compliance_report,
    generate_safety_compliance,
)

# =========================================================================
# Dataclasses
# =========================================================================


def test_compliance_item_required_fields():
    """ComplianceItem requires category/requirement/status; rest optional."""
    item = ComplianceItem(
        category="PPE",
        requirement="Hardhats required",
        status="compliant",
    )
    assert item.evidence == ""
    assert item.due_date == ""
    assert item.notes == ""


def test_compliance_section_default_independent_items():
    """Two sections must have independent default-list — guards against
    the mutable-default pitfall."""
    a = ComplianceSection(title="A")
    b = ComplianceSection(title="B")
    a.items.append(ComplianceItem(category="X", requirement="x", status="compliant"))
    assert b.items == []


def test_compliance_section_compliance_rate_empty_is_100():
    """Empty section → 100% compliance (vacuously true)."""
    section = ComplianceSection(title="Empty")
    assert section.compliance_rate == 100.0


def test_compliance_section_all_compliant_100():
    section = ComplianceSection(
        title="All Good",
        items=[
            ComplianceItem(category="x", requirement="x", status="compliant"),
            ComplianceItem(category="y", requirement="y", status="compliant"),
        ],
    )
    assert section.compliance_rate == 100.0


def test_compliance_section_not_applicable_counts_as_compliant():
    """``not_applicable`` items don't drag the rate down."""
    section = ComplianceSection(
        title="Mixed",
        items=[
            ComplianceItem(category="x", requirement="x", status="compliant"),
            ComplianceItem(category="y", requirement="y", status="not_applicable"),
        ],
    )
    assert section.compliance_rate == 100.0


def test_compliance_section_partial_split():
    """1 of 4 compliant → 25%."""
    section = ComplianceSection(
        title="Partial",
        items=[
            ComplianceItem(category="a", requirement="a", status="compliant"),
            ComplianceItem(category="b", requirement="b", status="non_compliant"),
            ComplianceItem(category="c", requirement="c", status="non_compliant"),
            ComplianceItem(category="d", requirement="d", status="partial"),
        ],
    )
    assert section.compliance_rate == 25.0


def test_compliance_report_default_independent_lists():
    a = ComplianceReport(
        project_id="p",
        project_name="A",
        report_date="2026-04-26",
        report_type="monthly",
        overall_score=80.0,
    )
    b = ComplianceReport(
        project_id="p2",
        project_name="B",
        report_date="2026-04-26",
        report_type="monthly",
        overall_score=80.0,
    )
    a.sections.append(ComplianceSection(title="X"))
    a.recommendations.append("rec")
    assert b.sections == []
    assert b.recommendations == []


def test_compliance_report_default_generated_by():
    """Default attribution → ConstructAI Compliance Engine."""
    r = ComplianceReport(
        project_id="p",
        project_name="A",
        report_date="2026-04-26",
        report_type="monthly",
        overall_score=80.0,
    )
    assert r.generated_by == "ConstructAI Compliance Engine"


# =========================================================================
# generate_safety_compliance
# =========================================================================


@pytest.mark.asyncio
async def test_safety_section_perfect_data():
    """All inputs at 100% / 0 violations → 100% compliance section."""
    section = await generate_safety_compliance(
        "p",
        "Project",
        {
            "ppe_compliance_rate": 100,
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    assert section.title == "Safety Compliance"
    assert section.compliance_rate == 100.0
    # 5 documented checks pinned:
    assert len(section.items) == 5


@pytest.mark.asyncio
async def test_safety_section_low_ppe_flagged():
    section = await generate_safety_compliance(
        "p",
        "Project",
        {
            "ppe_compliance_rate": 80,  # < 95 threshold
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    ppe_item = next(i for i in section.items if i.category == "PPE")
    assert ppe_item.status == "non_compliant"


@pytest.mark.asyncio
async def test_safety_section_partial_training():
    """< 100% training → partial (not non_compliant)."""
    section = await generate_safety_compliance(
        "p",
        "Project",
        {"ppe_compliance_rate": 100, "training_completion_rate": 90},
    )
    training_item = next(i for i in section.items if i.category == "Training")
    assert training_item.status == "partial"


@pytest.mark.asyncio
async def test_safety_section_unreported_incidents_non_compliant():
    section = await generate_safety_compliance(
        "p",
        "Project",
        {"unreported_incidents": 3},
    )
    incident_item = next(i for i in section.items if i.category == "Incident Reporting")
    assert incident_item.status == "non_compliant"
    assert "3" in incident_item.evidence


@pytest.mark.asyncio
async def test_safety_section_fall_protection_violation_flagged():
    """Fall-protection violations → non_compliant + reference to OSHA
    1926.501 in the requirement."""
    section = await generate_safety_compliance(
        "p",
        "Project",
        {"fall_protection_violations": 2},
    )
    fp_item = next(i for i in section.items if i.category == "Fall Protection")
    assert fp_item.status == "non_compliant"
    assert "1926.501" in fp_item.requirement


@pytest.mark.asyncio
async def test_safety_section_score_matches_compliance_rate():
    section = await generate_safety_compliance(
        "p",
        "Project",
        {
            "ppe_compliance_rate": 80,  # non-compliant
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    # 4 of 5 compliant → 80% (training=100, unreported=0, inspections=0,
    # falls=0; PPE only one failing)
    assert section.score == 80.0


# =========================================================================
# generate_compliance_report — multi-section assembly
# =========================================================================


@pytest.mark.asyncio
async def test_report_no_data_zero_overall():
    """No safety/quality/schedule data → empty report, 0 overall."""
    report = await generate_compliance_report("p", "Project")
    assert report.sections == []
    assert report.overall_score == 0.0
    assert "0.0%" in report.summary


@pytest.mark.asyncio
async def test_report_safety_only():
    report = await generate_compliance_report(
        "p",
        "Project",
        safety_data={
            "ppe_compliance_rate": 100,
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    assert len(report.sections) == 1
    assert report.overall_score == 100.0


@pytest.mark.asyncio
async def test_report_overall_is_section_average():
    report = await generate_compliance_report(
        "p",
        "Project",
        safety_data={
            "ppe_compliance_rate": 100,
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
        quality_data={
            "defect_rate": 0,
            "inspection_pass_rate": 100,
        },
    )
    # Both sections at 100 → overall 100
    assert report.overall_score == 100.0


@pytest.mark.asyncio
async def test_report_recommendations_list_non_compliant():
    """Each non_compliant item should produce a recommendation entry."""
    report = await generate_compliance_report(
        "p",
        "Project",
        safety_data={
            "ppe_compliance_rate": 50,  # non-compliant
            "training_completion_rate": 100,
            "unreported_incidents": 5,  # non-compliant
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    # 2 non-compliant items → 2 recommendations:
    assert len(report.recommendations) == 2
    joined = " ".join(report.recommendations)
    assert "PPE" in joined
    assert "Incident Reporting" in joined


@pytest.mark.asyncio
async def test_report_summary_includes_count_of_issues():
    report = await generate_compliance_report(
        "p",
        "Project",
        safety_data={
            "ppe_compliance_rate": 50,  # non-compliant
            "training_completion_rate": 100,
            "unreported_incidents": 0,
            "inspections_overdue": 0,
            "fall_protection_violations": 0,
        },
    )
    assert "1 non-compliant" in report.summary


@pytest.mark.asyncio
async def test_report_carries_metadata():
    report = await generate_compliance_report(
        "abc-123",
        "Tower Project",
        report_type="quarterly",
        safety_data={"ppe_compliance_rate": 100},
    )
    assert report.project_id == "abc-123"
    assert report.project_name == "Tower Project"
    assert report.report_type == "quarterly"
    # report_date is today's ISO date:
    assert len(report.report_date) == 10  # YYYY-MM-DD
