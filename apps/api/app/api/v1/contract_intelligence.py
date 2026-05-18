"""Contract intelligence API endpoints.

All routes are project-scoped: ``/projects/{project_id}/contracts/...``
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.contract import ContractClause, ContractDocument
from app.models.user import User
from app.schemas.contract_intelligence import (
    ApplyToProjectResponse,
    ClauseListResponse,
    ClauseResponse,
    ComparisonDiffItem,
    ContractCompareRequest,
    ContractComparisonResponse,
    ContractDocumentListResponse,
    ContractDocumentResponse,
    ContractUploadAndParse,
    DeviationCheckResponse,
    DeviationItem,
    UploadAndParseResponse,
)
from app.services.intelligence.contract_intelligence import (
    ExtractedClause,
    apply_contract_to_project,
    check_deviations,
    compare_contracts,
    extract_contract_clauses,
    save_comparison,
    save_extracted_clauses,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Upload + Parse (combined endpoint)
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/contracts/upload-and-parse",
    response_model=UploadAndParseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_and_parse_contract(
    project_id: uuid.UUID,
    body: ContractUploadAndParse,
    current_user: User = Depends(require_permission("contracts", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload contract text, extract clauses, and check deviations in one call."""
    await verify_project_access(project_id, current_user, db)

    # Create the contract document record
    contract_doc = ContractDocument(
        project_id=project_id,
        contract_type=body.contract_type,
        title=body.title,
        parties=body.parties,
        effective_date=body.effective_date,
        expiration_date=body.expiration_date,
        value=body.value,
        status="active",
    )
    db.add(contract_doc)
    await db.flush()
    await db.refresh(contract_doc)

    # Extract clauses via LLM
    extracted = await extract_contract_clauses(
        document_text=body.document_text,
        contract_type=body.contract_type,
        org_id=str(current_user.org_id),
    )

    # Save clauses to DB
    clause_rows = await save_extracted_clauses(db, contract_doc.id, extracted)

    # Check deviations
    deviations = check_deviations(extracted)

    # Build response
    clause_responses = []
    for row in clause_rows:
        await db.refresh(row)
        clause_responses.append(
            ClauseResponse(
                id=row.id,
                contract_document_id=row.contract_document_id,
                clause_type=row.clause_type,
                clause_text=row.clause_text,
                parsed_value=row.parsed_value,
                section_reference=row.section_reference,
                confidence=float(row.confidence),
                created_at=row.created_at,
            )
        )

    deviation_items = [
        DeviationItem(
            clause_type=d.clause_type,
            description=d.description,
            severity=d.severity,
            contract_value=d.contract_value,
            standard_value=d.standard_value,
            recommendation=d.recommendation,
        )
        for d in deviations
    ]

    await db.commit()

    return UploadAndParseResponse(
        contract=ContractDocumentResponse.model_validate(contract_doc),
        clauses=clause_responses,
        clause_count=len(clause_responses),
        deviations=deviation_items,
        deviation_count=len(deviation_items),
    )


# ---------------------------------------------------------------------------
# List contracts
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/contracts",
    response_model=ContractDocumentListResponse,
)
async def list_contracts(
    project_id: uuid.UUID,
    contract_type: str | None = Query(None),
    contract_status: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_permission("contracts", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List contracts for a project with optional filters and pagination."""
    await verify_project_access(project_id, current_user, db)

    stmt = (
        select(ContractDocument)
        .where(ContractDocument.project_id == project_id)
        .order_by(ContractDocument.created_at.desc())
    )

    if contract_type:
        stmt = stmt.where(ContractDocument.contract_type == contract_type)
    if contract_status:
        stmt = stmt.where(ContractDocument.status == contract_status)

    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    contracts = result.scalars().all()

    return ContractDocumentListResponse(
        data=[ContractDocumentResponse.model_validate(c) for c in contracts],
        count=len(contracts),
    )


# ---------------------------------------------------------------------------
# Get clauses
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/contracts/{contract_id}/clauses",
    response_model=ClauseListResponse,
)
async def list_clauses(
    project_id: uuid.UUID,
    contract_id: uuid.UUID,
    clause_type: str | None = Query(None),
    current_user: User = Depends(require_permission("contracts", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List extracted clauses for a contract."""
    await verify_project_access(project_id, current_user, db)

    # Verify contract belongs to project
    await _get_contract_or_raise(db, contract_id, project_id)

    stmt = (
        select(ContractClause)
        .where(ContractClause.contract_document_id == contract_id)
        .order_by(ContractClause.clause_type)
    )

    if clause_type:
        stmt = stmt.where(ContractClause.clause_type == clause_type)

    result = await db.execute(stmt)
    clauses = result.scalars().all()

    return ClauseListResponse(
        data=[
            ClauseResponse(
                id=c.id,
                contract_document_id=c.contract_document_id,
                clause_type=c.clause_type,
                clause_text=c.clause_text,
                parsed_value=c.parsed_value,
                section_reference=c.section_reference,
                confidence=float(c.confidence),
                created_at=c.created_at,
            )
            for c in clauses
        ],
        count=len(clauses),
        contract_document_id=contract_id,
    )


# ---------------------------------------------------------------------------
# Compare contracts
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/contracts/compare",
    response_model=ContractComparisonResponse,
)
async def compare_contracts_endpoint(
    project_id: uuid.UUID,
    body: ContractCompareRequest,
    current_user: User = Depends(require_permission("contracts", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Compare two contracts and identify differences."""
    await verify_project_access(project_id, current_user, db)

    # Verify both contracts belong to project
    await _get_contract_or_raise(db, body.contract_a_id, project_id)
    await _get_contract_or_raise(db, body.contract_b_id, project_id)

    # Fetch clauses for both contracts
    clauses_a = await _fetch_clauses_as_extracted(db, body.contract_a_id)
    clauses_b = await _fetch_clauses_as_extracted(db, body.contract_b_id)

    # Compare
    comparison = compare_contracts(clauses_a, clauses_b)

    # Save comparison to DB
    comp_row = await save_comparison(
        db, body.contract_a_id, body.contract_b_id, comparison, current_user.id
    )
    await db.refresh(comp_row)
    await db.commit()

    return ContractComparisonResponse(
        id=comp_row.id,
        contract_a_id=body.contract_a_id,
        contract_b_id=body.contract_b_id,
        additions=cast(list[ComparisonDiffItem], comparison.additions),
        removals=cast(list[ComparisonDiffItem], comparison.removals),
        changes=cast(list[ComparisonDiffItem], comparison.changes),
        summary=comparison.summary,
        created_at=comp_row.created_at,
    )


# ---------------------------------------------------------------------------
# Check deviations
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/contracts/{contract_id}/check-deviations",
    response_model=DeviationCheckResponse,
)
async def check_deviations_endpoint(
    project_id: uuid.UUID,
    contract_id: uuid.UUID,
    current_user: User = Depends(require_permission("contracts", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Check a contract's clauses against standard construction terms."""
    await verify_project_access(project_id, current_user, db)
    await _get_contract_or_raise(db, contract_id, project_id)

    clauses = await _fetch_clauses_as_extracted(db, contract_id)
    deviations = check_deviations(clauses)

    items = [
        DeviationItem(
            clause_type=d.clause_type,
            description=d.description,
            severity=d.severity,
            contract_value=d.contract_value,
            standard_value=d.standard_value,
            recommendation=d.recommendation,
        )
        for d in deviations
    ]

    critical_count = sum(1 for d in deviations if d.severity == "critical")
    high_count = sum(1 for d in deviations if d.severity == "high")

    return DeviationCheckResponse(
        contract_id=contract_id,
        deviations=items,
        deviation_count=len(items),
        critical_count=critical_count,
        high_count=high_count,
    )


# ---------------------------------------------------------------------------
# Apply to project
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/contracts/{contract_id}/apply",
    response_model=ApplyToProjectResponse,
)
async def apply_to_project_endpoint(
    project_id: uuid.UUID,
    contract_id: uuid.UUID,
    current_user: User = Depends(require_permission("contracts", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Apply extracted contract settings to the project."""
    await verify_project_access(project_id, current_user, db)
    await _get_contract_or_raise(db, contract_id, project_id)

    result = await apply_contract_to_project(db, contract_id, project_id)
    await db.commit()

    return ApplyToProjectResponse(**result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_contract_or_raise(
    db: AsyncSession,
    contract_id: uuid.UUID,
    project_id: uuid.UUID,
) -> ContractDocument:
    """Fetch a contract ensuring it belongs to the project."""
    stmt = select(ContractDocument).where(
        ContractDocument.id == contract_id,
        ContractDocument.project_id == project_id,
    )
    result = await db.execute(stmt)
    contract = result.scalar_one_or_none()
    if contract is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract not found",
        )
    return contract


async def _fetch_clauses_as_extracted(
    db: AsyncSession,
    contract_document_id: uuid.UUID,
) -> list[ExtractedClause]:
    """Fetch DB clauses and convert to ExtractedClause dataclass instances."""
    stmt = select(ContractClause).where(ContractClause.contract_document_id == contract_document_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        ExtractedClause(
            clause_type=row.clause_type,
            clause_text=row.clause_text,
            parsed_value=row.parsed_value,
            section_reference=row.section_reference,
            confidence=float(row.confidence),
        )
        for row in rows
    ]
