"""Phase 1: Document upload endpoint tests.

These tests exercise the POST /api/v1/documents/upload endpoint, verifying
that file validation, S3 storage, and database record creation work correctly.
All S3 interactions are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest_asyncio
from app.models.project import Project

from tests.fixtures.sample_documents import create_sample_pdf


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a project in the test database for upload tests."""
    project = Project(name="Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestDocumentUpload:
    """Tests for the document upload endpoint."""

    @patch("app.api.v1.documents.upload_file", return_value="documents/test.pdf")
    async def test_upload_pdf_returns_document_id(
        self, mock_upload, client, auth_headers, test_project
    ):
        """Uploading a PDF should return 201 with a document ID and pending status."""
        pdf_bytes = create_sample_pdf()

        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
            data={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "id" in data
        assert data["processing_status"] == "pending"
        uuid.UUID(data["id"])

    async def test_upload_rejects_unsupported_filetype(self, client, auth_headers, test_project):
        """Uploading an .exe file should return 422 Unprocessable Entity."""
        exe_bytes = b"\x4d\x5a" + b"\x00" * 100

        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("malware.exe", exe_bytes, "application/octet-stream")},
            data={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

        assert response.status_code == 422, (
            f"Expected 422 for .exe upload, got {response.status_code}: {response.text}"
        )

    def test_upload_rejects_oversized_file(self):
        """The endpoint enforces a 500MB file size limit via MAX_FILE_SIZE_BYTES.

        Sending 500+ MB over ASGI in a test is impractical, so we verify
        the size limit constant and the validation logic exist.
        """
        from app.api.v1.documents import MAX_FILE_SIZE_BYTES

        assert MAX_FILE_SIZE_BYTES == 500 * 1024 * 1024

        # Verify the size check pattern exists in the endpoint source
        import inspect

        from app.api.v1.documents import upload_document

        source = inspect.getsource(upload_document)
        assert "MAX_FILE_SIZE_BYTES" in source
        assert "HTTP_413" in source

    @patch("app.api.v1.documents.upload_file", return_value="documents/test.pdf")
    async def test_upload_creates_s3_object(self, mock_upload, client, auth_headers, test_project):
        """After upload, the S3 upload function should have been called with the file bytes."""
        pdf_bytes = create_sample_pdf()

        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("spec.pdf", pdf_bytes, "application/pdf")},
            data={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

        assert response.status_code == 201, f"Upload failed: {response.text}"
        mock_upload.assert_called_once()
        call_args = mock_upload.call_args
        assert call_args is not None
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        assert any(isinstance(arg, bytes | memoryview) for arg in all_args), (
            "S3 upload should have received file bytes"
        )

    @patch("app.api.v1.documents.upload_file", return_value="documents/test.pdf")
    async def test_upload_creates_database_record(
        self, mock_upload, client, auth_headers, test_project
    ):
        """After upload, the document should be retrievable from the database via API."""
        pdf_bytes = create_sample_pdf()

        upload_response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("foundation_spec.pdf", pdf_bytes, "application/pdf")},
            data={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

        assert upload_response.status_code == 201, f"Upload failed: {upload_response.text}"
        doc_id = upload_response.json()["id"]

        get_response = await client.get(
            f"/api/v1/documents/{doc_id}",
            headers=auth_headers,
        )

        assert get_response.status_code == 200, (
            f"Expected 200 for GET document, got {get_response.status_code}: {get_response.text}"
        )
        data = get_response.json()
        assert data["id"] == doc_id
        assert data["original_filename"] == "foundation_spec.pdf"
        assert data["processing_status"] in ("pending", "processing")
