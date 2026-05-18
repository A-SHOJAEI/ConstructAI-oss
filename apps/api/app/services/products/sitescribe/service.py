"""SiteScribe service — source management and narrative generation for daily reports.

Provides:
- Daily report creation (draft)
- Source upload and processing (photo, voice memo, text, manual)
- AI-powered narrative generation with template fallback
- Report approval workflow
- Dashboard analytics
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import DailyReport
from app.models.report_source import ReportSource

logger = logging.getLogger(__name__)

# Valid source types
VALID_SOURCE_TYPES = {"photo", "voice_memo", "text_message", "manual"}


# ---------------------------------------------------------------------------
# Report creation
# ---------------------------------------------------------------------------


async def create_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    report_date: date,
    user_id: uuid.UUID,
) -> DailyReport:
    """Create a new DailyReport in draft status."""
    report = DailyReport(
        project_id=project_id,
        report_date=report_date,
        status="draft",
        generated_by=str(user_id),
        sections={},
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)

    logger.info(
        "Created SiteScribe report %s for project %s on %s",
        report.id,
        project_id,
        report_date,
    )
    return report


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------


def _extract_exif(_s3_key: str | None) -> dict:
    """Extract EXIF data from a photo.

    This is a mock implementation. In production this would read the file
    from S3 and parse EXIF metadata (GPS coordinates, timestamp, camera model).
    """
    return {}


async def upload_source(
    db: AsyncSession,
    report_id: uuid.UUID,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    source_type: str,
    user_id: uuid.UUID,
    *,
    s3_key: str | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
    text_content: str | None = None,
) -> ReportSource:
    """Create a ReportSource linked to a daily report.

    For photo sources, attempts EXIF extraction (mock).
    Sets initial processing_status based on source type.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"Invalid source_type '{source_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"
        )

    exif_data = _extract_exif(s3_key) if source_type == "photo" else {}

    # Text-based sources are immediately complete; media needs processing
    initial_status = "completed" if source_type in ("text_message", "manual") else "pending"

    source = ReportSource(
        daily_report_id=report_id,
        project_id=project_id,
        organization_id=org_id,
        source_type=source_type,
        s3_key=s3_key,
        filename=filename,
        mime_type=mime_type,
        text_content=text_content,
        exif_data=exif_data,
        processing_status=initial_status,
        created_by=user_id,
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)

    logger.info(
        "Uploaded %s source %s for report %s",
        source_type,
        source.id,
        report_id,
    )
    return source


async def process_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> ReportSource:
    """Process a source based on its type.

    - photo: Run AI tagging (mock — sets generic tags).
    - voice_memo: Transcribe audio (mock — sets placeholder transcript).
    - text_message: Already has content, mark completed.
    - manual: Mark completed.
    """
    result = await db.execute(select(ReportSource).where(ReportSource.id == source_id))
    source = result.scalars().first()
    if source is None:
        raise ValueError(f"ReportSource {source_id} not found")

    if source.source_type == "photo":
        # Mock AI tagging — in production this would call a CV model
        source.ai_tags = {"trade": "unknown", "processed": True}
        source.processing_status = "completed"

    elif source.source_type == "voice_memo":
        # Mock transcription — in production this would call Whisper / speech-to-text
        source.transcript = "Voice memo transcript pending"
        source.processing_status = "completed"

    elif source.source_type == "text_message" or source.source_type == "manual":
        source.processing_status = "completed"

    else:
        source.processing_status = "failed"

    await db.flush()
    await db.refresh(source)

    logger.info(
        "Processed source %s (type=%s, status=%s)",
        source.id,
        source.source_type,
        source.processing_status,
    )
    return source


async def list_sources(
    db: AsyncSession,
    report_id: uuid.UUID,
) -> list[ReportSource]:
    """Return all sources for a given daily report."""
    result = await db.execute(
        select(ReportSource)
        .where(ReportSource.daily_report_id == report_id)
        .order_by(ReportSource.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------


def _build_template_narrative(
    report_date: date,
    project_id: uuid.UUID,
    sources: list[ReportSource],
) -> str:
    """Build a fallback template narrative when LLM is unavailable."""
    photo_count = sum(1 for s in sources if s.source_type == "photo")
    voice_count = sum(1 for s in sources if s.source_type == "voice_memo")
    text_count = sum(1 for s in sources if s.source_type in ("text_message", "manual"))

    return (
        f"Daily Construction Report - {report_date.isoformat()}\n"
        f"Project: {project_id}\n"
        f"\n"
        f"Sources reviewed: {len(sources)} "
        f"({photo_count} photos, {voice_count} voice memos, {text_count} text notes)\n"
        f"\n"
        f"Work performed today based on field documentation.\n"
        f"Further details pending superintendent review."
    )


async def generate_narrative(
    db: AsyncSession,
    report_id: uuid.UUID,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    include_previous_day: bool = True,
) -> DailyReport:
    """Generate an AI narrative from all completed sources for the report.

    Gathers completed sources, builds context, attempts LLM generation,
    and falls back to a structured template if LLM is unavailable.
    """
    # Fetch the report
    report_result = await db.execute(
        select(DailyReport).where(
            DailyReport.id == report_id,
            DailyReport.project_id == project_id,
        )
    )
    report = report_result.scalars().first()
    if report is None:
        raise ValueError(f"DailyReport {report_id} not found")

    # Gather completed sources
    sources = await list_sources(db, report_id)
    completed_sources = [s for s in sources if s.processing_status == "completed"]

    # Build context from sources
    context_parts: list[str] = []
    for src in completed_sources:
        if src.source_type == "photo" and src.ai_tags:
            context_parts.append(f"[Photo] {src.filename or 'unnamed'}: tags={src.ai_tags}")
        elif src.source_type == "voice_memo" and src.transcript:
            context_parts.append(f"[Voice] {src.transcript}")
        elif src.text_content:
            context_parts.append(f"[{src.source_type}] {src.text_content}")

    # Attempt LLM narrative generation
    narrative: str | None = None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()

        source_context = "\n".join(context_parts) if context_parts else "No sources available."
        prompt = (
            f"Generate a professional daily construction report narrative for "
            f"{report.report_date.isoformat()}.\n\n"
            f"Field documentation:\n{source_context}\n\n"
            f"Write a concise, factual narrative summarizing work performed, "
            f"conditions observed, and any notable items. "
            f"Use construction industry standard language."
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a construction project manager writing a daily report. "
                        "Be factual and concise. Use bullet points for clarity."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        narrative = response.choices[0].message.content
    except Exception:
        logger.warning(
            "LLM narrative generation failed for report %s, using template fallback",
            report_id,
            exc_info=True,
        )

    # Fallback to template
    if not narrative:
        narrative = _build_template_narrative(report.report_date, project_id, completed_sources)

    # Update the report
    report.content_markdown = narrative
    report.sections = {
        "source_count": len(completed_sources),
        "generation_method": "llm"
        if narrative != _build_template_narrative(report.report_date, project_id, completed_sources)
        else "template",
    }
    await db.flush()
    await db.refresh(report)

    logger.info(
        "Generated narrative for report %s (%d sources)",
        report_id,
        len(completed_sources),
    )
    return report


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------


async def approve_report(
    db: AsyncSession,
    report_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    reviewer_notes: str | None = None,
) -> DailyReport:
    """Approve a daily report, setting status and reviewer metadata."""
    result = await db.execute(
        select(DailyReport).where(
            DailyReport.id == report_id,
            DailyReport.project_id == project_id,
        )
    )
    report = result.scalars().first()
    if report is None:
        raise ValueError(f"DailyReport {report_id} not found")

    now = datetime.now(UTC)
    report.status = "approved"
    report.reviewed_by = user_id
    report.published_at = now

    # Store reviewer notes in sections if provided
    if reviewer_notes:
        sections = dict(report.sections) if report.sections else {}
        sections["reviewer_notes"] = reviewer_notes
        report.sections = sections

    await db.flush()
    await db.refresh(report)

    logger.info("Approved report %s by user %s", report_id, user_id)
    return report


# ---------------------------------------------------------------------------
# Report retrieval
# ---------------------------------------------------------------------------


async def get_report_with_sources(
    db: AsyncSession,
    report_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Fetch a DailyReport along with all its associated sources."""
    result = await db.execute(
        select(DailyReport).where(
            DailyReport.id == report_id,
            DailyReport.project_id == project_id,
        )
    )
    report = result.scalars().first()
    if report is None:
        raise ValueError(f"DailyReport {report_id} not found")

    sources = await list_sources(db, report_id)

    return {
        "report": report,
        "sources": sources,
    }


async def list_reports(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[DailyReport], int]:
    """Paginated listing of daily reports with optional filters."""
    conditions = [DailyReport.project_id == project_id]

    if status:
        conditions.append(DailyReport.status == status)
    if date_from:
        conditions.append(DailyReport.report_date >= date_from)
    if date_to:
        conditions.append(DailyReport.report_date <= date_to)

    where_clause = and_(*conditions)

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(DailyReport).where(where_clause)
    )
    total = count_result.scalar_one()

    # Paginated data
    offset = (page - 1) * page_size
    data_result = await db.execute(
        select(DailyReport)
        .where(where_clause)
        .order_by(DailyReport.report_date.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list(data_result.scalars().all())

    return items, total


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def get_dashboard(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Aggregate SiteScribe metrics for the project dashboard."""
    # Fetch all reports for the project
    reports_result = await db.execute(
        select(DailyReport).where(DailyReport.project_id == project_id)
    )
    all_reports = list(reports_result.scalars().all())

    total_reports = len(all_reports)
    draft_count = sum(1 for r in all_reports if r.status == "draft")
    approved_count = sum(1 for r in all_reports if r.status == "approved")

    latest_report_date: str | None = None
    if all_reports:
        latest = max(r.report_date for r in all_reports)
        latest_report_date = latest.isoformat()

    # Average sources per report
    avg_sources = 0.0
    if total_reports > 0:
        report_ids = [r.id for r in all_reports]
        source_count_result = await db.execute(
            select(func.count())
            .select_from(ReportSource)
            .where(ReportSource.daily_report_id.in_(report_ids))
        )
        total_sources = source_count_result.scalar_one()
        avg_sources = round(total_sources / total_reports, 2)

    return {
        "total_reports": total_reports,
        "draft_count": draft_count,
        "approved_count": approved_count,
        "latest_report_date": latest_report_date,
        "avg_sources_per_report": avg_sources,
    }
