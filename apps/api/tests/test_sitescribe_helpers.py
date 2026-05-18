"""Tests for SiteScribe pure helpers (template narrative builder).

The full service is DB-bound; these tests pin the documented source
type set and the LLM-fallback template-narrative builder.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

from app.services.products.sitescribe.service import (
    VALID_SOURCE_TYPES,
    _build_template_narrative,
    _extract_exif,
)

# =========================================================================
# VALID_SOURCE_TYPES — pin documented source types
# =========================================================================


def test_source_types_canonical():
    """Pin: photo / voice_memo / text_message / manual."""
    expected = {"photo", "voice_memo", "text_message", "manual"}
    assert expected == VALID_SOURCE_TYPES


# =========================================================================
# _extract_exif — placeholder behavior
# =========================================================================


def test_extract_exif_returns_empty_dict():
    """Documented mock: returns empty dict until prod EXIF parser is
    wired up. Pin so a partial implementation breaks the test."""
    out = _extract_exif("photo/path.jpg")
    assert out == {}


def test_extract_exif_none_input():
    """None s3_key — also returns empty dict."""
    assert _extract_exif(None) == {}


# =========================================================================
# _build_template_narrative — LLM-fallback narrative
# =========================================================================


def _source(source_type: str) -> object:
    """Build a minimal source-shaped object."""
    return SimpleNamespace(source_type=source_type)


def test_template_narrative_no_sources():
    """Empty sources → narrative still generated with 0 counts."""
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=uuid.uuid4(),
        sources=[],
    )
    assert "0 photos" in out
    assert "0 voice memos" in out
    assert "0 text notes" in out


def test_template_narrative_includes_date_iso():
    """Report date in ISO format must appear in the narrative."""
    project_id = uuid.uuid4()
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=project_id,
        sources=[],
    )
    assert "2026-04-26" in out


def test_template_narrative_includes_project_id():
    project_id = uuid.uuid4()
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=project_id,
        sources=[],
    )
    assert str(project_id) in out


def test_template_narrative_counts_each_source_type():
    sources = [
        _source("photo"),
        _source("photo"),
        _source("photo"),
        _source("voice_memo"),
        _source("voice_memo"),
        _source("text_message"),
        _source("manual"),
    ]
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=uuid.uuid4(),
        sources=sources,
    )
    assert "3 photos" in out
    assert "2 voice memos" in out
    # text_message and manual both count toward "text notes":
    assert "2 text notes" in out
    # Total source count:
    assert "Sources reviewed: 7" in out


def test_template_narrative_pending_review_message():
    """Fallback narrative explicitly tells the reviewer this is a
    placeholder."""
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=uuid.uuid4(),
        sources=[_source("photo")],
    )
    assert "review" in out.lower()


def test_template_narrative_unknown_source_types_not_counted():
    """An unknown source_type (data error) shouldn't crash; just
    not counted in any of the documented buckets."""
    sources = [
        _source("photo"),
        _source("alien_type"),  # unknown
        _source("voice_memo"),
    ]
    out = _build_template_narrative(
        report_date=date(2026, 4, 26),
        project_id=uuid.uuid4(),
        sources=sources,
    )
    # 1 photo, 1 voice memo, 0 text notes (alien doesn't match any):
    assert "1 photo" in out
    assert "1 voice memo" in out
    assert "0 text notes" in out
    # Sources reviewed total uses len() so it's still 3:
    assert "Sources reviewed: 3" in out
