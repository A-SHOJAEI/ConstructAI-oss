"""AI-assisted scope analysis for change orders / PCOs.

Flags work scopes that are likely *not* genuine additional work — for
instance, when an answered RFI already covers the issue, when the
clarification is in the contract specs, or when a related submittal already
addresses it.

The pipeline:
  1. Collect description + change_type + spec_section + drawing_reference.
  2. Search answered RFIs (similarity search) for matches.
  3. Retrieve project-spec / contract chunks via hybrid_search.
  4. Send everything to the LLM gateway with a strict-JSON prompt.
  5. Return a verdict: additional_work | covered_by_contract |
     covered_by_rfi | needs_clarification, plus citations.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.embeddings import embed_query
from app.services.rag.retrieval import hybrid_search, search_similar_rfis

logger = logging.getLogger(__name__)


_PROMPT = """You are a senior construction project manager evaluating whether
a proposed change order represents genuine ADDITIONAL work, or whether the
work is already covered by the existing contract / specs / answered RFIs.

You are given:
  - The change order (or PCO) title, description, and any references.
  - Up to 8 retrieved project-spec / contract chunks.
  - Up to 5 answered RFIs whose questions resemble this scope.

Output STRICT JSON with this exact shape and nothing else:
{
  "verdict": "additional_work" | "covered_by_contract" | "covered_by_rfi" | "needs_clarification",
  "summary": "<1-2 sentence verdict explanation>",
  "evidence": [
    {"type": "spec" | "rfi" | "drawing", "ref": "<doc/rfi number or section>", "quote": "<short quote>"}
  ],
  "recommendation": "<short recommendation for the PM>",
  "confidence": <float 0.0-1.0>
}

Definitions:
- "additional_work" = scope is NOT covered by existing contract documents — proceed with the change order.
- "covered_by_contract" = scope already in the spec/contract; this is included work, NOT a change.
- "covered_by_rfi" = an answered RFI directly resolves this scope; no new change order needed.
- "needs_clarification" = ambiguous; flag for owner/architect review before approving.

Rules:
- Only cite documents/RFIs that appear in the retrieved context.
- Quote exactly from the retrieved text — do not paraphrase.
- If the retrieved context is empty or irrelevant, set verdict to "needs_clarification" and lower confidence.
"""


_VERDICTS = {
    "additional_work",
    "covered_by_contract",
    "covered_by_rfi",
    "needs_clarification",
}


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def analyze_change_order_scope(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    title: str,
    description: str,
    change_type: str,
    spec_section: str | None = None,
    drawing_reference: str | None = None,
) -> dict[str, Any]:
    """Run the AI scope analysis. Returns dict with verdict + evidence."""

    query_parts = [title, description]
    if spec_section:
        query_parts.append(f"spec section {spec_section}")
    if drawing_reference:
        query_parts.append(f"drawing {drawing_reference}")
    query_text = " — ".join(p for p in query_parts if p)

    # 1. Retrieve project-spec context
    spec_sources: list[dict[str, Any]] = []
    spec_blocks: list[str] = []
    try:
        embedding = await embed_query(query_text)
        spec_hits = await hybrid_search(
            db,
            query=query_text,
            query_embedding=embedding,
            project_id=project_id,
            limit=8,
        )
        for hit in spec_hits:
            doc_title = hit.get("document_title") or hit.get("title") or "Project Spec"
            page = hit.get("page_number") or hit.get("page")
            section = hit.get("spec_section_path") or hit.get("section")
            content = hit.get("content") or hit.get("chunk_text") or ""
            spec_sources.append(
                {"document_title": doc_title, "page_number": page, "section": section}
            )
            label_parts = [doc_title]
            if section:
                label_parts.append(section)
            elif page is not None:
                label_parts.append(f"p. {page}")
            spec_blocks.append(f"[SPEC: {' — '.join(label_parts)}]\n{content}")
    except Exception as exc:
        logger.warning("Scope analysis spec retrieval failed: %s", exc)

    # 2. Find similar answered RFIs (ones with answers that may resolve this)
    rfi_sources: list[dict[str, Any]] = []
    rfi_blocks: list[str] = []
    try:
        rfi_hits = await search_similar_rfis(
            db,
            question_text=query_text,
            project_id=project_id,
            similarity_threshold=0.70,
            limit=5,
        )
        for hit in rfi_hits:
            answer = hit.get("answer") or ""
            if not answer:
                continue
            number = hit.get("rfi_number") or "RFI"
            subject = hit.get("subject") or ""
            question = hit.get("question") or ""
            score = hit.get("similarity_score")
            rfi_sources.append(
                {
                    "rfi_number": number,
                    "subject": subject,
                    "similarity_score": float(score) if score is not None else None,
                }
            )
            rfi_blocks.append(f"[RFI: {number} — {subject}]\nQ: {question}\nA: {answer}")
    except Exception as exc:
        logger.warning("Scope analysis RFI retrieval failed: %s", exc)

    spec_text = (
        "\n\n".join(spec_blocks)
        if spec_blocks
        else "(no relevant project spec / contract chunks found)"
    )
    rfi_text = "\n\n".join(rfi_blocks) if rfi_blocks else "(no closely related answered RFIs found)"

    user_message = (
        f"CHANGE ORDER UNDER REVIEW\n"
        f"Title: {title}\n"
        f"Type: {change_type}\n"
        f"Spec section: {spec_section or 'unspecified'}\n"
        f"Drawing reference: {drawing_reference or 'unspecified'}\n"
        f"Description: {description or '(none provided)'}\n\n"
        f"RETRIEVED PROJECT SPECS / CONTRACT EXCERPTS\n{spec_text}\n\n"
        f"RELATED ANSWERED RFIs\n{rfi_text}\n\n"
        f"Produce the JSON-formatted scope analysis now."
    )

    raw_text = ""
    model_used = None
    try:
        from app.services.reliability.llm_gateway import get_llm_gateway

        gateway = await get_llm_gateway()
        result = await gateway.complete(
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": user_message},
            ],
            agent_name="change_order_scope_analysis",
            temperature=0,
            max_tokens=1200,
        )
        raw_text = result.get("content", "")
        model_used = result.get("model")
    except Exception as exc:
        logger.error("Change-order scope analysis gateway failed: %s", exc)
        return {
            "verdict": "needs_clarification",
            "summary": "AI scope analysis unavailable — the local LLM gateway is not responding. Please review manually.",
            "evidence": [],
            "recommendation": "Retry once gateway is reachable, or escalate manually.",
            "confidence": 0.0,
            "spec_sources": spec_sources,
            "rfi_sources": rfi_sources,
            "model": None,
            "error": f"LLM gateway error: {exc}",
        }

    parsed = _extract_json(raw_text) or {}
    verdict = parsed.get("verdict", "needs_clarification")
    if verdict not in _VERDICTS:
        verdict = "needs_clarification"

    evidence_in = parsed.get("evidence", []) or []
    evidence: list[dict[str, Any]] = []
    for e in evidence_in:
        if not isinstance(e, dict):
            continue
        etype = e.get("type", "spec")
        if etype not in {"spec", "rfi", "drawing"}:
            etype = "spec"
        evidence.append(
            {
                "type": etype,
                "ref": str(e.get("ref", "")).strip(),
                "quote": str(e.get("quote", "")).strip(),
            }
        )

    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "verdict": verdict,
        "summary": str(parsed.get("summary", "")).strip() or "Scope analysis completed.",
        "evidence": evidence,
        "recommendation": str(parsed.get("recommendation", "")).strip(),
        "confidence": confidence,
        "spec_sources": spec_sources,
        "rfi_sources": rfi_sources,
        "model": model_used,
    }
