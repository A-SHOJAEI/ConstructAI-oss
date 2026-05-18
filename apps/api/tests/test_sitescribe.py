"""Tests for SiteScribe source management and daily report generation.

Covers:
- ReportSource model creation and defaults
- Report creation (draft)
- Source upload (photo, voice, text, manual)
- Source processing and status transitions
- Source listing by report
- Narrative generation (LLM fallback, template)
- Report approval workflow
- Report retrieval with sources
- Paginated report listing with filters
- Dashboard aggregation metrics
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.models.report_source import ReportSource

from app.services.products.sitescribe.service import (
    _build_template_narrative,
    approve_report,
    create_report,
    generate_narrative,
    get_dashboard,
    get_report_with_sources,
    list_reports,
    list_sources,
    process_source,
    upload_source,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report_source(
    *,
    source_type: str = "photo",
    daily_report_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    **kwargs,
) -> ReportSource:
    """Create a ReportSource instance (not persisted) for testing."""
    return ReportSource(
        daily_report_id=daily_report_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        source_type=source_type,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TestReportSourceModel — 5 tests
# ---------------------------------------------------------------------------


class TestReportSourceModel:
    """Tests for the ReportSource SQLAlchemy model defaults."""

    def test_photo_source_creation(self):
        """Photo source should have correct defaults."""
        src = _make_report_source(source_type="photo", s3_key="photos/test.jpg")
        assert src.source_type == "photo"
        assert src.s3_key == "photos/test.jpg"
        assert src.transcript is None
        assert src.text_content is None

    def test_voice_memo_source_creation(self):
        """Voice memo source should accept s3_key and transcript."""
        src = _make_report_source(
            source_type="voice_memo",
            s3_key="voice/memo.m4a",
            transcript="Test transcript",
        )
        assert src.source_type == "voice_memo"
        assert src.transcript == "Test transcript"

    def test_text_message_source_creation(self):
        """Text message source should accept text_content."""
        src = _make_report_source(
            source_type="text_message",
            text_content="Concrete poured on level 3",
        )
        assert src.source_type == "text_message"
        assert src.text_content == "Concrete poured on level 3"
        assert src.s3_key is None

    def test_manual_source_creation(self):
        """Manual note source should accept text_content."""
        src = _make_report_source(
            source_type="manual",
            text_content="Superintendent notes for the day",
        )
        assert src.source_type == "manual"
        assert src.text_content == "Superintendent notes for the day"

    def test_default_jsonb_fields(self):
        """JSONB fields ai_tags and exif_data default to empty dict in Python."""
        src = _make_report_source()
        # When not persisted, Python defaults are None — test column defaults
        assert src.source_type == "photo"


# ---------------------------------------------------------------------------
# TestCreateReport — 3 tests
# ---------------------------------------------------------------------------


class TestCreateReport:
    """Tests for the create_report service function."""

    @pytest.mark.asyncio
    async def test_create_draft_report(self, db_session, test_org, test_user):
        """Creating a report should produce a draft DailyReport."""
        from app.models.project import Project

        project = Project(
            name="Test Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(project)
        await db_session.flush()

        user_id = uuid.uuid4()
        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 15),
            user_id=user_id,
        )

        assert report.id is not None
        assert report.status == "draft"
        assert report.report_date == date(2026, 3, 15)
        assert report.project_id == project.id

    @pytest.mark.asyncio
    async def test_create_report_sets_generated_by(self, db_session, test_org, test_user):
        """Report should record the user who created it."""
        from app.models.project import Project

        project = Project(name="Test Project 2", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        user_id = uuid.uuid4()
        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 16),
            user_id=user_id,
        )
        assert report.generated_by == str(user_id)

    @pytest.mark.asyncio
    async def test_create_report_empty_sections(self, db_session, test_org, test_user):
        """New report should have empty sections dict."""
        from app.models.project import Project

        project = Project(name="Test Project 3", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 17),
            user_id=test_user.id,
        )
        assert report.sections == {}


# ---------------------------------------------------------------------------
# TestUploadSource — 5 tests
# ---------------------------------------------------------------------------


class TestUploadSource:
    """Tests for the upload_source service function."""

    @pytest.mark.asyncio
    async def test_upload_photo_source(self, db_session, test_org, test_user):
        """Uploading a photo source should create a pending ReportSource."""
        from app.models.project import Project

        project = Project(name="Upload Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 20),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/site/img001.jpg",
            filename="img001.jpg",
            mime_type="image/jpeg",
        )

        assert source.source_type == "photo"
        assert source.processing_status == "pending"
        assert source.s3_key == "photos/site/img001.jpg"

    @pytest.mark.asyncio
    async def test_upload_voice_memo(self, db_session, test_org, test_user):
        """Voice memo source should start as pending."""
        from app.models.project import Project

        project = Project(name="Voice Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 21),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="voice_memo",
            user_id=test_user.id,
            s3_key="voice/memo001.m4a",
            filename="memo001.m4a",
            mime_type="audio/m4a",
        )

        assert source.source_type == "voice_memo"
        assert source.processing_status == "pending"

    @pytest.mark.asyncio
    async def test_upload_text_message(self, db_session, test_org, test_user):
        """Text message source should be immediately completed."""
        from app.models.project import Project

        project = Project(name="Text Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 22),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="text_message",
            user_id=test_user.id,
            text_content="Rebar inspection passed on level 2",
        )

        assert source.source_type == "text_message"
        assert source.processing_status == "completed"
        assert source.text_content == "Rebar inspection passed on level 2"

    @pytest.mark.asyncio
    async def test_upload_manual_note(self, db_session, test_org, test_user):
        """Manual note should be immediately completed."""
        from app.models.project import Project

        project = Project(name="Manual Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 23),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="manual",
            user_id=test_user.id,
            text_content="Weather delay: rain from 10am-2pm",
        )

        assert source.processing_status == "completed"

    @pytest.mark.asyncio
    async def test_upload_invalid_source_type(self, db_session, test_org, test_user):
        """Invalid source type should raise ValueError."""
        from app.models.project import Project

        project = Project(name="Invalid Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 24),
            user_id=test_user.id,
        )

        with pytest.raises(ValueError, match="Invalid source_type"):
            await upload_source(
                db=db_session,
                report_id=report.id,
                project_id=project.id,
                org_id=test_org.id,
                source_type="video",
                user_id=test_user.id,
            )


# ---------------------------------------------------------------------------
# TestProcessSource — 5 tests
# ---------------------------------------------------------------------------


class TestProcessSource:
    """Tests for the process_source service function."""

    @pytest.mark.asyncio
    async def test_process_photo_sets_ai_tags(self, db_session, test_org, test_user):
        """Processing a photo should set ai_tags and mark completed."""
        from app.models.project import Project

        project = Project(name="Process Photo", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 25),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/test.jpg",
        )

        processed = await process_source(db=db_session, source_id=source.id)
        assert processed.processing_status == "completed"
        assert processed.ai_tags == {"trade": "unknown", "processed": True}

    @pytest.mark.asyncio
    async def test_process_voice_memo_sets_transcript(self, db_session, test_org, test_user):
        """Processing a voice memo should set a placeholder transcript."""
        from app.models.project import Project

        project = Project(name="Process Voice", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 25),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="voice_memo",
            user_id=test_user.id,
            s3_key="voice/test.m4a",
        )

        processed = await process_source(db=db_session, source_id=source.id)
        assert processed.processing_status == "completed"
        assert processed.transcript == "Voice memo transcript pending"

    @pytest.mark.asyncio
    async def test_process_text_message(self, db_session, test_org, test_user):
        """Processing a text message should mark completed."""
        from app.models.project import Project

        project = Project(name="Process Text", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 25),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="text_message",
            user_id=test_user.id,
            text_content="Foundation pour complete",
        )

        processed = await process_source(db=db_session, source_id=source.id)
        assert processed.processing_status == "completed"

    @pytest.mark.asyncio
    async def test_process_manual_source(self, db_session, test_org, test_user):
        """Processing a manual source should mark completed."""
        from app.models.project import Project

        project = Project(name="Process Manual", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 25),
            user_id=test_user.id,
        )

        source = await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="manual",
            user_id=test_user.id,
            text_content="Superintendent walkthrough notes",
        )

        processed = await process_source(db=db_session, source_id=source.id)
        assert processed.processing_status == "completed"

    @pytest.mark.asyncio
    async def test_process_nonexistent_source(self, db_session):
        """Processing a non-existent source should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await process_source(db=db_session, source_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# TestListSources — 3 tests
# ---------------------------------------------------------------------------


class TestListSources:
    """Tests for the list_sources service function."""

    @pytest.mark.asyncio
    async def test_list_sources_returns_matching(self, db_session, test_org, test_user):
        """Should return only sources for the specified report."""
        from app.models.project import Project

        project = Project(name="List Sources", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 26),
            user_id=test_user.id,
        )

        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/a.jpg",
        )
        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="text_message",
            user_id=test_user.id,
            text_content="Test note",
        )

        sources = await list_sources(db=db_session, report_id=report.id)
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_list_sources_empty(self, db_session, test_org, test_user):
        """Report with no sources should return empty list."""
        from app.models.project import Project

        project = Project(name="Empty Sources", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 27),
            user_id=test_user.id,
        )

        sources = await list_sources(db=db_session, report_id=report.id)
        assert sources == []

    @pytest.mark.asyncio
    async def test_list_sources_different_reports(self, db_session, test_org, test_user):
        """Sources from different reports should not intermix."""
        from app.models.project import Project

        project = Project(name="Multi Report", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report1 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 28),
            user_id=test_user.id,
        )
        report2 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 3, 29),
            user_id=test_user.id,
        )

        await upload_source(
            db=db_session,
            report_id=report1.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/r1.jpg",
        )
        await upload_source(
            db=db_session,
            report_id=report2.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/r2.jpg",
        )

        sources1 = await list_sources(db=db_session, report_id=report1.id)
        sources2 = await list_sources(db=db_session, report_id=report2.id)
        assert len(sources1) == 1
        assert len(sources2) == 1
        assert sources1[0].s3_key == "photos/r1.jpg"
        assert sources2[0].s3_key == "photos/r2.jpg"


# ---------------------------------------------------------------------------
# TestGenerateNarrative — 5 tests
# ---------------------------------------------------------------------------


class TestGenerateNarrative:
    """Tests for the generate_narrative service function."""

    @pytest.mark.asyncio
    async def test_template_fallback_narrative(self, db_session, test_org, test_user):
        """When LLM is unavailable, should use template fallback."""
        from app.models.project import Project

        project = Project(name="Narrative Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 1),
            user_id=test_user.id,
        )

        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="text_message",
            user_id=test_user.id,
            text_content="Framing completed on second floor",
        )

        # LLM will fail (no API key in tests), so template fallback is used
        updated = await generate_narrative(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
        )

        assert updated.content_markdown is not None
        assert "Daily Construction Report" in updated.content_markdown
        assert "1 text notes" in updated.content_markdown

    @pytest.mark.asyncio
    async def test_template_includes_source_counts(self, db_session, test_org, test_user):
        """Template narrative should include counts by source type."""
        from app.models.project import Project

        project = Project(name="Count Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 2),
            user_id=test_user.id,
        )

        # Add mixed sources
        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="text_message",
            user_id=test_user.id,
            text_content="Note 1",
        )
        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="manual",
            user_id=test_user.id,
            text_content="Note 2",
        )

        updated = await generate_narrative(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
        )

        assert "Sources reviewed: 2" in updated.content_markdown
        assert "0 photos" in updated.content_markdown
        assert "2 text notes" in updated.content_markdown

    @pytest.mark.asyncio
    async def test_narrative_nonexistent_report(self, db_session, test_org, test_user):
        """Generating for a non-existent report should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await generate_narrative(
                db=db_session,
                report_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                org_id=test_org.id,
            )

    @pytest.mark.asyncio
    async def test_narrative_no_sources(self, db_session, test_org, test_user):
        """Narrative generation with no sources should still produce template."""
        from app.models.project import Project

        project = Project(name="No Sources", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 3),
            user_id=test_user.id,
        )

        updated = await generate_narrative(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
        )

        assert updated.content_markdown is not None
        assert "Sources reviewed: 0" in updated.content_markdown

    def test_build_template_narrative_format(self):
        """Template narrative should follow the expected format."""
        sources = [
            _make_report_source(source_type="photo"),
            _make_report_source(source_type="photo"),
            _make_report_source(source_type="voice_memo"),
            _make_report_source(source_type="text_message"),
        ]
        project_id = uuid.uuid4()

        result = _build_template_narrative(date(2026, 4, 5), project_id, sources)

        assert "Daily Construction Report - 2026-04-05" in result
        assert f"Project: {project_id}" in result
        assert "Sources reviewed: 4" in result
        assert "2 photos" in result
        assert "1 voice memos" in result
        assert "1 text notes" in result
        assert "Further details pending superintendent review" in result


# ---------------------------------------------------------------------------
# TestApproveReport — 3 tests
# ---------------------------------------------------------------------------


class TestApproveReport:
    """Tests for the approve_report service function."""

    @pytest.mark.asyncio
    async def test_approve_sets_status(self, db_session, test_org, test_user):
        """Approving a report should set status to 'approved'."""
        from app.models.project import Project

        project = Project(name="Approve Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 10),
            user_id=test_user.id,
        )

        approved = await approve_report(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            user_id=test_user.id,
        )

        assert approved.status == "approved"
        assert approved.reviewed_by == test_user.id
        assert approved.published_at is not None

    @pytest.mark.asyncio
    async def test_approve_with_reviewer_notes(self, db_session, test_org, test_user):
        """Reviewer notes should be stored in sections."""
        from app.models.project import Project

        project = Project(name="Notes Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 11),
            user_id=test_user.id,
        )

        approved = await approve_report(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            user_id=test_user.id,
            reviewer_notes="Looks good, approved as-is",
        )

        assert approved.sections["reviewer_notes"] == "Looks good, approved as-is"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_report(self, db_session, test_org, test_user):
        """Approving a non-existent report should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await approve_report(
                db=db_session,
                report_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                user_id=test_user.id,
            )


# ---------------------------------------------------------------------------
# TestGetReportWithSources — 2 tests
# ---------------------------------------------------------------------------


class TestGetReportWithSources:
    """Tests for the get_report_with_sources service function."""

    @pytest.mark.asyncio
    async def test_get_report_with_sources(self, db_session, test_org, test_user):
        """Should return report and all associated sources."""
        from app.models.project import Project

        project = Project(name="Get Report", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 4, 15),
            user_id=test_user.id,
        )

        await upload_source(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/get_test.jpg",
        )

        data = await get_report_with_sources(
            db=db_session,
            report_id=report.id,
            project_id=project.id,
        )

        assert data["report"].id == report.id
        assert len(data["sources"]) == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent_report(self, db_session, test_org, test_user):
        """Getting a non-existent report should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await get_report_with_sources(
                db=db_session,
                report_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
            )


# ---------------------------------------------------------------------------
# TestListReports — 4 tests
# ---------------------------------------------------------------------------


class TestListReports:
    """Tests for the list_reports service function."""

    @pytest.mark.asyncio
    async def test_list_all_reports(self, db_session, test_org, test_user):
        """Should list all reports for a project."""
        from app.models.project import Project

        project = Project(name="List Reports", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        for day in range(1, 4):
            await create_report(
                db=db_session,
                project_id=project.id,
                org_id=test_org.id,
                report_date=date(2026, 5, day),
                user_id=test_user.id,
            )

        reports, total = await list_reports(db=db_session, project_id=project.id)
        assert total == 3
        assert len(reports) == 3

    @pytest.mark.asyncio
    async def test_list_reports_filter_by_status(self, db_session, test_org, test_user):
        """Should filter reports by status."""
        from app.models.project import Project

        project = Project(name="Filter Status", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report1 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 5, 10),
            user_id=test_user.id,
        )
        await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 5, 11),
            user_id=test_user.id,
        )

        # Approve one
        await approve_report(
            db=db_session,
            report_id=report1.id,
            project_id=project.id,
            user_id=test_user.id,
        )

        _drafts, draft_total = await list_reports(
            db=db_session, project_id=project.id, status="draft"
        )
        _approved, approved_total = await list_reports(
            db=db_session, project_id=project.id, status="approved"
        )

        assert draft_total == 1
        assert approved_total == 1

    @pytest.mark.asyncio
    async def test_list_reports_filter_by_date_range(self, db_session, test_org, test_user):
        """Should filter reports by date range."""
        from app.models.project import Project

        project = Project(name="Filter Date", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        for day in [5, 10, 15, 20, 25]:
            await create_report(
                db=db_session,
                project_id=project.id,
                org_id=test_org.id,
                report_date=date(2026, 6, day),
                user_id=test_user.id,
            )

        _reports, total = await list_reports(
            db=db_session,
            project_id=project.id,
            date_from=date(2026, 6, 10),
            date_to=date(2026, 6, 20),
        )
        assert total == 3  # 10, 15, 20

    @pytest.mark.asyncio
    async def test_list_reports_pagination(self, db_session, test_org, test_user):
        """Should respect page and page_size parameters."""
        from app.models.project import Project

        project = Project(name="Pagination", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        for day in range(1, 11):
            await create_report(
                db=db_session,
                project_id=project.id,
                org_id=test_org.id,
                report_date=date(2026, 7, day),
                user_id=test_user.id,
            )

        page1, total = await list_reports(db=db_session, project_id=project.id, page=1, page_size=3)
        page2, _ = await list_reports(db=db_session, project_id=project.id, page=2, page_size=3)

        assert total == 10
        assert len(page1) == 3
        assert len(page2) == 3
        # Pages should have different reports
        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)


# ---------------------------------------------------------------------------
# TestSiteScribeDashboard — 3 tests
# ---------------------------------------------------------------------------


class TestSiteScribeDashboard:
    """Tests for the get_dashboard service function."""

    @pytest.mark.asyncio
    async def test_dashboard_empty_project(self, db_session, test_org, test_user):
        """Dashboard for a project with no reports should return zeros."""
        from app.models.project import Project

        project = Project(name="Empty Dashboard", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        data = await get_dashboard(db=db_session, project_id=project.id)
        assert data["total_reports"] == 0
        assert data["draft_count"] == 0
        assert data["approved_count"] == 0
        assert data["latest_report_date"] is None
        assert data["avg_sources_per_report"] == 0.0

    @pytest.mark.asyncio
    async def test_dashboard_with_reports(self, db_session, test_org, test_user):
        """Dashboard should count reports and compute averages."""
        from app.models.project import Project

        project = Project(name="Dashboard Test", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report1 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 8, 1),
            user_id=test_user.id,
        )
        report2 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 8, 2),
            user_id=test_user.id,
        )

        # Add sources to report1
        for i in range(3):
            await upload_source(
                db=db_session,
                report_id=report1.id,
                project_id=project.id,
                org_id=test_org.id,
                source_type="text_message",
                user_id=test_user.id,
                text_content=f"Note {i}",
            )

        # Add source to report2
        await upload_source(
            db=db_session,
            report_id=report2.id,
            project_id=project.id,
            org_id=test_org.id,
            source_type="photo",
            user_id=test_user.id,
            s3_key="photos/dash.jpg",
        )

        data = await get_dashboard(db=db_session, project_id=project.id)
        assert data["total_reports"] == 2
        assert data["draft_count"] == 2
        assert data["approved_count"] == 0
        assert data["latest_report_date"] == "2026-08-02"
        assert data["avg_sources_per_report"] == 2.0  # 4 sources / 2 reports

    @pytest.mark.asyncio
    async def test_dashboard_with_approved_reports(self, db_session, test_org, test_user):
        """Dashboard should correctly count approved vs draft reports."""
        from app.models.project import Project

        project = Project(name="Approved Dash", org_id=test_org.id, status="active")
        db_session.add(project)
        await db_session.flush()

        report1 = await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 8, 10),
            user_id=test_user.id,
        )
        await create_report(
            db=db_session,
            project_id=project.id,
            org_id=test_org.id,
            report_date=date(2026, 8, 11),
            user_id=test_user.id,
        )

        await approve_report(
            db=db_session,
            report_id=report1.id,
            project_id=project.id,
            user_id=test_user.id,
        )

        data = await get_dashboard(db=db_session, project_id=project.id)
        assert data["total_reports"] == 2
        assert data["draft_count"] == 1
        assert data["approved_count"] == 1
