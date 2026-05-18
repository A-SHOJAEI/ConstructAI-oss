"""Tests for the document comparison API.

Tests the diff engine and the API endpoint (auth, project scoping, limits).
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Pure diff engine tests (no DB required)
# ---------------------------------------------------------------------------


class TestComputeDiff:
    """Unit tests for the _compute_diff helper."""

    def test_identical_documents_have_no_diffs(self):
        from app.api.v1.document_compare import _compute_diff

        text = "Line one\nLine two\nLine three\n"
        result = _compute_diff(text, text)

        assert result.added == 0
        assert result.removed == 0
        assert result.modified == 0
        assert result.similarity_ratio == 1.0
        assert len(result.diffs) == 0

    def test_added_lines_detected(self):
        from app.api.v1.document_compare import _compute_diff

        text_a = "Line one\n"
        text_b = "Line one\nLine two\n"
        result = _compute_diff(text_a, text_b)

        assert result.added >= 1
        assert any(d.change_type == "added" for d in result.diffs)
        assert result.similarity_ratio < 1.0

    def test_removed_lines_detected(self):
        from app.api.v1.document_compare import _compute_diff

        text_a = "Line one\nLine two\n"
        text_b = "Line one\n"
        result = _compute_diff(text_a, text_b)

        assert result.removed >= 1
        assert any(d.change_type == "removed" for d in result.diffs)

    def test_modified_lines_detected(self):
        from app.api.v1.document_compare import _compute_diff

        text_a = "SECTION 03 30 00 - Cast-In-Place Concrete\n"
        text_b = "SECTION 03 30 00 - Precast Concrete\n"
        result = _compute_diff(text_a, text_b)

        assert result.modified >= 1
        assert any(d.change_type == "modified" for d in result.diffs)
        modified_diff = next(d for d in result.diffs if d.change_type == "modified")
        assert "Cast-In-Place" in modified_diff.old_text
        assert "Precast" in modified_diff.new_text

    def test_completely_different_documents(self):
        from app.api.v1.document_compare import _compute_diff

        text_a = "Alpha\nBravo\nCharlie\n"
        text_b = "Delta\nEcho\nFoxtrot\n"
        result = _compute_diff(text_a, text_b)

        assert result.similarity_ratio < 0.5
        assert len(result.diffs) > 0

    def test_empty_documents(self):
        from app.api.v1.document_compare import _compute_diff

        result = _compute_diff("", "")
        assert result.similarity_ratio == 1.0
        assert result.added == 0
        assert result.removed == 0


class TestExtractTextFromChunks:
    """Test the chunk-to-text helper."""

    def test_chunks_sorted_by_index(self):
        from app.api.v1.document_compare import _extract_text_from_chunks

        chunks = [
            {"content": "second", "chunk_index": 1},
            {"content": "first", "chunk_index": 0},
            {"content": "third", "chunk_index": 2},
        ]
        text = _extract_text_from_chunks(chunks)
        assert text == "first\nsecond\nthird"

    def test_empty_chunks_return_empty_string(self):
        from app.api.v1.document_compare import _extract_text_from_chunks

        assert _extract_text_from_chunks([]) == ""


# ---------------------------------------------------------------------------
# API endpoint tests (require DB fixtures from conftest)
# ---------------------------------------------------------------------------


class TestDocumentCompareEndpoint:
    """POST /projects/{pid}/documents/compare"""

    @pytest.mark.asyncio
    async def test_compare_requires_auth(self, client):
        """Un-authed POST is rejected by CSRFMiddleware (no Bearer, no CSRF
        token) with 403 before the auth dependency runs."""
        fake_pid = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/projects/{fake_pid}/documents/compare",
            json={
                "document_a_id": str(uuid.uuid4()),
                "document_b_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_compare_returns_404_for_missing_project(self, client, auth_headers):
        """A non-existent project_id should yield 404."""
        fake_pid = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/projects/{fake_pid}/documents/compare",
            json={
                "document_a_id": str(uuid.uuid4()),
                "document_b_id": str(uuid.uuid4()),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_returns_404_for_missing_document(self, client, auth_headers):
        """When the project exists but the document does not, expect 404."""
        # Create a project
        create_resp = await client.post(
            "/api/v1/projects/",
            json={"name": "DocCompare Project"},
            headers=auth_headers,
        )
        project_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/documents/compare",
            json={
                "document_a_id": str(uuid.uuid4()),
                "document_b_id": str(uuid.uuid4()),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestDiffSectionModel:
    """Validate the DiffSection Pydantic model."""

    def test_diff_section_defaults(self):
        from app.api.v1.document_compare import DiffSection

        ds = DiffSection(change_type="added")
        assert ds.section == ""
        assert ds.old_text == ""
        assert ds.new_text == ""
        assert ds.line_start == 0
        assert ds.line_end == 0

    def test_comparison_result_model(self):
        from app.api.v1.document_compare import ComparisonResult

        result = ComparisonResult(
            document_a_id="aaa",
            document_b_id="bbb",
            total_sections=10,
            added=3,
            removed=2,
            modified=1,
            unchanged=4,
            similarity_ratio=0.75,
            diffs=[],
        )
        assert result.total_sections == 10
        assert result.similarity_ratio == 0.75
