"""Tests for the CloseoutIQ document validation helper.

The full service is DB + LLM bound; this file pins the deterministic
file-extension and type-specific validation logic that runs before
the LLM is asked. Even when the LLM is unreachable, these checks
must catch obvious mismatches.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.products.closeout_iq.service import _validate_document


@pytest.fixture(autouse=True)
def disable_llm():
    """Disable the LLM call so we test only the deterministic checks
    (the LLM call is best-effort and not deterministic)."""
    with patch("openai.AsyncOpenAI", side_effect=ImportError("no openai")):
        yield


# =========================================================================
# File extension validation
# =========================================================================


@pytest.mark.asyncio
async def test_pdf_accepted_no_warning():
    """PDF is the canonical closeout document format — no flags."""
    out = await _validate_document("warranty.pdf", "warranty")
    assert not any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_docx_accepted_no_warning():
    out = await _validate_document("warranty.docx", "warranty")
    assert not any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_xlsx_accepted_for_closeout_documents():
    """Spreadsheets are common closeout formats (BOMs, schedules)."""
    out = await _validate_document("equipment_list.xlsx", "spec_data")
    assert not any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_image_extensions_accepted():
    for ext in ("jpg", "jpeg", "png", "tiff", "tif"):
        out = await _validate_document(f"photo.{ext}", "punch_list")
        assert not any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_unusual_file_type_flagged():
    """An exe or zip → flagged with 'unusual_file_type' warning."""
    out = await _validate_document("strange.exe", "warranty")
    assert any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_no_extension_flagged():
    """File with no extension → unusual_file_type flag."""
    out = await _validate_document("noextension", "warranty")
    assert any(f["flag"] == "unusual_file_type" for f in out)


# =========================================================================
# Type-specific checks — warranty
# =========================================================================


@pytest.mark.asyncio
async def test_warranty_pdf_no_warning():
    """PDF is the canonical warranty format — no PDF-expected flag."""
    out = await _validate_document("warranty.pdf", "warranty")
    assert not any(f["flag"] == "expected_pdf_for_warranty" for f in out)


@pytest.mark.asyncio
async def test_warranty_image_emits_pdf_expected_info():
    """An image file for warranty triggers the documented info flag —
    warranty letters are typically PDF or Word."""
    out = await _validate_document("warranty.jpg", "warranty")
    flags = [f for f in out if f["flag"] == "expected_pdf_for_warranty"]
    assert len(flags) == 1
    assert flags[0]["severity"] == "info"


# =========================================================================
# Type-specific checks — test_report
# =========================================================================


@pytest.mark.asyncio
async def test_test_report_image_emits_warning():
    """Image format for a test report → warning (not info, more
    serious — images are hard to verify)."""
    out = await _validate_document("report.jpg", "test_report")
    flags = [f for f in out if f["flag"] == "image_for_test_report"]
    assert len(flags) == 1
    assert flags[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_test_report_pdf_no_warning():
    out = await _validate_document("report.pdf", "test_report")
    assert not any(f["flag"] == "image_for_test_report" for f in out)


@pytest.mark.asyncio
async def test_test_report_png_emits_warning():
    out = await _validate_document("report.png", "test_report")
    assert any(f["flag"] == "image_for_test_report" for f in out)


# =========================================================================
# Flag schema invariants
# =========================================================================


@pytest.mark.asyncio
async def test_flags_have_required_keys():
    """Every flag dict has flag/severity/message keys."""
    out = await _validate_document("strange.exe", "warranty")
    for flag in out:
        assert "flag" in flag
        assert "severity" in flag
        assert "message" in flag


@pytest.mark.asyncio
async def test_severity_values_canonical():
    """Severities are one of: info / warning / error."""
    out = await _validate_document("strange.exe", "warranty")
    for flag in out:
        assert flag["severity"] in {"info", "warning", "error"}


@pytest.mark.asyncio
async def test_clean_document_no_flags():
    """Normal warranty PDF → no flags at all (LLM disabled)."""
    out = await _validate_document("warranty.pdf", "warranty")
    assert out == []


@pytest.mark.asyncio
async def test_filename_extension_case_insensitive():
    """Filename extension matching is lowercased — PDF.PDF should
    NOT trigger unusual_file_type flag."""
    out = await _validate_document("WARRANTY.PDF", "warranty")
    assert not any(f["flag"] == "unusual_file_type" for f in out)


@pytest.mark.asyncio
async def test_multiple_flags_for_warranty_image():
    """A .jpg for warranty triggers BOTH:
    - expected_pdf_for_warranty (info, warranty-specific check)
    Note: jpg is in allowed extensions so unusual_file_type does NOT fire.
    """
    out = await _validate_document("warranty.jpg", "warranty")
    assert any(f["flag"] == "expected_pdf_for_warranty" for f in out)
    # JPG is allowed → no unusual_file_type:
    assert not any(f["flag"] == "unusual_file_type" for f in out)
