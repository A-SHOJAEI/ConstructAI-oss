"""Insurance and risk data export API endpoints."""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.insurance import EMRCalculation
from app.models.user import User
from app.schemas.insurance import (
    EMRCalculateRequest,
    EMRCalculationResponse,
    EMRExportResponse,
    EMRResultResponse,
    ExportRequest,
    InsuranceExportListResponse,
    InsuranceExportResponse,
    LossRunEntryResponse,
    LossRunResponse,
    OSHA300EntryResponse,
    OSHA300LogResponse,
    RiskProfileResponse,
    SafetySummaryResponse,
)
from app.schemas.pagination import PaginationMeta
from app.services.compliance.insurance_export_service import (
    calculate_emr,
    export_to_csv,
    export_to_pdf,
    generate_emr_supporting_docs,
    generate_loss_run,
    generate_osha_300_log,
    generate_risk_profile,
    generate_safety_summary,
    list_exports,
    save_export_record,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _decimal_serializer(obj):
    """Recursively convert Decimal to str in dicts/lists for JSON serialization."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _decimal_serializer(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_serializer(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Safety Summary
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insurance/safety-summary",
    response_model=SafetySummaryResponse,
)
async def get_safety_summary(
    org_id: uuid.UUID,
    date_range_start: date = Query(...),
    date_range_end: date = Query(...),
    project_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get safety summary for insurance underwriters."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    summary = await generate_safety_summary(
        db,
        str(org_id),
        str(project_id) if project_id else None,
        date_range_start,
        date_range_end,
    )
    return SafetySummaryResponse(
        org_id=summary.org_id,
        project_id=summary.project_id,
        date_range_start=summary.date_range_start,
        date_range_end=summary.date_range_end,
        total_hours_worked=summary.total_hours_worked,
        total_recordable_incidents=summary.total_recordable_incidents,
        trir=summary.trir,
        dart_incidents=summary.dart_incidents,
        dart_rate=summary.dart_rate,
        lost_time_injuries=summary.lost_time_injuries,
        ltir=summary.ltir,
        near_misses=summary.near_misses,
        near_miss_frequency=summary.near_miss_frequency,
        severity_rate=summary.severity_rate,
        lost_workdays=summary.lost_workdays,
        incident_by_type=summary.incident_by_type,
    )


# ---------------------------------------------------------------------------
# EMR
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/insurance/emr/calculate",
    response_model=EMRExportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def calculate_emr_endpoint(
    org_id: uuid.UUID,
    request: EMRCalculateRequest,
    current_user: User = Depends(require_permission("insurance", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Calculate Experience Modification Rate with supporting docs."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    if request.actual_losses is not None and request.expected_losses is not None:
        # Use provided values directly
        emr_result = calculate_emr(
            request.actual_losses,
            request.expected_losses,
            request.ballast_value,
            request.weighting_factor,
        )
        return EMRExportResponse(
            emr_result=EMRResultResponse(
                emr_value=emr_result.emr_value,
                actual_primary=emr_result.actual_primary,
                actual_excess=emr_result.actual_excess,
                expected_primary=emr_result.expected_primary,
                expected_excess=emr_result.expected_excess,
                weighting_factor=emr_result.weighting_factor,
                ballast_value=emr_result.ballast_value,
                formula_numerator=emr_result.formula_numerator,
                formula_denominator=emr_result.formula_denominator,
            ),
            payroll_by_class={k: {"payroll": str(v)} for k, v in request.payroll_by_class.items()},
            expected_losses_by_class={},
            actual_losses_detail=[],
            total_payroll=sum(request.payroll_by_class.values(), Decimal(0)),
            total_expected_losses=request.expected_losses,
            total_actual_losses=request.actual_losses,
            calculation_year=request.year,
        )

    # Auto-calculate from payroll and incident data
    export = await generate_emr_supporting_docs(
        db,
        str(org_id),
        request.year,
        request.payroll_by_class,
    )

    expected_by_class_serialized = {k: str(v) for k, v in export.expected_losses_by_class.items()}

    return EMRExportResponse(
        emr_result=EMRResultResponse(
            emr_value=export.emr_result.emr_value,
            actual_primary=export.emr_result.actual_primary,
            actual_excess=export.emr_result.actual_excess,
            expected_primary=export.emr_result.expected_primary,
            expected_excess=export.emr_result.expected_excess,
            weighting_factor=export.emr_result.weighting_factor,
            ballast_value=export.emr_result.ballast_value,
            formula_numerator=export.emr_result.formula_numerator,
            formula_denominator=export.emr_result.formula_denominator,
        ),
        payroll_by_class=export.payroll_by_class,
        expected_losses_by_class=expected_by_class_serialized,
        actual_losses_detail=export.actual_losses_detail,
        total_payroll=export.total_payroll,
        total_expected_losses=export.total_expected_losses,
        total_actual_losses=export.total_actual_losses,
        calculation_year=export.calculation_year,
    )


@router.get(
    "/{org_id}/insurance/emr/{year}",
    response_model=EMRCalculationResponse,
)
async def get_emr_for_year(
    org_id: uuid.UUID,
    year: int,
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get stored EMR calculation for a specific year."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    result = await db.execute(
        select(EMRCalculation)
        .where(
            EMRCalculation.org_id == str(org_id),
            EMRCalculation.calculation_year == year,
        )
        .order_by(EMRCalculation.created_at.desc())
        .limit(1)
    )
    emr = result.scalars().first()
    if emr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No EMR calculation found for year {year}",
        )
    return emr


# ---------------------------------------------------------------------------
# Loss Run
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insurance/loss-run",
    response_model=LossRunResponse,
)
async def get_loss_run(
    org_id: uuid.UUID,
    date_range_start: date = Query(...),
    date_range_end: date = Query(...),
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get loss run report for insurance underwriters."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    loss_run = await generate_loss_run(db, str(org_id), date_range_start, date_range_end)

    entries = []
    for entry in loss_run.entries:
        entries.append(
            {
                "incident_date": entry.incident_date,
                "incident_type": entry.incident_type,
                "description": entry.description,
                "medical_cost": entry.medical_cost,
                "indemnity_cost": entry.indemnity_cost,
                "property_cost": entry.property_cost,
                "total_cost": entry.total_cost,
                "status": entry.status,
                "reserve_amount": entry.reserve_amount,
                "claimant": entry.claimant,
            }
        )

    return LossRunResponse(
        org_id=loss_run.org_id,
        date_range_start=loss_run.date_range_start,
        date_range_end=loss_run.date_range_end,
        entries=cast(list[LossRunEntryResponse], entries),
        total_medical=loss_run.total_medical,
        total_indemnity=loss_run.total_indemnity,
        total_property=loss_run.total_property,
        total_incurred=loss_run.total_incurred,
        total_reserved=loss_run.total_reserved,
        open_claims=loss_run.open_claims,
        closed_claims=loss_run.closed_claims,
    )


# ---------------------------------------------------------------------------
# Risk Profile
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insurance/risk-profile",
    response_model=RiskProfileResponse,
)
async def get_risk_profile(
    org_id: uuid.UUID,
    project_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get comprehensive risk profile for insurance underwriters."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    profile = await generate_risk_profile(
        db,
        str(org_id),
        str(project_id) if project_id else None,
    )
    return RiskProfileResponse(
        org_id=profile.org_id,
        project_id=profile.project_id,
        trir_trend=profile.trir_trend,
        top_risk_categories=profile.top_risk_categories,
        ppe_compliance_rate=profile.ppe_compliance_rate,
        training_hours=profile.training_hours,
        predictive_risk_scores=profile.predictive_risk_scores,
        mitigation_effectiveness=profile.mitigation_effectiveness,
        emr_history=profile.emr_history,
    )


# ---------------------------------------------------------------------------
# OSHA 300
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insurance/osha-300",
    response_model=OSHA300LogResponse,
)
async def get_osha_300(
    org_id: uuid.UUID,
    year: int = Query(..., ge=2000, le=2100),
    establishment: str = Query(default="Main Office"),
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get OSHA Form 300 log data."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    log = await generate_osha_300_log(db, str(org_id), establishment, year)

    entries = []
    for entry in log.entries:
        entries.append(
            {
                "case_number": entry.case_number,
                "employee_name": entry.employee_name,
                "job_title": entry.job_title,
                "date_of_injury": entry.date_of_injury,
                "where_event_occurred": entry.where_event_occurred,
                "description": entry.description,
                "classified_as": entry.classified_as,
                "days_away": entry.days_away,
                "days_restricted": entry.days_restricted,
            }
        )

    return OSHA300LogResponse(
        establishment_name=log.establishment_name,
        org_id=log.org_id,
        year=log.year,
        entries=cast(list[OSHA300EntryResponse], entries),
        total_deaths=log.total_deaths,
        total_days_away_cases=log.total_days_away_cases,
        total_restricted_cases=log.total_restricted_cases,
        total_other_recordable=log.total_other_recordable,
        total_days_away=log.total_days_away,
        total_days_restricted=log.total_days_restricted,
    )


# ---------------------------------------------------------------------------
# Export (CSV/PDF)
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/insurance/export",
)
async def generate_export(
    org_id: uuid.UUID,
    request: ExportRequest,
    current_user: User = Depends(require_permission("insurance", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an exportable insurance package (CSV or PDF)."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    export_type = request.export_type
    fmt = request.format

    # Generate the data. `result` is typed Any so the subsequent asdict()
    # call works uniformly across the different dataclass shapes each
    # generator returns.
    result: Any
    try:
        if export_type == "safety_summary":
            result = await generate_safety_summary(
                db,
                str(org_id),
                str(request.project_id) if request.project_id else None,
                request.date_range_start,
                request.date_range_end,
            )
            data = _decimal_serializer(asdict(result))

        elif export_type == "loss_run":
            result = await generate_loss_run(
                db,
                str(org_id),
                request.date_range_start,
                request.date_range_end,
            )
            data = _decimal_serializer(asdict(result))

        elif export_type == "osha_300":
            result = await generate_osha_300_log(
                db,
                str(org_id),
                "Main Office",
                request.date_range_start.year,
            )
            data = _decimal_serializer(asdict(result))

        elif export_type == "risk_profile":
            result = await generate_risk_profile(
                db,
                str(org_id),
                str(request.project_id) if request.project_id else None,
            )
            data = _decimal_serializer(asdict(result))

        elif export_type == "emr":
            # EMR needs payroll data — return stored data or empty
            data = {"message": "Use POST /emr/calculate for EMR exports"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown export type: {export_type}",
            )
    except Exception as e:
        logger.error("Export generation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export generation failed: {e!s}",
        )

    # Save the export record
    await save_export_record(
        db,
        str(org_id),
        export_type,
        request.date_range_start,
        request.date_range_end,
        data,
        str(current_user.id),
        str(request.project_id) if request.project_id else None,
    )

    if fmt == "json":
        return data

    # Generate file
    if fmt == "csv":
        file_bytes = export_to_csv(data, export_type)
        media_type = "text/csv"
        ext = "csv"
    elif fmt == "pdf":
        try:
            file_bytes = export_to_pdf(data, export_type)
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=str(e),
            )
        media_type = "application/pdf"
        ext = "pdf"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {fmt}",
        )

    filename = f"{export_type}_{request.date_range_start}_{request.date_range_end}.{ext}"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Export History
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insurance/exports",
    response_model=InsuranceExportListResponse,
)
async def list_export_history(
    org_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("insurance", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List previous insurance exports for an organization."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    exports, total = await list_exports(db, str(org_id), skip, limit)
    has_more = (skip + limit) < total

    return InsuranceExportListResponse(
        data=cast(list[InsuranceExportResponse], exports),
        meta=PaginationMeta(has_more=has_more),
        total=total,
    )
