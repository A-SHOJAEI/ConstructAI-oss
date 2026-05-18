"""CloseoutIQ service — spec-driven closeout tracking with magic-link uploads.

Provides:
- Auto-generation of closeout requirements from spec documents (LLM + fallback)
- Filtered listing with pagination
- Magic-link subcontractor upload workflow
- AI-based document validation
- Warranty management and claim filing
- Dashboard analytics with projected completion
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.closeout import (
    CloseoutCommunication,
    CloseoutRequirement,
    WarrantyClaim,
    WarrantyRecord,
)
from app.services.shared.magic_link import generate_magic_link_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default closeout requirements per CSI division (LLM fallback)
# ---------------------------------------------------------------------------

_DEFAULT_REQUIREMENTS: list[dict] = [
    {
        "csi_division": "03",
        "section_title": "Concrete",
        "items": [
            {"type": "warranty", "desc": "Concrete workmanship warranty (1 year)"},
            {"type": "test_report", "desc": "Concrete cylinder break test results"},
            {"type": "certification", "desc": "Ready-mix plant certification"},
        ],
    },
    {
        "csi_division": "04",
        "section_title": "Masonry",
        "items": [
            {"type": "warranty", "desc": "Masonry workmanship warranty (1 year)"},
            {"type": "test_report", "desc": "Mortar/grout test results"},
        ],
    },
    {
        "csi_division": "05",
        "section_title": "Metals",
        "items": [
            {"type": "certification", "desc": "Structural steel mill certificates"},
            {"type": "test_report", "desc": "Weld inspection reports"},
        ],
    },
    {
        "csi_division": "07",
        "section_title": "Thermal & Moisture Protection",
        "items": [
            {"type": "warranty", "desc": "Roofing manufacturer warranty (20 year)"},
            {"type": "warranty", "desc": "Waterproofing warranty (10 year)"},
            {"type": "om_manual", "desc": "Roof maintenance manual"},
        ],
    },
    {
        "csi_division": "08",
        "section_title": "Openings",
        "items": [
            {"type": "warranty", "desc": "Window/glazing warranty (10 year)"},
            {"type": "test_report", "desc": "Air/water infiltration test report"},
        ],
    },
    {
        "csi_division": "09",
        "section_title": "Finishes",
        "items": [
            {"type": "warranty", "desc": "Flooring warranty"},
            {"type": "certification", "desc": "Paint/coating VOC compliance certificates"},
        ],
    },
    {
        "csi_division": "14",
        "section_title": "Conveying Equipment",
        "items": [
            {"type": "warranty", "desc": "Elevator manufacturer warranty"},
            {"type": "om_manual", "desc": "Elevator O&M manual"},
            {"type": "training", "desc": "Elevator maintenance training"},
            {"type": "certification", "desc": "Elevator inspection certificate"},
        ],
    },
    {
        "csi_division": "22",
        "section_title": "Plumbing",
        "items": [
            {"type": "warranty", "desc": "Plumbing system warranty (1 year)"},
            {"type": "om_manual", "desc": "Plumbing O&M manual"},
            {"type": "as_built", "desc": "Plumbing as-built drawings"},
        ],
    },
    {
        "csi_division": "23",
        "section_title": "HVAC",
        "items": [
            {"type": "warranty", "desc": "HVAC equipment manufacturer warranty"},
            {"type": "om_manual", "desc": "HVAC O&M manual"},
            {"type": "test_report", "desc": "TAB (Testing, Adjusting, Balancing) report"},
            {"type": "training", "desc": "HVAC system training for owner"},
            {"type": "as_built", "desc": "HVAC as-built drawings"},
            {"type": "spare_parts", "desc": "HVAC filters and belt inventory"},
        ],
    },
    {
        "csi_division": "26",
        "section_title": "Electrical",
        "items": [
            {"type": "warranty", "desc": "Electrical system warranty (1 year)"},
            {"type": "om_manual", "desc": "Electrical O&M manual"},
            {"type": "test_report", "desc": "Electrical system test reports (Megger, etc.)"},
            {"type": "as_built", "desc": "Electrical as-built drawings"},
            {"type": "certification", "desc": "Panel schedules and arc-flash labels"},
        ],
    },
    {
        "csi_division": "28",
        "section_title": "Electronic Safety and Security",
        "items": [
            {"type": "warranty", "desc": "Fire alarm system warranty"},
            {"type": "om_manual", "desc": "Fire alarm O&M manual"},
            {"type": "certification", "desc": "Fire alarm acceptance test certificate"},
            {"type": "training", "desc": "Fire alarm system training"},
        ],
    },
    {
        "csi_division": "32",
        "section_title": "Exterior Improvements",
        "items": [
            {"type": "warranty", "desc": "Paving/hardscape warranty"},
            {"type": "warranty", "desc": "Landscape warranty (1 year)"},
        ],
    },
    {
        "csi_division": "33",
        "section_title": "Utilities",
        "items": [
            {"type": "test_report", "desc": "Hydrostatic/pressure test results"},
            {"type": "as_built", "desc": "Utility as-built drawings"},
        ],
    },
]


# ---------------------------------------------------------------------------
# LLM-based requirement extraction
# ---------------------------------------------------------------------------


async def _extract_requirements_via_llm(
    chunks: list[dict],
) -> list[dict]:
    """Use LLM to extract closeout requirements from document chunks.

    Returns a list of dicts with keys: csi_division, section_title,
    requirement_type, description, spec_reference.
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()

        combined_text = "\n\n".join(c.get("content", "")[:2000] for c in chunks[:20])

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a construction closeout specialist. Extract all "
                        "closeout requirements from the following specification text. "
                        "For each requirement return a JSON object with: "
                        "csi_division (2-digit string), section_title, "
                        "requirement_type (one of: warranty, om_manual, test_report, "
                        "certification, as_built, training, attic_stock, spare_parts, "
                        "lien_waiver, other), description, spec_reference. "
                        "Return a JSON array."
                    ),
                },
                {"role": "user", "content": combined_text},
            ],
            temperature=0.1,
            max_tokens=4000,
        )

        content = response.choices[0].message.content or "[]"
        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0]
        return json.loads(content)
    except Exception:
        logger.warning(
            "LLM extraction failed, falling back to default requirements",
            exc_info=True,
        )
        return []


# ---------------------------------------------------------------------------
# Core service functions
# ---------------------------------------------------------------------------


async def generate_requirements(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    spec_document_id: uuid.UUID,
) -> list[CloseoutRequirement]:
    """Parse uploaded spec document via RAG to auto-generate closeout requirements.

    1. Fetch document chunks for the spec
    2. Use LLM to extract closeout requirements per CSI division
    3. Fall back to hardcoded common requirements if LLM fails
    4. Create CloseoutRequirement records for each
    """
    from app.models.document import DocumentChunk

    # Fetch spec document chunks
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == spec_document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    chunks = result.scalars().all()

    chunk_dicts = [
        {
            "content": c.content,
            "csi_section": c.csi_section,
            "metadata": c.metadata_,
        }
        for c in chunks
    ]

    # Attempt LLM extraction
    extracted = await _extract_requirements_via_llm(chunk_dicts) if chunk_dicts else []

    requirements: list[CloseoutRequirement] = []

    if extracted:
        for item in extracted:
            req = CloseoutRequirement(
                project_id=project_id,
                organization_id=org_id,
                csi_division=item.get("csi_division"),
                section_title=item.get("section_title"),
                requirement_type=item.get("requirement_type", "other"),
                description=item.get("description"),
                spec_reference=item.get("spec_reference"),
                status="not_started",
            )
            db.add(req)
            requirements.append(req)
    else:
        # Fallback: generate from defaults
        for division in _DEFAULT_REQUIREMENTS:
            for item in division["items"]:
                req = CloseoutRequirement(
                    project_id=project_id,
                    organization_id=org_id,
                    csi_division=division["csi_division"],
                    section_title=division["section_title"],
                    requirement_type=item["type"],
                    description=item["desc"],
                    status="not_started",
                )
                db.add(req)
                requirements.append(req)

    await db.flush()
    for req in requirements:
        await db.refresh(req)

    logger.info(
        "Generated %d closeout requirements for project %s (source=%s)",
        len(requirements),
        project_id,
        "llm" if extracted else "fallback",
    )
    return requirements


async def list_requirements(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    csi_division: str | None = None,
    responsible_sub_id: uuid.UUID | None = None,
    overdue_only: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[CloseoutRequirement], int]:
    """Filtered & paginated listing of closeout requirements."""
    conditions = [CloseoutRequirement.project_id == project_id]

    if status:
        conditions.append(CloseoutRequirement.status == status)
    if csi_division:
        conditions.append(CloseoutRequirement.csi_division == csi_division)
    if responsible_sub_id:
        conditions.append(CloseoutRequirement.responsible_sub_id == responsible_sub_id)
    if overdue_only:
        today = date.today()
        conditions.append(CloseoutRequirement.due_date < today)
        conditions.append(CloseoutRequirement.status.notin_(["accepted", "waived"]))

    where_clause = and_(*conditions)

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(CloseoutRequirement).where(where_clause)
    )
    total = count_result.scalar_one()

    # Paginated data
    offset = (page - 1) * page_size
    data_result = await db.execute(
        select(CloseoutRequirement)
        .where(where_clause)
        .order_by(CloseoutRequirement.csi_division, CloseoutRequirement.created_at)
        .offset(offset)
        .limit(page_size)
    )
    items = list(data_result.scalars().all())

    return items, total


async def update_requirement(
    db: AsyncSession,
    requirement_id: uuid.UUID,
    project_id: uuid.UUID,
    updates: dict,
) -> CloseoutRequirement:
    """Update fields on a closeout requirement."""
    result = await db.execute(
        select(CloseoutRequirement).where(
            CloseoutRequirement.id == requirement_id,
            CloseoutRequirement.project_id == project_id,
        )
    )
    req = result.scalars().first()
    if req is None:
        raise ValueError(f"Closeout requirement {requirement_id} not found")

    allowed_fields = {
        "status",
        "due_date",
        "responsible_sub_name",
        "responsible_sub_email",
        "responsible_sub_id",
        "rejection_notes",
        "due_milestone",
        "pay_app_linkage",
        "description",
    }

    for key, value in updates.items():
        if key in allowed_fields and value is not None:
            setattr(req, key, value)

    req.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(req)
    return req


async def send_document_request(
    db: AsyncSession,
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    user_id: uuid.UUID,
    recipient_email: str,
    *,
    recipient_name: str | None = None,
    message: str | None = None,
) -> dict:
    """Generate a magic link for a subcontractor to upload a closeout document.

    Returns a dict with token_url and communication_id.
    """
    # Verify requirement exists
    result = await db.execute(
        select(CloseoutRequirement).where(
            CloseoutRequirement.id == requirement_id,
            CloseoutRequirement.project_id == project_id,
        )
    )
    req = result.scalars().first()
    if req is None:
        raise ValueError(f"Closeout requirement {requirement_id} not found")

    # Generate magic link token
    raw_token, token_record = await generate_magic_link_token(
        db,
        project_id=project_id,
        org_id=req.organization_id,
        purpose="closeout_upload",
        entity_id=requirement_id,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        expires_in_days=14,
        max_uses=3,
        metadata={
            "requirement_type": req.requirement_type,
            "csi_division": req.csi_division,
            "requested_by": str(user_id),
        },
    )

    # Build upload URL
    token_url = f"/upload/closeout?token={raw_token}"

    # Log communication
    body = message or (
        f"Please upload the required closeout document: {req.description or req.requirement_type}"
    )
    comm = CloseoutCommunication(
        requirement_id=requirement_id,
        channel="email",
        sent_to=recipient_email,
        message_body=body,
        magic_link_token_id=token_record.id,
    )
    db.add(comm)

    # Update requirement status and sub info
    if req.status == "not_started":
        req.status = "requested"
    req.responsible_sub_email = recipient_email
    if recipient_name:
        req.responsible_sub_name = recipient_name
    req.updated_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(comm)

    logger.info(
        "Sent closeout document request for requirement %s to %s",
        requirement_id,
        recipient_email,
    )

    return {
        "token_url": token_url,
        "communication_id": str(comm.id),
        "token_id": str(token_record.id),
    }


async def handle_sub_upload(
    db: AsyncSession,
    token_hash: str,
    s3_key: str,
    filename: str,
) -> CloseoutRequirement:
    """Process a subcontractor's uploaded closeout document.

    Called after file upload is complete. Updates the requirement record,
    sets status to 'submitted', and runs basic AI validation.
    """
    from app.models.magic_link import MagicLinkToken

    # Look up the token to find the requirement
    result = await db.execute(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))
    token_record = result.scalars().first()
    if token_record is None:
        raise ValueError("Invalid upload token")

    requirement_id = token_record.entity_id
    if requirement_id is None:
        raise ValueError("Token is not linked to a closeout requirement")

    req_result = await db.execute(
        select(CloseoutRequirement).where(CloseoutRequirement.id == requirement_id)
    )
    req = req_result.scalars().first()
    if req is None:
        raise ValueError(f"Closeout requirement {requirement_id} not found")

    # Update requirement with uploaded document
    req.submitted_doc_s3_key = s3_key
    req.submitted_doc_name = filename
    req.status = "submitted"
    req.updated_at = datetime.now(UTC)

    # Run basic AI validation (best effort)
    validation_flags = await _validate_document(filename, req.requirement_type)
    req.validation_flags = validation_flags

    await db.flush()
    await db.refresh(req)

    logger.info(
        "Subcontractor uploaded document for requirement %s: %s",
        requirement_id,
        filename,
    )
    return req


async def _validate_document(filename: str, requirement_type: str) -> list[dict]:
    """Run basic validation checks on an uploaded closeout document.

    Returns a list of validation flag dicts with keys: flag, severity, message.
    """
    flags: list[dict] = []

    # Check file extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"pdf", "doc", "docx", "jpg", "jpeg", "png", "tiff", "tif", "xlsx", "xls"}:
        flags.append(
            {
                "flag": "unusual_file_type",
                "severity": "warning",
                "message": f"Unexpected file extension: .{ext}",
            }
        )

    # Type-specific checks
    if requirement_type == "warranty" and ext not in {"pdf", "doc", "docx"}:
        flags.append(
            {
                "flag": "expected_pdf_for_warranty",
                "severity": "info",
                "message": "Warranty letters are typically submitted as PDF or Word documents.",
            }
        )

    if requirement_type == "test_report" and ext in {"jpg", "jpeg", "png"}:
        flags.append(
            {
                "flag": "image_for_test_report",
                "severity": "warning",
                "message": "Test reports submitted as images may be difficult to verify.",
            }
        )

    # LLM-based validation (best effort)
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are reviewing a construction closeout document. "
                        f"The document is a '{requirement_type}' type with filename "
                        f"'{filename}'. Identify potential issues. Return JSON array of "
                        "objects with keys: flag, severity (info/warning/error), message."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Validate this {requirement_type} document: {filename}",
                },
            ],
            temperature=0.1,
            max_tokens=500,
        )
        content = response.choices[0].message.content or "[]"
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0]
        llm_flags = json.loads(content)
        if isinstance(llm_flags, list):
            flags.extend(llm_flags)
    except Exception:
        logger.debug("LLM validation unavailable, using basic checks only")

    return flags


async def review_document(
    db: AsyncSession,
    requirement_id: uuid.UUID,
    project_id: uuid.UUID,
    accepted: bool,
    reviewer_id: uuid.UUID,
    *,
    notes: str | None = None,
) -> CloseoutRequirement:
    """Accept or reject a submitted closeout document.

    When accepting a warranty-type requirement, automatically creates
    a WarrantyRecord linked to the requirement.
    """
    result = await db.execute(
        select(CloseoutRequirement).where(
            CloseoutRequirement.id == requirement_id,
            CloseoutRequirement.project_id == project_id,
        )
    )
    req = result.scalars().first()
    if req is None:
        raise ValueError(f"Closeout requirement {requirement_id} not found")

    now = datetime.now(UTC)
    req.reviewer_id = reviewer_id
    req.reviewed_at = now
    req.updated_at = now

    if accepted:
        req.status = "accepted"
        req.rejection_notes = None

        # Auto-create warranty record for warranty-type requirements
        if req.requirement_type == "warranty":
            warranty = WarrantyRecord(
                project_id=project_id,
                organization_id=req.organization_id,
                closeout_requirement_id=requirement_id,
                warrantor=req.responsible_sub_name or "Unknown",
                system_description=req.section_title,
                coverage_description=req.description,
                warranty_years=1,
                start_date=date.today(),
                end_date=date.today() + timedelta(days=365),
                warranty_letter_s3_key=req.submitted_doc_s3_key,
                status="active",
            )
            db.add(warranty)
    else:
        req.status = "rejected"
        req.rejection_notes = notes

    await db.flush()
    await db.refresh(req)

    logger.info(
        "Reviewed closeout requirement %s: %s",
        requirement_id,
        "accepted" if accepted else "rejected",
    )
    return req


async def get_dashboard(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Aggregate closeout progress for the project dashboard."""
    today = date.today()

    # All requirements for this project
    all_result = await db.execute(
        select(CloseoutRequirement).where(CloseoutRequirement.project_id == project_id)
    )
    all_reqs = list(all_result.scalars().all())

    total_items = len(all_reqs)
    completed_statuses = {"accepted", "waived"}
    completed_items = sum(1 for r in all_reqs if r.status in completed_statuses)
    overall_pct = (completed_items / total_items * 100.0) if total_items > 0 else 0.0

    # Overdue count
    overdue_count = sum(
        1
        for r in all_reqs
        if r.due_date is not None and r.due_date < today and r.status not in completed_statuses
    )

    # Progress by CSI division
    div_counts: dict[str, dict[str, int]] = {}
    for r in all_reqs:
        div = r.csi_division or "Unclassified"
        if div not in div_counts:
            div_counts[div] = {"total": 0, "completed": 0}
        div_counts[div]["total"] += 1
        if r.status in completed_statuses:
            div_counts[div]["completed"] += 1

    progress_by_division = [
        {
            "csi_division": div,
            "total": counts["total"],
            "completed": counts["completed"],
            "pct": round(counts["completed"] / counts["total"] * 100, 1)
            if counts["total"] > 0
            else 0.0,
        }
        for div, counts in sorted(div_counts.items())
    ]

    # Progress by responsible subcontractor
    sub_counts: dict[str, dict[str, int | str | None]] = {}
    for r in all_reqs:
        sub_key = r.responsible_sub_name or "Unassigned"
        if sub_key not in sub_counts:
            sub_counts[sub_key] = {
                "total": 0,
                "completed": 0,
                "sub_id": str(r.responsible_sub_id) if r.responsible_sub_id else None,
            }
        sub_counts[sub_key]["total"] += 1  # type: ignore[operator]
        if r.status in completed_statuses:
            sub_counts[sub_key]["completed"] += 1  # type: ignore[operator]

    progress_by_sub = [
        {
            "responsible_sub_name": name,
            "responsible_sub_id": info["sub_id"],
            "total": info["total"],
            "completed": info["completed"],
            "pct": round(
                int(info["completed"] or 0) / int(info["total"] or 0) * 100,
                1,
            )
            if int(info["total"] or 0) > 0
            else 0.0,
        }
        for name, info in sorted(sub_counts.items())
    ]

    # Projected completion date (linear extrapolation)
    projected_completion_date: str | None = None
    if 0 < completed_items < total_items:
        # Find earliest and latest completion dates to estimate rate
        accepted_reqs = [r for r in all_reqs if r.status in completed_statuses and r.reviewed_at]
        if accepted_reqs:
            first_completed = min(r.reviewed_at for r in accepted_reqs)  # type: ignore[type-var]
            now = datetime.now(UTC)
            days_elapsed = max((now - first_completed).days, 1)  # type: ignore[operator]
            rate_per_day = completed_items / days_elapsed
            remaining = total_items - completed_items
            days_remaining = int(remaining / rate_per_day) if rate_per_day > 0 else 0
            projected = today + timedelta(days=days_remaining)
            projected_completion_date = projected.isoformat()

    return {
        "progress_by_division": progress_by_division,
        "progress_by_sub": progress_by_sub,
        "overdue_count": overdue_count,
        "total_items": total_items,
        "completed_items": completed_items,
        "overall_pct": round(overall_pct, 1),
        "projected_completion_date": projected_completion_date,
    }


async def warranty_check(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Find warranties expiring within 90 days and already expired."""
    today = date.today()
    ninety_days = today + timedelta(days=90)

    all_result = await db.execute(
        select(WarrantyRecord).where(WarrantyRecord.project_id == project_id)
    )
    all_warranties = list(all_result.scalars().all())

    expiring_soon = [
        w
        for w in all_warranties
        if w.end_date is not None and today <= w.end_date <= ninety_days and w.status == "active"
    ]

    expired = [
        w
        for w in all_warranties
        if w.end_date is not None and w.end_date < today and w.status in ("active", "expiring_soon")
    ]

    # Update statuses for expired warranties
    for w in expired:
        w.status = "expired"
        w.updated_at = datetime.now(UTC)

    # Update statuses for expiring soon warranties
    for w in expiring_soon:
        w.status = "expiring_soon"
        w.updated_at = datetime.now(UTC)

    if expired or expiring_soon:
        await db.flush()
        for w in expired + expiring_soon:
            await db.refresh(w)

    return {
        "expiring_soon": expiring_soon,
        "expired": expired,
    }


async def list_warranties(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[WarrantyRecord]:
    """List all warranty records for a project."""
    result = await db.execute(
        select(WarrantyRecord)
        .where(WarrantyRecord.project_id == project_id)
        .order_by(WarrantyRecord.end_date.asc().nullslast())
    )
    return list(result.scalars().all())


async def file_warranty_claim(
    db: AsyncSession,
    warranty_id: uuid.UUID,
    issue_description: str,
    photos: list[str],
    reporter_id: uuid.UUID | None = None,
) -> WarrantyClaim:
    """File a claim against a warranty record."""
    # Verify warranty exists
    warranty = await db.get(WarrantyRecord, warranty_id)
    if warranty is None:
        raise ValueError(f"Warranty record {warranty_id} not found")

    claim = WarrantyClaim(
        warranty_id=warranty_id,
        reported_by=reporter_id,
        issue_description=issue_description,
        photos=photos,
        claim_date=date.today(),
        resolution_status="reported",
    )
    db.add(claim)

    # Update warranty status
    warranty.status = "claimed"
    warranty.updated_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(claim)

    logger.info("Filed warranty claim %s for warranty %s", claim.id, warranty_id)
    return claim
