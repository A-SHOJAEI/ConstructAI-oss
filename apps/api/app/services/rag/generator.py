"""LLM answer generation for the construction-domain RAG pipeline."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import the LLM Gateway for preferred routing
# ---------------------------------------------------------------------------
try:
    from app.services.reliability.llm_gateway import get_llm_gateway

    _HAS_GATEWAY = True
except ImportError:
    get_llm_gateway = None  # type: ignore[assignment,misc]
    _HAS_GATEWAY = False

# ---------------------------------------------------------------------------
# Document type weights for source quality scoring
# ---------------------------------------------------------------------------
_DOC_TYPE_WEIGHTS: dict[str, float] = {
    "specification": 1.0,
    "spec": 1.0,
    "specs": 1.0,
    "technical_specification": 1.0,
    "drawing": 0.9,
    "drawings": 0.9,
    "blueprint": 0.9,
    "shop_drawing": 0.85,
    "submittal": 0.8,
    "contract": 0.8,
    "rfi": 0.75,
    "change_order": 0.75,
    "meeting_minutes": 0.7,
    "correspondence": 0.6,
    "email": 0.5,
    "photo": 0.4,
    "report": 0.7,
    "daily_report": 0.65,
    "inspection_report": 0.8,
}


CONSTRUCTION_RAG_PROMPT = """\
You are ConstructAI, an expert assistant for the Architecture, Engineering, and \
Construction (AEC) industry. Your role is to answer questions accurately using \
ONLY the provided context from construction project documents.

RULES:
1. Base your answer strictly on the provided context. Do NOT hallucinate or \
invent information.
2. If the context does not contain enough information to answer the question, \
say so explicitly.
3. When referencing information, cite the source document title and page number \
in square brackets, e.g. [Document Title, p. 12].
4. Use precise construction terminology (CSI divisions, specification sections, \
code references) when relevant.
5. For quantities, measurements, or specifications, quote the exact values from \
the documents.

RESPONSE FORMAT:
Return your response as valid JSON with the following structure:
{
  "answer": "Your detailed answer here with [Source, p. X] citations inline.",
  "confidence": 0.85,
  "sources": [
    {"document_title": "...", "page_number": ..., "section": "..."}
  ]
}

- "confidence" is a float between 0.0 and 1.0 reflecting how well the context \
supports your answer.
- "sources" lists every document you referenced.
"""


def _build_context_block(context_chunks: list[dict]) -> str:
    """Format retrieved chunks into a numbered context block for the LLM."""
    if not context_chunks:
        return "(No relevant context available.)"

    parts: list[str] = []
    for i, chunk in enumerate(context_chunks, start=1):
        header_parts: list[str] = []
        # SECURITY (C-03): Sanitize ALL chunk metadata fields, not just content.
        # Document titles, section hierarchies, and CSI sections are user-controlled
        # (e.g. from uploaded document metadata) and can contain prompt injection.
        if chunk.get("document_title"):
            safe_title = sanitize_for_prompt(str(chunk["document_title"]), max_length=200)
            header_parts.append(f"Document: {safe_title}")
        if chunk.get("page_number") is not None:
            header_parts.append(f"Page: {chunk['page_number']}")
        if chunk.get("section_hierarchy"):
            hierarchy = chunk["section_hierarchy"]
            if isinstance(hierarchy, list):
                safe_hierarchy = sanitize_for_prompt(
                    " > ".join(str(s) for s in hierarchy), max_length=300
                )
            else:
                safe_hierarchy = sanitize_for_prompt(str(hierarchy), max_length=300)
            header_parts.append(f"Section: {safe_hierarchy}")
        if chunk.get("csi_section"):
            safe_csi = sanitize_for_prompt(str(chunk["csi_section"]), max_length=50)
            header_parts.append(f"CSI: {safe_csi}")

        header = " | ".join(header_parts) if header_parts else "Unknown Source"
        sanitized_content = sanitize_for_prompt(chunk.get("content", ""), max_length=4000)
        parts.append(
            f"[{i}] {header}\n<retrieved_document>{sanitized_content}</retrieved_document>"
        )

    return "\n\n---\n\n".join(parts)


def _parse_llm_response(raw_text: str, model_used: str) -> dict:
    """Parse the LLM's JSON response, falling back to plain text if needed."""
    # Try to extract JSON from the response (the LLM may wrap it in markdown
    # code fences).
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # Strip markdown code fences
        lines = cleaned.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        # SECURITY [M-20]: Clamp LLM self-reported confidence to 0.90 max.
        # LLM self-reported confidence should NEVER be the sole factor for
        # auto-approval routing decisions. The guardrails pipeline's
        # confidence scorer (Stage 5) and routing decision (Stage 6) should
        # combine multiple signals (schema validation, domain rules, knowledge
        # verification) rather than relying on what the model claims.
        # Previous cap was 0.95, tightened to 0.90 to add safety margin.
        raw_conf = float(data.get("confidence", 0.5))
        clamped_conf = max(0.0, min(0.90, raw_conf))
        return {
            "answer": data.get("answer", cleaned),
            "confidence": clamped_conf,
            "model_used": model_used,
            "sources": data.get("sources", []),
        }
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM response was not valid JSON; returning raw text.")
        return {
            "answer": raw_text.strip(),
            "confidence": 0.5,
            "model_used": model_used,
            "sources": [],
        }


def source_quality_score(chunk: dict) -> float:
    """Score a retrieved chunk based on recency, document type, and relevance.

    Returns a float between 0.0 and 1.0 where higher is better.

    Scoring components (weighted average):
    - Recency (30%): Documents updated/created more recently score higher.
      Full score for docs < 30 days old, linearly decaying to 0.2 at 2 years.
    - Document type (40%): Specs > drawings > submittals > correspondence.
    - Chunk relevance (30%): Uses the vector similarity score if available.

    Args:
        chunk: A retrieved chunk dict. May contain ``document_type``,
            ``created_at`` or ``updated_at`` (ISO strings or datetime),
            and ``relevance_score`` (float 0-1).

    Returns:
        Composite quality score between 0.0 and 1.0.
    """
    # -- Recency component (0.0 - 1.0) --
    recency_score = 0.5  # default when no date info
    date_str = chunk.get("updated_at") or chunk.get("created_at")
    if date_str:
        try:
            if isinstance(date_str, str):
                # Handle ISO format strings, with or without timezone
                doc_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            elif isinstance(date_str, datetime):
                doc_date = date_str
            else:
                doc_date = None

            if doc_date is not None:
                now = datetime.now(UTC)
                if doc_date.tzinfo is None:
                    doc_date = doc_date.replace(tzinfo=UTC)
                age_days = (now - doc_date).days
                if age_days <= 30:
                    recency_score = 1.0
                elif age_days >= 730:  # 2 years
                    recency_score = 0.2
                else:
                    # Linear decay from 1.0 to 0.2 over 30-730 days
                    recency_score = 1.0 - 0.8 * (age_days - 30) / 700
        except (ValueError, TypeError):
            recency_score = 0.5

    # -- Document type component (0.0 - 1.0) --
    doc_type = (chunk.get("document_type") or chunk.get("type") or "").lower().strip()
    type_score = _DOC_TYPE_WEIGHTS.get(doc_type, 0.5)

    # -- Relevance component (0.0 - 1.0) --
    relevance_score = chunk.get("relevance_score") or chunk.get("score")
    if relevance_score is not None:
        try:
            relevance_score = float(relevance_score)
            relevance_score = max(0.0, min(1.0, relevance_score))
        except (ValueError, TypeError):
            relevance_score = 0.5
    else:
        relevance_score = 0.5

    # Weighted composite
    composite = (0.30 * recency_score) + (0.40 * type_score) + (0.30 * relevance_score)
    return round(composite, 4)


async def _call_via_gateway(
    query: str,
    context_block: str,
    model: str,
    temperature: float,
    max_tokens: int,
    org_id: str | None = None,
) -> dict | None:
    """Attempt to generate an answer through the LLM Gateway.

    Returns the parsed result dict on success, or None if the gateway
    is unavailable or the call fails.
    """
    if not _HAS_GATEWAY:
        return None

    try:
        gateway = await get_llm_gateway()  # type: ignore[misc]
        sanitized_query = sanitize_for_prompt(query, max_length=2000)
        messages = [
            {"role": "system", "content": CONSTRUCTION_RAG_PROMPT},
            {
                "role": "user",
                "content": (
                    f"CONTEXT:\n{context_block}\n\n"
                    f"QUESTION:\n<user_query>{sanitized_query}</user_query>"
                ),
            },
        ]
        kwargs: dict = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if org_id:
            kwargs["org_id"] = org_id
        result = await gateway.complete(
            messages=messages,
            agent_name="rag_generator",
            **kwargs,
        )
        raw_text = result.get("content", "")
        model_used = result.get("model", model)
        return _parse_llm_response(raw_text, model_used=model_used)
    except Exception as exc:
        logger.warning("LLM Gateway call failed, falling back to direct LangChain: %s", exc)
        return None


async def generate_answer(
    query: str,
    context_chunks: list[dict],
    model: str = "gpt-4o",
    temperature: float = 0,
    max_tokens: int = 2048,
    org_id: str | None = None,
) -> dict:
    """Generate an answer using an LLM grounded on retrieved context chunks.

    The function first tries to route the request through the LLM Gateway
    (which provides fallback chains, cost tracking, and circuit breaking).
    If the gateway is unavailable or fails, it falls back to direct LangChain
    calls.

    Args:
        query: The user's natural-language question.
        context_chunks: Ranked retrieval results. Each dict should contain at
            minimum ``content``, and optionally ``document_title``,
            ``page_number``, ``section_hierarchy``, and ``csi_section``.
        model: LLM model identifier.  Supported prefixes:

            * ``gpt-*``  -- routes to OpenAI via ``ChatOpenAI``
            * ``claude-*`` -- routes to Anthropic via ``ChatAnthropic``

        temperature: Sampling temperature for the LLM (0 = deterministic).
        max_tokens: Maximum tokens in the LLM response.
        org_id: Optional organization ID for per-org usage tracking
            through the LLM gateway.

    Returns:
        Dict with keys: ``answer``, ``confidence``, ``model_used``, ``sources``.
    """
    # Graceful handling when there is no context to ground the answer.
    if not context_chunks:
        return {
            "answer": (
                "I don't have enough information in the project documents to "
                "answer this question. Please try refining your query or "
                "uploading additional relevant documents."
            ),
            "confidence": 0.0,
            "model_used": model,
            "sources": [],
        }

    context_block = _build_context_block(context_chunks)

    # --- Try the LLM Gateway first ---
    gateway_result = await _call_via_gateway(
        query=query,
        context_block=context_block,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        org_id=org_id,
    )
    if gateway_result is not None:
        return gateway_result

    # --- Fallback: direct LangChain calls ---
    sanitized_query = sanitize_for_prompt(query, max_length=2000)
    user_message = (
        f"CONTEXT:\n{context_block}\n\nQUESTION:\n<user_query>{sanitized_query}</user_query>"
    )

    # Select the appropriate LangChain chat model.
    llm: Any
    if model.startswith("claude"):
        # Import lazily so the module works even if langchain_anthropic is not
        # installed (OpenAI is the default path).
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model_name=model, temperature=temperature, max_tokens=max_tokens)  # type: ignore[call-arg]
    else:
        llm = ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens)  # type: ignore[call-arg]

    messages = [
        SystemMessage(content=CONSTRUCTION_RAG_PROMPT),
        HumanMessage(content=user_message),
    ]

    try:
        import asyncio

        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=60.0)
        raw_text = response.content
        if isinstance(raw_text, list):
            raw_text = "".join(str(c) for c in raw_text)
        return _parse_llm_response(raw_text, model_used=model)
    except Exception as exc:
        logger.error("LLM generation failed (model=%s): %s", model, exc)
        return {
            "answer": ("An error occurred while generating the answer. Please try again later."),
            "confidence": 0.0,
            "model_used": model,
            "sources": [],
        }
