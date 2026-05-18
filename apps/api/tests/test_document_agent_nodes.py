"""Tests for the document analysis LangGraph agent nodes.

Pin per-node behavior + the documented fan-out shape (classify ->
[extract_entities, detect_risks] in parallel) + per-node error
isolation (entity/risk failures must NOT crash the whole graph).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.document_agent import (
    build_document_agent,
    classify_node,
    detect_risks_node,
    extract_entities_node,
)

# =========================================================================
# classify_node
# =========================================================================


@pytest.mark.asyncio
async def test_classify_node_passes_text_and_filename():
    """[contract] classifier receives both text_content AND filename
    (filename hints help disambiguate, e.g., spec.pdf vs rfi.pdf)."""
    captured = {}

    async def fake_classify(*, text_sample, filename):
        captured.update({"text_sample": text_sample, "filename": filename})
        return {"document_type": "specification", "confidence": 0.9}

    state = {
        "document_id": "d-1",
        "text_content": "PART 1 - GENERAL\n1.1 SCOPE",
        "filename": "spec_03_30_00.pdf",
    }
    with patch("app.services.agents.document_agent.classify_document", fake_classify):
        out = await classify_node(state)

    assert captured["text_sample"] == "PART 1 - GENERAL\n1.1 SCOPE"
    assert captured["filename"] == "spec_03_30_00.pdf"
    assert out["status"] == "classified"
    assert out["classification"]["document_type"] == "specification"


@pytest.mark.asyncio
async def test_classify_node_failure_isolated():
    """[error isolation] Classifier crash -> classification=None,
    status='classification_failed', error captured. Graph keeps
    moving."""

    async def boom(**_kwargs):
        raise RuntimeError("LLM unavailable")

    state = {
        "document_id": "d-1",
        "text_content": "x",
        "filename": "x.pdf",
    }
    with patch("app.services.agents.document_agent.classify_document", boom):
        out = await classify_node(state)

    assert out["classification"] is None
    assert out["status"] == "classification_failed"
    assert "LLM unavailable" in out["error"]


# =========================================================================
# extract_entities_node
# =========================================================================


@pytest.mark.asyncio
async def test_extract_entities_node_passes_text():
    """Entity extractor receives only text (no filename)."""
    captured = {}

    async def fake_extract(*, text):
        captured["text"] = text
        return [{"entity_type": "product", "entity_value": "concrete"}]

    state = {"document_id": "d-1", "text_content": "Use 4000 psi concrete."}
    with patch("app.services.agents.document_agent.extract_entities", fake_extract):
        out = await extract_entities_node(state)

    assert captured["text"] == "Use 4000 psi concrete."
    assert len(out["entities"]) == 1
    assert out["entities"][0]["entity_value"] == "concrete"


@pytest.mark.asyncio
async def test_extract_entities_node_failure_yields_empty_list():
    """[error isolation] Extractor crash -> entities=[] (NOT None,
    NOT crash). Pin so downstream consumers don't choke on a None
    list when iterating."""

    async def boom(**_kwargs):
        raise ValueError("token limit")

    state = {"document_id": "d-1", "text_content": "x"}
    with patch("app.services.agents.document_agent.extract_entities", boom):
        out = await extract_entities_node(state)

    assert out["entities"] == []
    assert "token limit" in out["error"]


@pytest.mark.asyncio
async def test_extract_entities_returns_extractor_output_intact():
    """Whatever the extractor returns is passed through unmodified —
    no filtering or normalization at the node level (that's the
    extractor's job)."""

    async def fake_extract(**_kwargs):
        return [
            {"entity_type": "product", "entity_value": "rebar"},
            {"entity_type": "standard", "entity_value": "ASTM A615"},
        ]

    state = {"document_id": "d-1", "text_content": "x"}
    with patch("app.services.agents.document_agent.extract_entities", fake_extract):
        out = await extract_entities_node(state)

    assert len(out["entities"]) == 2
    assert {e["entity_type"] for e in out["entities"]} == {"product", "standard"}


# =========================================================================
# detect_risks_node
# =========================================================================


@pytest.mark.asyncio
async def test_detect_risks_node_passes_text():
    captured = {}

    async def fake_detect(*, text):
        captured["text"] = text
        return [{"risk_type": "liability", "severity": "high"}]

    state = {"document_id": "d-1", "text_content": "Contract clause 9.4: unlimited liability."}
    with patch("app.services.agents.document_agent.detect_risks", fake_detect):
        out = await detect_risks_node(state)

    assert "9.4" in captured["text"]
    assert len(out["risks"]) == 1
    assert out["risks"][0]["risk_type"] == "liability"


@pytest.mark.asyncio
async def test_detect_risks_node_failure_yields_empty_list():
    """[error isolation] Same pattern as entity extractor — crashes
    yield [], not None, not raise."""

    async def boom(**_kwargs):
        raise RuntimeError("API timeout")

    state = {"document_id": "d-1", "text_content": "x"}
    with patch("app.services.agents.document_agent.detect_risks", boom):
        out = await detect_risks_node(state)

    assert out["risks"] == []
    assert "API timeout" in out["error"]


# =========================================================================
# Graph topology — fan-out shape
# =========================================================================


def test_build_document_agent_returns_compiled_graph():
    graph = build_document_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    # 3 documented processing nodes:
    assert {"classify", "extract_entities", "detect_risks"} <= nodes


def test_build_document_agent_has_classify_as_entry_point():
    """[contract] classify runs FIRST, fans out to entity + risk in
    parallel. Pin: refactor must not change entry point or skip
    classification."""
    graph = build_document_agent()
    g = graph.get_graph()
    # Edge from classify to both extract_entities and detect_risks
    edge_targets = {(e.source, e.target) for e in g.edges}
    assert ("classify", "extract_entities") in edge_targets
    assert ("classify", "detect_risks") in edge_targets


def test_build_document_agent_with_none_checkpointer_compiles():
    """[fallback] Passing None checkpointer (the documented default
    for stateless runs) must compile successfully."""
    graph = build_document_agent(checkpointer=None)
    assert graph is not None
