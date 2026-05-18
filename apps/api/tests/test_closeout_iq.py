"""Tests for CloseoutIQ — spec-driven closeout tracking and warranty management.

Test categories:
1. Model creation and defaults
2. Service functions (generate, list, update, review, dashboard, warranty)
3. Magic link integration
4. API endpoint tests
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.closeout import (
    CloseoutCommunication,
    CloseoutRequirement,
    WarrantyRecord,
)
from app.models.organization import Organization
from app.models.project import Project
from app.models.user import User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_project(db_session: AsyncSession, test_org: Organization) -> Project:
    project = Project(
        org_id=test_org.id,
        name="Closeout Test Project",
        status="construction",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 12, 31),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def sample_requirement(
    db_session: AsyncSession,
    test_project: Project,
    test_org: Organization,
) -> CloseoutRequirement:
    req = CloseoutRequirement(
        project_id=test_project.id,
        organization_id=test_org.id,
        csi_division="07",
        section_title="Thermal & Moisture Protection",
        requirement_type="warranty",
        description="Roofing manufacturer warranty (20 year)",
        spec_reference="Section 07 52 00",
        status="not_started",
    )
    db_session.add(req)
    await db_session.flush()
    await db_session.refresh(req)
    return req


@pytest_asyncio.fixture
async def submitted_requirement(
    db_session: AsyncSession,
    test_project: Project,
    test_org: Organization,
) -> CloseoutRequirement:
    req = CloseoutRequirement(
        project_id=test_project.id,
        organization_id=test_org.id,
        csi_division="23",
        section_title="HVAC",
        requirement_type="warranty",
        description="HVAC equipment manufacturer warranty",
        status="submitted",
        submitted_doc_s3_key="closeout/test/warranty.pdf",
        submitted_doc_name="warranty.pdf",
        responsible_sub_name="ABC Mechanical",
        responsible_sub_email="abc@example.com",
    )
    db_session.add(req)
    await db_session.flush()
    await db_session.refresh(req)
    return req


@pytest_asyncio.fixture
async def sample_warranty(
    db_session: AsyncSession,
    test_project: Project,
    test_org: Organization,
) -> WarrantyRecord:
    warranty = WarrantyRecord(
        project_id=test_project.id,
        organization_id=test_org.id,
        warrantor="ABC Mechanical",
        system_description="HVAC System",
        coverage_description="Full parts and labor",
        warranty_years=2,
        start_date=date(2025, 6, 1),
        end_date=date(2027, 6, 1),
        status="active",
    )
    db_session.add(warranty)
    await db_session.flush()
    await db_session.refresh(warranty)
    return warranty


# ═══════════════════════════════════════════════════════════════════════════
# 1. Model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCloseoutRequirementModel:
    """Test CloseoutRequirement model creation and defaults."""

    async def test_create_requirement(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        req = CloseoutRequirement(
            project_id=test_project.id,
            organization_id=test_org.id,
            requirement_type="warranty",
            description="Test warranty",
        )
        db_session.add(req)
        await db_session.flush()
        await db_session.refresh(req)

        assert req.id is not None
        assert req.status == "not_started"
        assert req.due_milestone == "substantial_completion"
        assert req.pay_app_linkage is False
        assert req.validation_flags == []

    async def test_default_status(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        req = CloseoutRequirement(
            project_id=test_project.id,
            organization_id=test_org.id,
            requirement_type="om_manual",
        )
        db_session.add(req)
        await db_session.flush()
        await db_session.refresh(req)
        assert req.status == "not_started"

    async def test_nullable_fields(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        req = CloseoutRequirement(
            project_id=test_project.id,
            organization_id=test_org.id,
            requirement_type="test_report",
        )
        db_session.add(req)
        await db_session.flush()
        await db_session.refresh(req)

        assert req.csi_division is None
        assert req.section_title is None
        assert req.description is None
        assert req.responsible_sub_id is None
        assert req.due_date is None
        assert req.submitted_doc_s3_key is None
        assert req.reviewer_id is None

    async def test_csi_division_stored(self, sample_requirement: CloseoutRequirement):
        assert sample_requirement.csi_division == "07"
        assert sample_requirement.section_title == "Thermal & Moisture Protection"

    async def test_timestamps(self, sample_requirement: CloseoutRequirement):
        assert sample_requirement.created_at is not None
        assert sample_requirement.updated_at is not None


class TestWarrantyRecordModel:
    """Test WarrantyRecord model creation and status tracking."""

    async def test_create_warranty(self, sample_warranty: WarrantyRecord):
        assert sample_warranty.id is not None
        assert sample_warranty.warrantor == "ABC Mechanical"
        assert sample_warranty.warranty_years == 2
        assert sample_warranty.status == "active"

    async def test_default_warranty_years(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Test Warrantor",
        )
        db_session.add(warranty)
        await db_session.flush()
        await db_session.refresh(warranty)
        assert warranty.warranty_years == 1

    async def test_default_status(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Test",
        )
        db_session.add(warranty)
        await db_session.flush()
        await db_session.refresh(warranty)
        assert warranty.status == "active"

    async def test_warranty_dates(self, sample_warranty: WarrantyRecord):
        assert sample_warranty.start_date == date(2025, 6, 1)
        assert sample_warranty.end_date == date(2027, 6, 1)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Generate requirements
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateRequirements:
    """Test generate_requirements with LLM mock and fallback."""

    async def test_fallback_generates_default_requirements(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        """When no document chunks exist, fallback generates defaults."""
        from app.services.products.closeout_iq.service import generate_requirements

        fake_doc_id = uuid.uuid4()
        reqs = await generate_requirements(db_session, test_project.id, test_org.id, fake_doc_id)

        assert len(reqs) > 0
        # Should have multiple CSI divisions represented
        divisions = {r.csi_division for r in reqs}
        assert "07" in divisions  # Roofing
        assert "23" in divisions  # HVAC
        assert "26" in divisions  # Electrical

    async def test_fallback_requirement_types(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        """Fallback requirements include multiple types."""
        from app.services.products.closeout_iq.service import generate_requirements

        fake_doc_id = uuid.uuid4()
        reqs = await generate_requirements(db_session, test_project.id, test_org.id, fake_doc_id)

        types = {r.requirement_type for r in reqs}
        assert "warranty" in types
        assert "om_manual" in types
        assert "test_report" in types

    async def test_all_requirements_have_correct_project(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import generate_requirements

        fake_doc_id = uuid.uuid4()
        reqs = await generate_requirements(db_session, test_project.id, test_org.id, fake_doc_id)

        for req in reqs:
            assert req.project_id == test_project.id
            assert req.organization_id == test_org.id
            assert req.status == "not_started"

    @patch(
        "app.services.products.closeout_iq.service._extract_requirements_via_llm",
        new_callable=AsyncMock,
    )
    async def test_llm_extraction_used_when_chunks_available(
        self,
        mock_llm,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        """When LLM returns results, those are used instead of fallback."""
        from app.models.document import Document, DocumentChunk
        from app.services.products.closeout_iq.service import generate_requirements

        # Create a fake document with chunks
        doc = Document(
            project_id=test_project.id,
            type="specification",
            title="Project Spec",
            original_filename="spec.pdf",
            s3_key="specs/spec.pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        await db_session.refresh(doc)

        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            content="SECTION 07 52 00 - MODIFIED BITUMINOUS MEMBRANE ROOFING\nPART 1 - GENERAL",
            chunk_type="text",
        )
        db_session.add(chunk)
        await db_session.flush()

        mock_llm.return_value = [
            {
                "csi_division": "07",
                "section_title": "Modified Bituminous Roofing",
                "requirement_type": "warranty",
                "description": "20-year roofing warranty",
                "spec_reference": "07 52 00",
            },
        ]

        reqs = await generate_requirements(db_session, test_project.id, test_org.id, doc.id)

        mock_llm.assert_called_once()
        assert len(reqs) == 1
        assert reqs[0].csi_division == "07"
        assert reqs[0].description == "20-year roofing warranty"

    @patch(
        "app.services.products.closeout_iq.service._extract_requirements_via_llm",
        new_callable=AsyncMock,
    )
    async def test_llm_failure_falls_back(
        self,
        mock_llm,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        """When LLM returns empty list, fallback is used."""
        from app.models.document import Document, DocumentChunk
        from app.services.products.closeout_iq.service import generate_requirements

        doc = Document(
            project_id=test_project.id,
            type="specification",
            title="Project Spec",
            original_filename="spec.pdf",
            s3_key="specs/spec.pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        await db_session.refresh(doc)

        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            content="Some spec content",
            chunk_type="text",
        )
        db_session.add(chunk)
        await db_session.flush()

        mock_llm.return_value = []

        reqs = await generate_requirements(db_session, test_project.id, test_org.id, doc.id)

        # Should fall back to defaults
        assert len(reqs) > 10

    async def test_requirements_persisted_to_db(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import generate_requirements

        fake_doc_id = uuid.uuid4()
        await generate_requirements(db_session, test_project.id, test_org.id, fake_doc_id)

        result = await db_session.execute(
            select(CloseoutRequirement).where(CloseoutRequirement.project_id == test_project.id)
        )
        db_reqs = list(result.scalars().all())
        assert len(db_reqs) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. List requirements
# ═══════════════════════════════════════════════════════════════════════════


class TestListRequirements:
    """Test filtered and paginated requirement listing."""

    async def _seed_requirements(
        self,
        db_session: AsyncSession,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        count: int = 10,
    ) -> list[CloseoutRequirement]:
        reqs = []
        divisions = ["03", "07", "23", "26"]
        statuses = ["not_started", "submitted", "accepted", "rejected"]
        sub_id = uuid.uuid4()
        for i in range(count):
            req = CloseoutRequirement(
                project_id=project_id,
                organization_id=org_id,
                csi_division=divisions[i % len(divisions)],
                section_title=f"Section {i}",
                requirement_type="warranty" if i % 2 == 0 else "om_manual",
                status=statuses[i % len(statuses)],
                responsible_sub_id=sub_id if i < 5 else None,
                due_date=date.today() - timedelta(days=10) if i < 3 else None,
            )
            db_session.add(req)
            reqs.append(req)
        await db_session.flush()
        for r in reqs:
            await db_session.refresh(r)
        return reqs

    async def test_list_all(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 10)
        items, total = await list_requirements(db_session, test_project.id)
        assert total == 10
        assert len(items) == 10

    async def test_filter_by_status(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 12)
        items, _total = await list_requirements(db_session, test_project.id, status="submitted")
        for item in items:
            assert item.status == "submitted"

    async def test_filter_by_division(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 12)
        items, _total = await list_requirements(db_session, test_project.id, csi_division="07")
        for item in items:
            assert item.csi_division == "07"

    async def test_filter_by_sub_id(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        reqs = await self._seed_requirements(db_session, test_project.id, test_org.id, 10)
        sub_id = reqs[0].responsible_sub_id
        items, total = await list_requirements(
            db_session, test_project.id, responsible_sub_id=sub_id
        )
        assert total == 5
        for item in items:
            assert item.responsible_sub_id == sub_id

    async def test_overdue_only(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 12)
        items, _total = await list_requirements(db_session, test_project.id, overdue_only=True)
        today = date.today()
        for item in items:
            assert item.due_date is not None
            assert item.due_date < today
            assert item.status not in ("accepted", "waived")

    async def test_pagination_page_1(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 30)
        items, total = await list_requirements(db_session, test_project.id, page=1, page_size=10)
        assert total == 30
        assert len(items) == 10

    async def test_pagination_page_2(
        self, db_session: AsyncSession, test_project: Project, test_org: Organization
    ):
        from app.services.products.closeout_iq.service import list_requirements

        await self._seed_requirements(db_session, test_project.id, test_org.id, 30)
        items, total = await list_requirements(db_session, test_project.id, page=2, page_size=10)
        assert total == 30
        assert len(items) == 10

    async def test_empty_project(self, db_session: AsyncSession, test_project: Project):
        from app.services.products.closeout_iq.service import list_requirements

        items, total = await list_requirements(db_session, test_project.id)
        assert total == 0
        assert items == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. Update requirement
# ═══════════════════════════════════════════════════════════════════════════


class TestUpdateRequirement:
    """Test requirement field updates."""

    async def test_update_status(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import update_requirement

        updated = await update_requirement(
            db_session,
            sample_requirement.id,
            test_project.id,
            {"status": "requested"},
        )
        assert updated.status == "requested"

    async def test_update_due_date(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import update_requirement

        new_date = date(2026, 6, 15)
        updated = await update_requirement(
            db_session,
            sample_requirement.id,
            test_project.id,
            {"due_date": new_date},
        )
        assert updated.due_date == new_date

    async def test_update_sub_info(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import update_requirement

        updated = await update_requirement(
            db_session,
            sample_requirement.id,
            test_project.id,
            {
                "responsible_sub_name": "XYZ Roofing",
                "responsible_sub_email": "xyz@example.com",
            },
        )
        assert updated.responsible_sub_name == "XYZ Roofing"
        assert updated.responsible_sub_email == "xyz@example.com"

    async def test_update_nonexistent_raises(
        self,
        db_session: AsyncSession,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import update_requirement

        with pytest.raises(ValueError, match="not found"):
            await update_requirement(
                db_session, uuid.uuid4(), test_project.id, {"status": "accepted"}
            )

    async def test_update_ignores_unknown_fields(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import update_requirement

        # Should not raise even with unknown fields
        updated = await update_requirement(
            db_session,
            sample_requirement.id,
            test_project.id,
            {"status": "requested", "nonexistent_field": "value"},
        )
        assert updated.status == "requested"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Send document request (magic link)
# ═══════════════════════════════════════════════════════════════════════════


class TestSendDocumentRequest:
    """Test magic link generation and communication logging."""

    async def test_send_request(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import send_document_request

        result = await send_document_request(
            db_session,
            project_id=test_project.id,
            requirement_id=sample_requirement.id,
            user_id=test_user.id,
            recipient_email="sub@example.com",
            recipient_name="John Sub",
        )

        assert "token_url" in result
        assert "communication_id" in result
        assert "token_id" in result

    async def test_updates_requirement_status_to_requested(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import send_document_request

        assert sample_requirement.status == "not_started"

        await send_document_request(
            db_session,
            project_id=test_project.id,
            requirement_id=sample_requirement.id,
            user_id=test_user.id,
            recipient_email="sub@example.com",
        )

        await db_session.refresh(sample_requirement)
        assert sample_requirement.status == "requested"

    async def test_stores_sub_email(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import send_document_request

        await send_document_request(
            db_session,
            project_id=test_project.id,
            requirement_id=sample_requirement.id,
            user_id=test_user.id,
            recipient_email="sub@example.com",
            recipient_name="John Sub",
        )

        await db_session.refresh(sample_requirement)
        assert sample_requirement.responsible_sub_email == "sub@example.com"
        assert sample_requirement.responsible_sub_name == "John Sub"

    async def test_communication_logged(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import send_document_request

        await send_document_request(
            db_session,
            project_id=test_project.id,
            requirement_id=sample_requirement.id,
            user_id=test_user.id,
            recipient_email="sub@example.com",
        )

        result = await db_session.execute(
            select(CloseoutCommunication).where(
                CloseoutCommunication.requirement_id == sample_requirement.id
            )
        )
        comms = list(result.scalars().all())
        assert len(comms) == 1
        assert comms[0].channel == "email"
        assert comms[0].sent_to == "sub@example.com"

    async def test_nonexistent_requirement_raises(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import send_document_request

        with pytest.raises(ValueError, match="not found"):
            await send_document_request(
                db_session,
                project_id=test_project.id,
                requirement_id=uuid.uuid4(),
                user_id=test_user.id,
                recipient_email="sub@example.com",
            )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Handle sub upload
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleSubUpload:
    """Test subcontractor document upload processing."""

    async def test_upload_updates_requirement(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_org: Organization,
    ):
        from app.models.magic_link import MagicLinkToken
        from app.services.products.closeout_iq.service import handle_sub_upload

        # Create a magic link token
        token = MagicLinkToken(
            token_hash="test_hash_upload_1",
            project_id=test_project.id,
            organization_id=test_org.id,
            purpose="closeout_upload",
            entity_id=sample_requirement.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(token)
        await db_session.flush()

        req = await handle_sub_upload(
            db_session,
            token_hash="test_hash_upload_1",
            s3_key="closeout/test/warranty.pdf",
            filename="warranty.pdf",
        )

        assert req.status == "submitted"
        assert req.submitted_doc_s3_key == "closeout/test/warranty.pdf"
        assert req.submitted_doc_name == "warranty.pdf"

    async def test_upload_sets_validation_flags(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_org: Organization,
    ):
        from app.models.magic_link import MagicLinkToken
        from app.services.products.closeout_iq.service import handle_sub_upload

        token = MagicLinkToken(
            token_hash="test_hash_upload_2",
            project_id=test_project.id,
            organization_id=test_org.id,
            purpose="closeout_upload",
            entity_id=sample_requirement.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(token)
        await db_session.flush()

        req = await handle_sub_upload(
            db_session,
            token_hash="test_hash_upload_2",
            s3_key="closeout/test/photo.jpg",
            filename="photo.jpg",
        )

        # Should have validation flags for non-PDF warranty
        assert isinstance(req.validation_flags, list)

    async def test_invalid_token_raises(self, db_session: AsyncSession):
        from app.services.products.closeout_iq.service import handle_sub_upload

        with pytest.raises(ValueError, match="Invalid upload token"):
            await handle_sub_upload(
                db_session,
                token_hash="nonexistent_hash",
                s3_key="test.pdf",
                filename="test.pdf",
            )

    async def test_token_without_entity_raises(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.models.magic_link import MagicLinkToken
        from app.services.products.closeout_iq.service import handle_sub_upload

        token = MagicLinkToken(
            token_hash="test_hash_no_entity",
            project_id=test_project.id,
            organization_id=test_org.id,
            purpose="closeout_upload",
            entity_id=None,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(token)
        await db_session.flush()

        with pytest.raises(ValueError, match="not linked"):
            await handle_sub_upload(
                db_session,
                token_hash="test_hash_no_entity",
                s3_key="test.pdf",
                filename="test.pdf",
            )

    async def test_upload_pdf_no_flags(
        self,
        db_session: AsyncSession,
        sample_requirement: CloseoutRequirement,
        test_project: Project,
        test_org: Organization,
    ):
        from app.models.magic_link import MagicLinkToken
        from app.services.products.closeout_iq.service import handle_sub_upload

        token = MagicLinkToken(
            token_hash="test_hash_upload_pdf",
            project_id=test_project.id,
            organization_id=test_org.id,
            purpose="closeout_upload",
            entity_id=sample_requirement.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(token)
        await db_session.flush()

        req = await handle_sub_upload(
            db_session,
            token_hash="test_hash_upload_pdf",
            s3_key="closeout/test/warranty.pdf",
            filename="warranty.pdf",
        )

        # PDF for warranty should not trigger basic validation warnings
        basic_flags = [
            f
            for f in req.validation_flags
            if f.get("flag") in ("unusual_file_type", "expected_pdf_for_warranty")
        ]
        assert len(basic_flags) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Review document
# ═══════════════════════════════════════════════════════════════════════════


class TestReviewDocument:
    """Test document accept/reject flow."""

    async def test_accept_sets_status(
        self,
        db_session: AsyncSession,
        submitted_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import review_document

        req = await review_document(
            db_session,
            submitted_requirement.id,
            test_project.id,
            accepted=True,
            reviewer_id=test_user.id,
        )
        assert req.status == "accepted"
        assert req.reviewer_id == test_user.id
        assert req.reviewed_at is not None

    async def test_accept_warranty_creates_record(
        self,
        db_session: AsyncSession,
        submitted_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import review_document

        await review_document(
            db_session,
            submitted_requirement.id,
            test_project.id,
            accepted=True,
            reviewer_id=test_user.id,
        )

        result = await db_session.execute(
            select(WarrantyRecord).where(
                WarrantyRecord.closeout_requirement_id == submitted_requirement.id
            )
        )
        warranty = result.scalars().first()
        assert warranty is not None
        assert warranty.warrantor == "ABC Mechanical"
        assert warranty.status == "active"
        assert warranty.warranty_letter_s3_key == "closeout/test/warranty.pdf"

    async def test_reject_sets_status_and_notes(
        self,
        db_session: AsyncSession,
        submitted_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import review_document

        req = await review_document(
            db_session,
            submitted_requirement.id,
            test_project.id,
            accepted=False,
            reviewer_id=test_user.id,
            notes="Missing signature on page 2",
        )
        assert req.status == "rejected"
        assert req.rejection_notes == "Missing signature on page 2"

    async def test_reject_clears_notes_on_subsequent_accept(
        self,
        db_session: AsyncSession,
        submitted_requirement: CloseoutRequirement,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import review_document

        # First reject
        await review_document(
            db_session,
            submitted_requirement.id,
            test_project.id,
            accepted=False,
            reviewer_id=test_user.id,
            notes="Bad doc",
        )

        # Then accept
        req = await review_document(
            db_session,
            submitted_requirement.id,
            test_project.id,
            accepted=True,
            reviewer_id=test_user.id,
        )
        assert req.status == "accepted"
        assert req.rejection_notes is None

    async def test_nonexistent_requirement_raises(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import review_document

        with pytest.raises(ValueError, match="not found"):
            await review_document(
                db_session,
                uuid.uuid4(),
                test_project.id,
                accepted=True,
                reviewer_id=test_user.id,
            )

    async def test_accept_non_warranty_type_no_warranty_created(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
        test_user: User,
    ):
        """Accepting a non-warranty requirement should NOT create a WarrantyRecord."""
        from app.services.products.closeout_iq.service import review_document

        req = CloseoutRequirement(
            project_id=test_project.id,
            organization_id=test_org.id,
            requirement_type="om_manual",
            status="submitted",
            submitted_doc_s3_key="closeout/manual.pdf",
            submitted_doc_name="manual.pdf",
        )
        db_session.add(req)
        await db_session.flush()
        await db_session.refresh(req)

        await review_document(
            db_session,
            req.id,
            test_project.id,
            accepted=True,
            reviewer_id=test_user.id,
        )

        result = await db_session.execute(
            select(WarrantyRecord).where(WarrantyRecord.closeout_requirement_id == req.id)
        )
        assert result.scalars().first() is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. Dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestCloseoutDashboard:
    """Test dashboard aggregation and analytics."""

    async def _seed_mixed_requirements(
        self,
        db_session: AsyncSession,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ):
        items = [
            ("07", "accepted"),
            ("07", "accepted"),
            ("07", "not_started"),
            ("23", "submitted"),
            ("23", "accepted"),
            ("26", "not_started"),
            ("26", "rejected"),
            ("26", "waived"),
        ]
        reqs = []
        for div, st in items:
            req = CloseoutRequirement(
                project_id=project_id,
                organization_id=org_id,
                csi_division=div,
                requirement_type="warranty",
                status=st,
                due_date=date.today() - timedelta(days=5) if st == "not_started" else None,
                reviewed_at=datetime.now(UTC) - timedelta(days=3)
                if st in ("accepted", "waived")
                else None,
            )
            db_session.add(req)
            reqs.append(req)
        await db_session.flush()
        return reqs

    async def test_total_and_completed(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import get_dashboard

        await self._seed_mixed_requirements(db_session, test_project.id, test_org.id)
        dashboard = await get_dashboard(db_session, test_project.id)

        assert dashboard["total_items"] == 8
        # accepted(3) + waived(1) = 4
        assert dashboard["completed_items"] == 4
        assert dashboard["overall_pct"] == 50.0

    async def test_overdue_count(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import get_dashboard

        await self._seed_mixed_requirements(db_session, test_project.id, test_org.id)
        dashboard = await get_dashboard(db_session, test_project.id)

        # 2 not_started items have due_date in the past
        assert dashboard["overdue_count"] == 2

    async def test_progress_by_division(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import get_dashboard

        await self._seed_mixed_requirements(db_session, test_project.id, test_org.id)
        dashboard = await get_dashboard(db_session, test_project.id)

        div_07 = next(d for d in dashboard["progress_by_division"] if d["csi_division"] == "07")
        assert div_07["total"] == 3
        assert div_07["completed"] == 2

    async def test_projected_completion(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import get_dashboard

        await self._seed_mixed_requirements(db_session, test_project.id, test_org.id)
        dashboard = await get_dashboard(db_session, test_project.id)

        # Should have a projected completion date since some items are completed
        assert dashboard["projected_completion_date"] is not None

    async def test_empty_project_dashboard(
        self,
        db_session: AsyncSession,
        test_project: Project,
    ):
        from app.services.products.closeout_iq.service import get_dashboard

        dashboard = await get_dashboard(db_session, test_project.id)
        assert dashboard["total_items"] == 0
        assert dashboard["completed_items"] == 0
        assert dashboard["overall_pct"] == 0.0
        assert dashboard["overdue_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 9. Warranty check
# ═══════════════════════════════════════════════════════════════════════════


class TestWarrantyCheck:
    """Test warranty expiration detection."""

    async def test_expiring_soon_detected(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import warranty_check

        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Test",
            warranty_years=1,
            start_date=date.today() - timedelta(days=330),
            end_date=date.today() + timedelta(days=35),
            status="active",
        )
        db_session.add(warranty)
        await db_session.flush()

        result = await warranty_check(db_session, test_project.id)
        assert len(result["expiring_soon"]) == 1
        assert result["expiring_soon"][0].id == warranty.id

    async def test_expired_detected(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import warranty_check

        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Test Expired",
            warranty_years=1,
            start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=35),
            status="active",
        )
        db_session.add(warranty)
        await db_session.flush()

        result = await warranty_check(db_session, test_project.id)
        assert len(result["expired"]) == 1
        assert result["expired"][0].status == "expired"

    async def test_active_not_expiring_not_returned(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import warranty_check

        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Test Active",
            warranty_years=2,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=730),
            status="active",
        )
        db_session.add(warranty)
        await db_session.flush()

        result = await warranty_check(db_session, test_project.id)
        assert len(result["expiring_soon"]) == 0
        assert len(result["expired"]) == 0

    async def test_claimed_warranty_not_returned_as_expired(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_org: Organization,
    ):
        from app.services.products.closeout_iq.service import warranty_check

        warranty = WarrantyRecord(
            project_id=test_project.id,
            organization_id=test_org.id,
            warrantor="Claimed",
            warranty_years=1,
            start_date=date.today() - timedelta(days=400),
            end_date=date.today() - timedelta(days=35),
            status="claimed",
        )
        db_session.add(warranty)
        await db_session.flush()

        result = await warranty_check(db_session, test_project.id)
        # claimed status is not in ("active", "expiring_soon"), so should not appear
        assert len(result["expired"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. File warranty claim
# ═══════════════════════════════════════════════════════════════════════════


class TestFileWarrantyClaim:
    """Test warranty claim creation."""

    async def test_create_claim(
        self,
        db_session: AsyncSession,
        sample_warranty: WarrantyRecord,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import file_warranty_claim

        claim = await file_warranty_claim(
            db_session,
            warranty_id=sample_warranty.id,
            issue_description="Roof leak at northwest corner",
            photos=["photo1.jpg", "photo2.jpg"],
            reporter_id=test_user.id,
        )

        assert claim.id is not None
        assert claim.warranty_id == sample_warranty.id
        assert claim.issue_description == "Roof leak at northwest corner"
        assert claim.photos == ["photo1.jpg", "photo2.jpg"]
        assert claim.resolution_status == "reported"

    async def test_claim_updates_warranty_status(
        self,
        db_session: AsyncSession,
        sample_warranty: WarrantyRecord,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import file_warranty_claim

        await file_warranty_claim(
            db_session,
            warranty_id=sample_warranty.id,
            issue_description="System failure",
            photos=[],
            reporter_id=test_user.id,
        )

        await db_session.refresh(sample_warranty)
        assert sample_warranty.status == "claimed"

    async def test_claim_nonexistent_warranty_raises(
        self,
        db_session: AsyncSession,
        test_user: User,
    ):
        from app.services.products.closeout_iq.service import file_warranty_claim

        with pytest.raises(ValueError, match="not found"):
            await file_warranty_claim(
                db_session,
                warranty_id=uuid.uuid4(),
                issue_description="Test",
                photos=[],
                reporter_id=test_user.id,
            )

    async def test_claim_without_reporter(
        self,
        db_session: AsyncSession,
        sample_warranty: WarrantyRecord,
    ):
        from app.services.products.closeout_iq.service import file_warranty_claim

        claim = await file_warranty_claim(
            db_session,
            warranty_id=sample_warranty.id,
            issue_description="Anonymous report",
            photos=[],
            reporter_id=None,
        )
        assert claim.reported_by is None


# ═══════════════════════════════════════════════════════════════════════════
# 11. API endpoint tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCloseoutAPI:
    """HTTP endpoint tests using httpx AsyncClient."""

    @pytest_asyncio.fixture
    async def api_project(
        self, db_session: AsyncSession, test_org: Organization, test_user: User
    ) -> Project:
        from app.models.project import ProjectMember

        project = Project(
            org_id=test_org.id,
            name="API Test Project",
            status="construction",
            start_date=date(2025, 1, 1),
            end_date=date(2026, 12, 31),
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)

        # Add user as project member for access check
        member = ProjectMember(
            project_id=project.id,
            user_id=test_user.id,
            role="admin",
        )
        db_session.add(member)
        await db_session.flush()
        return project

    async def test_generate_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
    ):
        response = await client.post(
            f"/api/v1/projects/{api_project.id}/closeout/generate",
            json={"spec_document_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    async def test_list_requirements_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
        db_session: AsyncSession,
        test_org: Organization,
    ):
        # Seed a requirement
        req = CloseoutRequirement(
            project_id=api_project.id,
            organization_id=test_org.id,
            requirement_type="warranty",
            csi_division="07",
            status="not_started",
        )
        db_session.add(req)
        await db_session.flush()

        response = await client.get(
            f"/api/v1/projects/{api_project.id}/closeout/requirements",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert isinstance(data["data"], list)

    async def test_update_requirement_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
        db_session: AsyncSession,
        test_org: Organization,
    ):
        req = CloseoutRequirement(
            project_id=api_project.id,
            organization_id=test_org.id,
            requirement_type="warranty",
            status="not_started",
        )
        db_session.add(req)
        await db_session.flush()
        await db_session.refresh(req)

        response = await client.patch(
            f"/api/v1/projects/{api_project.id}/closeout/requirements/{req.id}",
            json={"status": "requested"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "requested"

    async def test_dashboard_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
    ):
        response = await client.get(
            f"/api/v1/projects/{api_project.id}/closeout/dashboard",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_items" in data
        assert "overall_pct" in data

    async def test_warranties_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
    ):
        response = await client.get(
            f"/api/v1/projects/{api_project.id}/closeout/warranties",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_warranty_check_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
    ):
        response = await client.get(
            f"/api/v1/projects/{api_project.id}/closeout/warranty-check",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "expiring_soon" in data
        assert "expired" in data

    async def test_review_endpoint_not_found(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
    ):
        response = await client.post(
            f"/api/v1/projects/{api_project.id}/closeout/requirements/{uuid.uuid4()}/review",
            json={"accepted": True},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_file_claim_endpoint(
        self,
        client,
        auth_headers: dict,
        api_project: Project,
        db_session: AsyncSession,
        test_org: Organization,
    ):
        warranty = WarrantyRecord(
            project_id=api_project.id,
            organization_id=test_org.id,
            warrantor="API Test Warrantor",
            warranty_years=2,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=730),
            status="active",
        )
        db_session.add(warranty)
        await db_session.flush()
        await db_session.refresh(warranty)

        response = await client.post(
            f"/api/v1/projects/{api_project.id}/closeout/warranties/{warranty.id}/claims",
            json={
                "issue_description": "Water damage on 3rd floor",
                "photos": ["damage.jpg"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["issue_description"] == "Water damage on 3rd floor"
        assert data["resolution_status"] == "reported"
