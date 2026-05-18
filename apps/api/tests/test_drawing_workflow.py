"""Tests for drawing management: sets, revisions, markups, links, bulk upload."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.models.communication import RFI, Submittal
from app.models.field_management import PunchListItem
from app.models.project import Project
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.communication.drawing_service import (
    infer_discipline,
    parse_sheet_number,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession, test_org):
    project = Project(
        name="Drawing Test Project",
        org_id=test_org.id,
        status="active",
        contract_value=Decimal("500000.00"),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture(scope="function")
async def test_rfi(db_session: AsyncSession, test_project, test_user):
    rfi = RFI(
        project_id=test_project.id,
        rfi_number="RFI-001",
        subject="Foundation detail",
        question="What is the foundation depth?",
        submitted_by=test_user.id,
    )
    db_session.add(rfi)
    await db_session.flush()
    await db_session.refresh(rfi)
    return rfi


@pytest_asyncio.fixture(scope="function")
async def test_submittal(db_session: AsyncSession, test_project, test_user):
    sub = Submittal(
        project_id=test_project.id,
        submittal_number="SUB-001",
        title="Concrete mix design",
        submitted_by=test_user.id,
    )
    db_session.add(sub)
    await db_session.flush()
    await db_session.refresh(sub)
    return sub


@pytest_asyncio.fixture(scope="function")
async def test_punch_item(db_session: AsyncSession, test_project, test_user):
    item = PunchListItem(
        project_id=test_project.id,
        item_number="PL-001",
        description="Touch up paint in lobby",
        status="open",
        priority="normal",
        created_by=test_user.id,
    )
    db_session.add(item)
    await db_session.flush()
    await db_session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Sheet Number Parsing (pure functions, no DB)
# ---------------------------------------------------------------------------


class TestSheetNumberParsing:
    def test_parse_standard_format(self):
        assert parse_sheet_number("A-101 Floor Plan.pdf") == "A-101"

    def test_parse_no_separator(self):
        assert parse_sheet_number("S200_Foundation.pdf") == "S-200"

    def test_parse_underscore_separator(self):
        assert parse_sheet_number("M_301 HVAC Plan.dxf") == "M-301"

    def test_parse_with_decimal(self):
        assert parse_sheet_number("E-1.1 Electrical.pdf") == "E-1.1"

    def test_parse_lowercase(self):
        assert parse_sheet_number("a-101.pdf") == "A-101"

    def test_parse_no_match_returns_none(self):
        assert parse_sheet_number("notes.pdf") is None

    def test_parse_numbers_only_returns_none(self):
        assert parse_sheet_number("12345.pdf") is None

    def test_infer_discipline_architectural(self):
        assert infer_discipline("A-101") == "architectural"

    def test_infer_discipline_structural(self):
        assert infer_discipline("S-200") == "structural"

    def test_infer_discipline_mechanical(self):
        assert infer_discipline("M-301") == "mechanical"

    def test_infer_discipline_electrical(self):
        assert infer_discipline("E-100") == "electrical"

    def test_infer_discipline_plumbing(self):
        assert infer_discipline("P-100") == "plumbing"

    def test_infer_discipline_civil(self):
        assert infer_discipline("C-100") == "civil"

    def test_infer_discipline_unknown(self):
        assert infer_discipline("X-100") == "general"

    def test_infer_discipline_empty(self):
        assert infer_discipline("") == "general"


# ---------------------------------------------------------------------------
# Drawing Set CRUD
# ---------------------------------------------------------------------------


class TestDrawingSetCRUD:
    @pytest.mark.asyncio
    async def test_create_drawing_set(self, client, auth_headers, test_project):
        response = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={
                "name": "Architectural Set",
                "discipline": "architectural",
                "description": "Main architectural drawings",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Architectural Set"
        assert data["discipline"] == "architectural"
        assert data["drawing_count"] == 0

    @pytest.mark.asyncio
    async def test_list_drawing_sets(self, client, auth_headers, test_project):
        # Create 2 sets
        for name in ["Arch Set", "Struct Set"]:
            await client.post(
                f"/api/v1/projects/{test_project.id}/drawing-sets",
                json={"name": name, "discipline": "architectural"},
                headers=auth_headers,
            )

        response = await client.get(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2

    @pytest.mark.asyncio
    async def test_unique_set_name_per_project(self, client, auth_headers, test_project):
        payload = {"name": "Arch Set", "discipline": "architectural"}
        await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json=payload,
            headers=auth_headers,
        )
        response = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json=payload,
            headers=auth_headers,
        )
        # Should fail due to unique constraint
        assert response.status_code in (409, 422, 500)

    @pytest.mark.asyncio
    async def test_delete_drawing_set(self, client, auth_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "To Delete", "discipline": "structural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_get_drawing_set_not_found(self, client, auth_headers, test_project):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_drawing_set(self, client, auth_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Old Name", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}",
            json={"name": "New Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"


# ---------------------------------------------------------------------------
# Bulk Upload
# ---------------------------------------------------------------------------


class TestBulkUpload:
    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_bulk_upload_creates_drawings(
        self, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        # Create set first
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Arch Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        # Upload files
        files = [
            ("files", ("A-101 Floor Plan.pdf", b"%PDF-fake-content", "application/pdf")),
            ("files", ("S-200 Foundation.pdf", b"%PDF-fake-content", "application/pdf")),
        ]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert len(data["uploaded"]) == 2

        # Verify sheet numbers parsed correctly
        sheet_numbers = {item["drawing"]["sheet_number"] for item in data["uploaded"]}
        assert "A-101" in sheet_numbers
        assert "S-200" in sheet_numbers

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_bulk_upload_invalid_extension_rejected(
        self, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        files = [
            ("files", ("notes.txt", b"text content", "text/plain")),
        ]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        # Endpoint rejects the whole request with 400 if any file has an
        # unsupported extension — the validate-then-reject contract.
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_bulk_upload_unparseable_filename(
        self, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        files = [
            ("files", ("meeting_notes.pdf", b"%PDF-fake", "application/pdf")),
        ]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["successful"] == 1
        assert len(data["uploaded"][0]["warnings"]) > 0

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_bulk_upload_duplicate_creates_new_revision(
        self, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        # Upload same sheet number twice
        for _ in range(2):
            files = [("files", ("A-101 Floor.pdf", b"%PDF-fake", "application/pdf"))]
            await client.post(
                f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
                files=files,
                headers=auth_headers,
            )

        # Second upload should have created revision 2
        # Get the drawing to verify
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}",
            headers=auth_headers,
        )
        data = resp.json()
        # Should have 1 drawing (not 2), with 2 revisions
        assert len(data["drawings"]) == 1


# ---------------------------------------------------------------------------
# Revision Management
# ---------------------------------------------------------------------------


class TestRevisionManagement:
    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    @patch("app.services.communication.drawing_service.generate_presigned_url")
    async def test_upload_revision_supersedes_previous(
        self, mock_url, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"
        mock_url.return_value = "https://minio.test/presigned"

        # Create set and upload first drawing
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]

        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        # Upload revision 2
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions",
            files={"file": ("A-101_rev2.pdf", b"%PDF-v2", "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["revision_number"] == 2
        assert resp.json()["status"] == "current"

        # List revisions and verify rev 1 is superseded
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions",
            headers=auth_headers,
        )
        revisions = resp.json()
        assert len(revisions) == 2
        rev_statuses = {r["revision_number"]: r["status"] for r in revisions}
        assert rev_statuses[1] == "superseded"
        assert rev_statuses[2] == "current"

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    @patch("app.services.communication.drawing_service.generate_presigned_url")
    async def test_get_revision_download_url(
        self, mock_url, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"
        mock_url.return_value = "https://minio.test/presigned-url"

        # Create set and upload
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_id = resp.json()["uploaded"][0]["revision"]["id"]

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/download",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "download_url" in resp.json()

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    @patch("app.services.communication.drawing_service.generate_presigned_url")
    async def test_compare_revisions(
        self, mock_url, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"
        mock_url.return_value = "https://minio.test/presigned"

        # Create set and upload, then upload revision 2
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_a_id = resp.json()["uploaded"][0]["revision"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions",
            files={"file": ("A-101_rev2.pdf", b"%PDF-v2", "application/pdf")},
            headers=auth_headers,
        )
        rev_b_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/compare",
            params={"rev_a": rev_a_id, "rev_b": rev_b_id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rev_a" in data
        assert "rev_b" in data
        assert data["rev_a"]["revision_number"] == 1
        assert data["rev_b"]["revision_number"] == 2


# ---------------------------------------------------------------------------
# Drawing Markups
# ---------------------------------------------------------------------------


class TestDrawingMarkups:
    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_create_markup(self, mock_upload, client, auth_headers, test_project):
        mock_upload.return_value = "drawings/test/key.pdf"

        # Create set + drawing
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_id = resp.json()["uploaded"][0]["revision"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
            json={
                "markup_data": {"type": "circle", "x": 100, "y": 200, "radius": 30},
                "markup_type": "cloud",
                "layer": "review",
                "label": "Check this detail",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["markup_type"] == "cloud"
        assert data["layer"] == "review"
        assert data["label"] == "Check this detail"

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_list_markups_filter_by_layer(
        self, mock_upload, client, auth_headers, test_project
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_id = resp.json()["uploaded"][0]["revision"]["id"]

        # Create markups in different layers
        for layer in ["review", "coordination", "punchlist"]:
            await client.post(
                f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
                json={"markup_data": {"note": layer}, "markup_type": "text", "layer": layer},
                headers=auth_headers,
            )

        # Filter by review layer
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
            params={"layer": "review"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["layer"] == "review"

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_update_markup(self, mock_upload, client, auth_headers, test_project):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_id = resp.json()["uploaded"][0]["revision"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
            json={"markup_data": {"old": True}, "markup_type": "cloud"},
            headers=auth_headers,
        )
        markup_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/markups/{markup_id}",
            json={"markup_data": {"updated": True}, "label": "Updated label"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["markup_data"] == {"updated": True}
        assert resp.json()["label"] == "Updated label"

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_delete_markup(self, mock_upload, client, auth_headers, test_project):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]
        rev_id = resp.json()["uploaded"][0]["revision"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/revisions/{rev_id}/markups",
            json={"markup_data": {"to": "delete"}, "markup_type": "arrow"},
            headers=auth_headers,
        )
        markup_id = resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/markups/{markup_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Drawing Links
# ---------------------------------------------------------------------------


class TestDrawingLinks:
    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_link_drawing_to_rfi(
        self, mock_upload, client, auth_headers, test_project, test_rfi
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            json={"link_type": "rfi", "entity_id": str(test_rfi.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_link_drawing_to_submittal(
        self, mock_upload, client, auth_headers, test_project, test_submittal
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            json={"link_type": "submittal", "entity_id": str(test_submittal.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_link_drawing_to_punch_list_item(
        self, mock_upload, client, auth_headers, test_project, test_punch_item
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            json={"link_type": "punch_list", "entity_id": str(test_punch_item.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_get_drawing_links(
        self, mock_upload, client, auth_headers, test_project, test_rfi
    ):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        # Link to RFI
        await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            json={"link_type": "rfi", "entity_id": str(test_rfi.id)},
            headers=auth_headers,
        )

        # Get links
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rfis"]) == 1
        assert data["rfis"][0]["rfi_number"] == "RFI-001"

    @pytest.mark.asyncio
    @patch("app.services.communication.drawing_service.upload_file")
    async def test_unlink_drawing(self, mock_upload, client, auth_headers, test_project, test_rfi):
        mock_upload.return_value = "drawings/test/key.pdf"

        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets",
            json={"name": "Test Set", "discipline": "architectural"},
            headers=auth_headers,
        )
        set_id = resp.json()["id"]
        files = [("files", ("A-101.pdf", b"%PDF-v1", "application/pdf"))]
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/drawing-sets/{set_id}/upload",
            files=files,
            headers=auth_headers,
        )
        drawing_id = resp.json()["uploaded"][0]["drawing"]["id"]

        # Link then unlink
        await client.post(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            json={"link_type": "rfi", "entity_id": str(test_rfi.id)},
            headers=auth_headers,
        )
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links/rfi/{test_rfi.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Verify link removed
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/drawings/{drawing_id}/links",
            headers=auth_headers,
        )
        assert len(resp.json()["rfis"]) == 0
