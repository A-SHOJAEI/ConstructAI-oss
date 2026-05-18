"""AI-assisted compliance review for submittals.

A lightweight pipeline that:
  1. Loads the submittal + spec_section + description.
  2. Retrieves the most relevant project-spec chunks via hybrid_search.
  3. Asks the LLM to assess compliance and produce findings + a recommended
     review action (no_exception_taken / approved_as_noted / revise_and_resubmit).
  4. Returns a structured dict the UI can render directly.

The pipeline is intentionally narrower than the RFI resolution agent — it
runs a single LLM call and skips full Stage-3 verification. The output is
labeled "AI-ASSISTED REVIEW" so reviewers don't mistake it for a final
disposition.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import Submittal
from app.services.rag.embeddings import embed_query
from app.services.rag.retrieval import hybrid_search

logger = logging.getLogger(__name__)


_PROMPT = """You are an experienced construction submittal reviewer. Your job
is to compare a submittal against the project specifications and produce an
AI-assisted compliance review. You do NOT make the final disposition — a
human reviewer always signs off — but your output guides their work.

Output STRICT JSON with this shape and nothing else:
{
  "recommendation": "no_exception_taken" | "approved_as_noted" | "revise_and_resubmit",
  "summary": "<1-2 sentence summary of compliance assessment>",
  "findings": [
    {"severity": "info" | "minor" | "major", "text": "<finding>", "spec_ref": "<spec section / paragraph if known>"}
  ],
  "confidence": <float 0.0-1.0>
}

Rules:
- Cite spec paragraph numbers (e.g. "03 30 00 §2.1.B") when available in the
  retrieved context. If you cannot find the relevant spec, set
  spec_ref to null and lower the confidence.
- "no_exception_taken" = fully compliant.
- "approved_as_noted" = compliant with minor clarifications/conditions.
- "revise_and_resubmit" = at least one major non-compliance.
- Never invent spec sections that are not in the retrieved context.
- Keep findings concrete and actionable.
"""


_RECOMMENDATIONS = {"no_exception_taken", "approved_as_noted", "revise_and_resubmit"}


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the JSON block out of the LLM response, even if it's wrapped."""
    if not text:
        return None
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Look for a fenced ```json ... ``` block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Last resort — first {...} blob
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def review_submittal_ai(
    db: AsyncSession,
    submittal: Submittal,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the AI review pipeline for a single submittal.

    Returns a dict with: recommendation, summary, findings, confidence,
    sources, model.
    """

    query_parts: list[str] = []
    if submittal.spec_section:
        query_parts.append(f"Spec section {submittal.spec_section}")
    if submittal.title:
        query_parts.append(submittal.title)
    if submittal.description:
        query_parts.append(submittal.description)
    query_text = " — ".join(query_parts) or submittal.title or "submittal"

    sources: list[dict[str, Any]] = []
    context_blocks: list[str] = []
    try:
        embedding = await embed_query(query_text)
        results = await hybrid_search(
            db,
            query=query_text,
            query_embedding=embedding,
            project_id=project_id,
            limit=8,
        )
        for hit in results:
            title = hit.get("document_title") or hit.get("title") or "Project Spec"
            page = hit.get("page_number") or hit.get("page")
            section = hit.get("spec_section_path") or hit.get("section")
            content = hit.get("content") or hit.get("chunk_text") or ""
            sources.append(
                {
                    "document_title": title,
                    "page_number": page,
                    "section": section,
                }
            )
            label = title
            if section:
                label = f"{title} — {section}"
            elif page is not None:
                label = f"{title} (p. {page})"
            context_blocks.append(f"[{label}]\n{content}")
    except Exception as exc:
        logger.warning("Submittal AI review retrieval failed: %s", exc)

    context_text = (
        "\n\n".join(context_blocks)
        if context_blocks
        else "(no relevant project spec chunks found in retrieval)"
    )

    user_message = (
        f"SUBMITTAL\n"
        f"Number: {submittal.submittal_number}\n"
        f"Title: {submittal.title}\n"
        f"Type: {submittal.submittal_type}\n"
        f"Spec Section: {submittal.spec_section or 'unspecified'}\n"
        f"Description: {submittal.description or '(none provided)'}\n\n"
        f"RETRIEVED PROJECT SPEC CONTEXT\n{context_text}\n\n"
        f"Produce the JSON-formatted AI-assisted review now."
    )

    from app.config import settings as _cfg

    model = _cfg.LLM_MODEL_RFI_AGENT or "gpt-4o"
    raw_text = ""
    model_used = model

    # Route through the LLM gateway only. The gateway has its own fallback
    # chain (Spark 1 vLLM + Spark 2 Ollama for this deployment); do NOT add
    # a langchain ChatOpenAI fallback here, since OPENAI_API_KEY is a
    # placeholder in the demo env and would surface an irrelevant 401.
    try:
        from app.services.reliability.llm_gateway import get_llm_gateway

        gateway = await get_llm_gateway()
        result = await gateway.complete(
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": user_message},
            ],
            agent_name="submittal_ai_review",
            temperature=0,
            max_tokens=1200,
        )
        raw_text = result.get("content", "")
        model_used = result.get("model", model)
    except Exception as exc:
        logger.error("Submittal AI review gateway call failed: %s", exc)
        return {
            "recommendation": "revise_and_resubmit",
            "summary": "AI review unavailable — the local LLM gateway is not responding. Please review manually.",
            "findings": [],
            "confidence": 0.0,
            "sources": sources,
            "model": None,
            "error": f"LLM gateway error: {exc}",
        }

    parsed = _extract_json(raw_text) or {}
    rec = parsed.get("recommendation", "revise_and_resubmit")
    if rec not in _RECOMMENDATIONS:
        rec = "revise_and_resubmit"
    findings_in = parsed.get("findings", []) or []
    findings: list[dict[str, Any]] = []
    for f in findings_in:
        if not isinstance(f, dict):
            continue
        sev = f.get("severity", "minor")
        if sev not in {"info", "minor", "major"}:
            sev = "minor"
        findings.append(
            {
                "severity": sev,
                "text": str(f.get("text", "")).strip(),
                "spec_ref": f.get("spec_ref"),
            }
        )

    conf_raw = parsed.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(conf_raw)))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "recommendation": rec,
        "summary": str(parsed.get("summary", "")).strip() or "AI review completed.",
        "findings": findings,
        "confidence": confidence,
        "sources": sources,
        "model": model_used,
    }
