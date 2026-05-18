"""Stage 4: RAG knowledge base cross-reference verification.

SECURITY [M-12]: This module implements basic source verification for agent
outputs. It checks that claimed sources can be matched against the provided
context chunks. This is a foundational implementation -- production deployments
should extend with full RAG-backed verification for higher assurance.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def verify(
    parsed_output: dict,
    agent_name: str,
    context_chunks: list[dict] | None = None,
) -> dict:
    """Cross-reference agent output against knowledge base.

    Verifies that claimed sources in the agent output can be matched
    against the provided context chunks (by document_title or content).
    Returns ``verification_passed=False`` if more than 50% of claimed
    sources cannot be matched.

    Args:
        parsed_output: The structured output from the agent.
        agent_name: Identifier for the calling agent.
        context_chunks: The RAG context chunks that were provided to the
            agent. Each chunk should have at minimum ``content`` and
            optionally ``document_title``.

    Returns:
        Dict with ``warnings`` list and ``verification_passed`` bool.
    """
    warnings: list[dict] = []
    context_chunks = context_chunks or []

    # Check if output contains verifiable claims
    verifiable_fields = [
        "specification_reference",
        "code_reference",
        "standard_reference",
        "material_spec",
    ]

    for field in verifiable_fields:
        if field in parsed_output:
            value = parsed_output[field]
            if not _has_source_reference(parsed_output):
                warnings.append(
                    {
                        "stage": "knowledge_verify",
                        "field": field,
                        "message": (
                            f"Claim '{field}={value}' not cross-referenced with knowledge base"
                        ),
                        "severity": "info",
                    }
                )

    # SECURITY [M-12]: Verify claimed sources against provided context chunks.
    # For each claimed source, check if it can be found in the context by
    # document_title match or by content substring match. If more than 50%
    # of claimed sources are unverifiable, flag verification as failed.
    claimed_sources = _extract_claimed_sources(parsed_output)
    verification_passed = True

    if claimed_sources and context_chunks:
        matched = 0
        for source in claimed_sources:
            if _source_matches_context(source, context_chunks):
                matched += 1
            else:
                warnings.append(
                    {
                        "stage": "knowledge_verify",
                        "field": "source",
                        "message": (
                            f"Claimed source '{source.get('document_title', 'unknown')}' "
                            f"could not be matched against provided context"
                        ),
                        "severity": "warning",
                    }
                )

        # Fail verification if more than 50% of sources are unmatched
        if len(claimed_sources) > 0 and matched / len(claimed_sources) < 0.5:
            verification_passed = False
            warnings.append(
                {
                    "stage": "knowledge_verify",
                    "field": "sources",
                    "message": (
                        f"Verification failed: only {matched}/{len(claimed_sources)} "
                        f"claimed sources could be matched against context"
                    ),
                    "severity": "error",
                }
            )
    elif claimed_sources and not context_chunks:
        # Sources claimed but no context provided — cannot verify
        verification_passed = False
        warnings.append(
            {
                "stage": "knowledge_verify",
                "field": "sources",
                "message": "Sources claimed but no context chunks provided for verification",
                "severity": "warning",
            }
        )

    return {"warnings": warnings, "verification_passed": verification_passed}


def _has_source_reference(output: dict) -> bool:
    """Check if output includes source references."""
    source_keys = ("source", "sources", "references", "source_documents")
    return any(key in output for key in source_keys)


def _extract_claimed_sources(output: dict) -> list[dict]:
    """Extract claimed source references from the parsed output.

    Looks for sources in common keys: ``sources``, ``references``,
    ``source_documents``. Returns a list of dicts, each with at least
    a ``document_title`` key (may be empty string if not available).
    """
    sources: list[dict] = []
    for key in ("sources", "references", "source_documents"):
        val = output.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    sources.append(item)
                elif isinstance(item, str):
                    sources.append({"document_title": item})
    # Single source field
    if "source" in output:
        val = output["source"]
        if isinstance(val, str):
            sources.append({"document_title": val})
        elif isinstance(val, dict):
            sources.append(val)
    return sources


def _source_matches_context(source: dict, context_chunks: list[dict]) -> bool:
    """Check if a claimed source matches any of the provided context chunks.

    Matching strategies (any match is sufficient):
    1. Document title exact or substring match (case-insensitive).
    2. Source content appears as substring in a chunk's content.

    Returns True if the source can be verified against the context.
    """
    source_title = (source.get("document_title") or "").lower().strip()
    source_section = (source.get("section") or "").lower().strip()

    for chunk in context_chunks:
        chunk_title = (chunk.get("document_title") or "").lower().strip()
        chunk_content = (chunk.get("content") or "").lower()

        # Strategy 1: Title match
        if source_title and chunk_title:
            if source_title in chunk_title or chunk_title in source_title:
                return True

        # Strategy 2: Section match within same document context
        if source_section and source_section in chunk_content:
            return True

        # Strategy 3: Title appears in chunk content
        if source_title and len(source_title) > 3 and source_title in chunk_content:
            return True

    return False
