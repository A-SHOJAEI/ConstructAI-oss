"""Tests for RFI-helper pure functions.

The RFI helper has three public functions:

- ``suggest_rfi_response``: builds a draft response. Tested with mocked
  RAG search (so no DB needed) and the legacy fallback path.
- ``suggest_spec_section``: keyword → CSI division mapping.
- ``assess_impact``: cost/schedule keyword detection.

The RAG side is mocked at the boundary (embed_query + hybrid_search)
so each test runs without network or DB.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.communication.rfi_helper import (
    assess_impact,
    suggest_rfi_response,
    suggest_spec_section,
)

# =========================================================================
# suggest_rfi_response — RAG path
# =========================================================================


async def test_suggest_response_uses_rag_when_db_and_project_id_present():
    """When db + project_id are passed, the helper calls embed_query and
    hybrid_search. We verify the response embeds the snippets and the
    references reflect document titles."""
    fake_results = [
        {"title": "Spec 03 30 00", "content": "Concrete shall be ASTM C150 Type I/II."},
        {"document_title": "Drawing S-101", "content": "See note 5 for rebar size."},
    ]
    with (
        patch(
            "app.services.rag.embeddings.embed_query",
            new=AsyncMock(return_value=[0.1] * 1024),
        ),
        patch(
            "app.services.rag.retrieval.hybrid_search",
            new=AsyncMock(return_value=fake_results),
        ),
    ):
        out = await suggest_rfi_response(
            subject="Concrete spec clarification",
            question="What strength?",
            db=AsyncMock(),
            project_id=uuid.uuid4(),
        )

    # Both reference titles surfaced (one via "title", one via "document_title"):
    assert "Spec 03 30 00" in out["references"]
    assert "Drawing S-101" in out["references"]
    # Snippets embedded:
    assert "ASTM C150" in out["suggested_response"]
    # Confidence rises with the number of snippets:
    assert out["confidence"] > 0.5


async def test_suggest_response_rag_failure_falls_back_silently():
    """A RAG-side error (e.g. embed_query timeout) must not crash —
    the helper falls through to the legacy project_context path."""
    with (
        patch(
            "app.services.rag.embeddings.embed_query",
            new=AsyncMock(side_effect=ConnectionError("voyage down")),
        ),
        patch("app.services.rag.retrieval.hybrid_search", new=AsyncMock()),
    ):
        out = await suggest_rfi_response(
            subject="Q",
            question="Q",
            project_context={"specifications": ["Spec 03 30 00"]},
            db=AsyncMock(),
            project_id=uuid.uuid4(),
        )
    # Fell back to legacy reference list:
    assert any("Spec 03 30 00" in r for r in out["references"])


async def test_suggest_response_string_project_id_coerces_to_uuid():
    """``project_id`` may arrive as a string from the API layer — the
    helper must coerce to UUID before calling hybrid_search."""
    with (
        patch(
            "app.services.rag.embeddings.embed_query",
            new=AsyncMock(return_value=[0.0] * 1024),
        ),
        patch(
            "app.services.rag.retrieval.hybrid_search",
            new=AsyncMock(return_value=[]),
        ) as hs,
    ):
        await suggest_rfi_response(
            subject="x",
            question="y",
            db=AsyncMock(),
            project_id=str(uuid.uuid4()),  # string, not UUID
        )
    _, kwargs = hs.call_args
    assert isinstance(kwargs["project_id"], uuid.UUID)


# =========================================================================
# suggest_rfi_response — legacy fallback path
# =========================================================================


async def test_suggest_response_uses_legacy_specs_when_no_db():
    out = await suggest_rfi_response(
        subject="Concrete clarification",
        question="What strength?",
        project_context={
            "specifications": ["03 30 00 Concrete", "03 20 00 Reinforcement"],
            "drawings": ["S-101", "S-102"],
        },
    )
    refs = out["references"]
    assert any("03 30 00" in r for r in refs)
    assert any("S-101" in r for r in refs)
    # Legacy-mode confidence: 0.7 with refs, 0.3 without.
    assert out["confidence"] == 0.7


async def test_suggest_response_no_refs_no_db_low_confidence():
    out = await suggest_rfi_response(subject="Q", question="Q")
    assert out["references"] == []
    assert out["confidence"] == 0.3


async def test_suggest_response_legacy_caps_at_three_references():
    """Legacy fallback caps each ref category at 3 entries to avoid
    bloating the suggested response."""
    out = await suggest_rfi_response(
        subject="Q",
        question="Q",
        project_context={
            "specifications": [f"Spec {i}" for i in range(10)],
            "drawings": [f"D-{i}" for i in range(10)],
        },
    )
    spec_refs = [r for r in out["references"] if r.startswith("Spec:")]
    drawing_refs = [r for r in out["references"] if r.startswith("Drawing:")]
    assert len(spec_refs) == 3
    assert len(drawing_refs) == 3


# =========================================================================
# suggest_spec_section
# =========================================================================


@pytest.mark.parametrize(
    "subject,expected_division",
    [
        ("Concrete strength", "03"),
        ("Steel beam connection", "05"),
        ("HVAC ductwork routing", "23"),
        ("Drywall finish question", "09"),
        ("Door hardware schedule", "08"),
        ("Excavation grading depth", "31"),
        ("Roofing membrane termination", "07"),
    ],
)
async def test_suggest_spec_section_maps_keyword_to_correct_division(
    subject: str, expected_division: str
):
    out = await suggest_spec_section(subject, "Need clarification.")
    assert out["spec_section"] is not None
    assert out["spec_section"].startswith(f"Division {expected_division}")
    assert out["confidence"] > 0.0


async def test_suggest_spec_section_returns_none_when_no_match():
    out = await suggest_spec_section("totally unrelated topic", "blah blah")
    assert out["spec_section"] is None
    assert out["confidence"] == 0.0


async def test_suggest_spec_section_picks_highest_keyword_count():
    """Concrete + rebar + slab → 3 hits in Division 03 vs 1 each in
    other divisions. The higher-hit division wins."""
    out = await suggest_spec_section("Concrete slab with rebar reinforcement", "How many bars?")
    assert out["spec_section"].startswith("Division 03")
    # Confidence rises with hits (capped at 0.9):
    assert 0.5 <= out["confidence"] <= 0.9


# =========================================================================
# assess_impact
# =========================================================================


async def test_assess_impact_detects_cost_keyword():
    out = await assess_impact("Material substitution", "Need price quote.")
    assert out["cost_impact"] is True
    assert out["schedule_impact"] is False
    assert out["cost_estimate"] is None  # placeholder


async def test_assess_impact_detects_schedule_keyword():
    out = await assess_impact("Long lead item", "Will this delay critical path?")
    assert out["schedule_impact"] is True


async def test_assess_impact_detects_both_dimensions():
    out = await assess_impact(
        "Change order needed",
        "This will delay the schedule and cost a premium.",
    )
    assert out["cost_impact"] is True
    assert out["schedule_impact"] is True
    # More keyword hits → higher confidence (capped at 0.85).
    assert out["confidence"] > 0.5


async def test_assess_impact_no_keywords_low_confidence():
    out = await assess_impact("General question", "Just curious about the design.")
    assert out["cost_impact"] is False
    assert out["schedule_impact"] is False
    assert out["confidence"] < 0.5


async def test_assess_impact_confidence_capped():
    """Lots of keywords shouldn't push confidence above 0.85."""
    out = await assess_impact(
        "delay schedule lead time critical path expedite postpone",
        "additional cost premium upgrade material cost budget substitution",
    )
    assert out["confidence"] <= 0.85
