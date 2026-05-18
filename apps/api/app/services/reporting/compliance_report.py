"""Compliance report generation for construction projects.

Generates comprehensive compliance reports covering safety, quality,
schedule, and regulatory requirements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger(__name__)


@dataclass
class ComplianceItem:
    category: str
    requirement: str
    status: str  # compliant | non_compliant | partial | not_applicable
    evidence: str = ""
    due_date: str = ""
    notes: str = ""


@dataclass
class ComplianceSection:
    title: str
    items: list[ComplianceItem] = field(default_factory=list)
    score: float = 0.0  # 0-100

    @property
    def compliance_rate(self) -> float:
        if not self.items:
            return 100.0
        compliant = sum(1 for i in self.items if i.status in ("compliant", "not_applicable"))
        return round(compliant / len(self.items) * 100, 1)


@dataclass
class ComplianceReport:
    project_id: str
    project_name: str
    report_date: str
    report_type: str
    overall_score: float
    sections: list[ComplianceSection] = field(default_factory=list)
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    generated_by: str = "ConstructAI Compliance Engine"


async def generate_safety_compliance(
    project_id: str,
    project_name: str,
    safety_data: dict,
) -> ComplianceSection:
    """Generate safety compliance section."""
    items = []

    # PPE Compliance
    ppe_rate = safety_data.get("ppe_compliance_rate", 0)
    items.append(
        ComplianceItem(
            category="PPE",
            requirement="All workers must wear required PPE at all times",
            status="compliant" if ppe_rate >= 95 else "non_compliant",
            evidence=f"PPE compliance rate: {ppe_rate}%",
            notes=f"Target: 95%, Actual: {ppe_rate}%",
        )
    )

    # Safety training
    training_rate = safety_data.get("training_completion_rate", 0)
    items.append(
        ComplianceItem(
            category="Training",
            requirement="All workers must complete safety orientation",
            status="compliant" if training_rate >= 100 else "partial",
            evidence=f"Training completion: {training_rate}%",
        )
    )

    # Incident reporting
    unreported = safety_data.get("unreported_incidents", 0)
    items.append(
        ComplianceItem(
            category="Incident Reporting",
            requirement="All incidents must be reported within 24 hours",
            status="compliant" if unreported == 0 else "non_compliant",
            evidence=f"Unreported incidents: {unreported}",
        )
    )

    # Safety inspections
    inspections_due = safety_data.get("inspections_overdue", 0)
    items.append(
        ComplianceItem(
            category="Inspections",
            requirement="Weekly safety inspections required",
            status="compliant" if inspections_due == 0 else "non_compliant",
            evidence=f"Overdue inspections: {inspections_due}",
        )
    )

    # Fall protection
    fall_violations = safety_data.get("fall_protection_violations", 0)
    items.append(
        ComplianceItem(
            category="Fall Protection",
            requirement="Fall protection required above 6 feet (OSHA 1926.501)",
            status="compliant" if fall_violations == 0 else "non_compliant",
            evidence=f"Violations detected: {fall_violations}",
        )
    )

    section = ComplianceSection(title="Safety Compliance", items=items)
    section.score = section.compliance_rate
    return section


async def generate_quality_compliance(
    project_id: str,
    quality_data: dict,
) -> ComplianceSection:
    """Generate quality compliance section."""
    items = []

    # Defect rate
    defect_rate = quality_data.get("defect_rate", 0)
    items.append(
        ComplianceItem(
            category="Quality Control",
            requirement="Defect rate must be below 5%",
            status="compliant" if defect_rate < 5 else "non_compliant",
            evidence=f"Current defect rate: {defect_rate}%",
        )
    )

    # Inspection pass rate
    pass_rate = quality_data.get("inspection_pass_rate", 0)
    items.append(
        ComplianceItem(
            category="Inspections",
            requirement="Inspection first-pass rate above 90%",
            status="compliant" if pass_rate >= 90 else "partial",
            evidence=f"First-pass rate: {pass_rate}%",
        )
    )

    # Punch list closure
    punch_open = quality_data.get("open_punch_items", 0)
    punch_overdue = quality_data.get("overdue_punch_items", 0)
    items.append(
        ComplianceItem(
            category="Punch List",
            requirement="No punch list items overdue by more than 14 days",
            status="compliant" if punch_overdue == 0 else "non_compliant",
            evidence=f"Open: {punch_open}, Overdue: {punch_overdue}",
        )
    )

    # RFI response time
    avg_rfi_days = quality_data.get("avg_rfi_response_days", 0)
    items.append(
        ComplianceItem(
            category="RFI Management",
            requirement="RFI responses within 7 business days",
            status="compliant" if avg_rfi_days <= 7 else "non_compliant",
            evidence=f"Average response time: {avg_rfi_days} days",
        )
    )

    section = ComplianceSection(title="Quality Compliance", items=items)
    section.score = section.compliance_rate
    return section


async def generate_schedule_compliance(
    project_id: str,
    schedule_data: dict,
) -> ComplianceSection:
    """Generate schedule compliance section."""
    items = []

    # Schedule variance
    spi = schedule_data.get("spi", 1.0)
    items.append(
        ComplianceItem(
            category="Schedule Performance",
            requirement="Schedule Performance Index (SPI) >= 0.95",
            status="compliant" if spi >= 0.95 else "non_compliant",
            evidence=f"Current SPI: {spi:.2f}",
        )
    )

    # Critical path float
    negative_float = schedule_data.get("negative_float_activities", 0)
    items.append(
        ComplianceItem(
            category="Critical Path",
            requirement="No activities with negative float",
            status="compliant" if negative_float == 0 else "non_compliant",
            evidence=f"Activities with negative float: {negative_float}",
        )
    )

    # Schedule updates
    last_update = schedule_data.get("last_schedule_update", "")
    days_since = 0
    if last_update:
        try:
            update_date = datetime.fromisoformat(last_update).date()
            days_since = (date.today() - update_date).days
        except (ValueError, TypeError):
            days_since = 999
    items.append(
        ComplianceItem(
            category="Schedule Updates",
            requirement="Schedule updated within 7 days",
            status="compliant" if days_since <= 7 else "non_compliant",
            evidence=f"Last update: {days_since} days ago",
        )
    )

    # DCMA compliance
    dcma_score = schedule_data.get("dcma_score", 0)
    items.append(
        ComplianceItem(
            category="DCMA 14-Point",
            requirement="DCMA score >= 80%",
            status="compliant" if dcma_score >= 80 else "partial",
            evidence=f"DCMA score: {dcma_score}%",
        )
    )

    section = ComplianceSection(title="Schedule Compliance", items=items)
    section.score = section.compliance_rate
    return section


async def generate_compliance_report(
    project_id: str,
    project_name: str,
    safety_data: dict | None = None,
    quality_data: dict | None = None,
    schedule_data: dict | None = None,
    report_type: str = "monthly",
) -> ComplianceReport:
    """Generate a comprehensive compliance report.

    Parameters
    ----------
    project_id:
        Project identifier.
    project_name:
        Human-readable project name.
    safety_data:
        Safety metrics for compliance assessment.
    quality_data:
        Quality metrics.
    schedule_data:
        Schedule metrics.
    report_type:
        Report frequency: "weekly", "monthly", or "quarterly".

    Returns
    -------
    ComplianceReport with all sections scored.
    """
    sections = []

    if safety_data:
        sections.append(await generate_safety_compliance(project_id, project_name, safety_data))
    if quality_data:
        sections.append(await generate_quality_compliance(project_id, quality_data))
    if schedule_data:
        sections.append(await generate_schedule_compliance(project_id, schedule_data))

    overall_score = sum(s.score for s in sections) / len(sections) if sections else 0.0

    # Generate recommendations
    recommendations = []
    for section in sections:
        for item in section.items:
            if item.status == "non_compliant":
                recommendations.append(
                    f"[{section.title}] {item.category}: {item.requirement} — "
                    f"Current: {item.evidence}"
                )

    summary = (
        f"Overall compliance score: {overall_score:.1f}%. "
        f"{len(recommendations)} non-compliant items require attention."
    )

    report = ComplianceReport(
        project_id=project_id,
        project_name=project_name,
        report_date=date.today().isoformat(),
        report_type=report_type,
        overall_score=round(overall_score, 1),
        sections=sections,
        summary=summary,
        recommendations=recommendations,
    )

    logger.info(
        "Generated %s compliance report for %s: %.1f%% compliant, %d issues",
        report_type,
        project_name,
        overall_score,
        len(recommendations),
    )

    return report
