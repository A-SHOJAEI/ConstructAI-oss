"""RFI Resolution Agent — 3-stage LangGraph pipeline.

Stage 1: Unnecessary RFI Detection
    Searches specs, historical RFIs, and meeting minutes to determine if
    the RFI is already answered in existing project documents.

Stage 2: AI-Assisted Response Drafting
    Retrieves relevant context (RAG chunks + OSHA standards for safety
    topics), generates a draft response via the construction-domain LLM
    prompt, and scores confidence.

Stage 3: Response Verification
    Validates the draft for hallucinations (cited sections must exist),
    contradictions (cross-reference conflicting sources), and completeness
    (all sub-questions addressed).  Labels output as "AI-ASSISTED DRAFT".
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Similarity thresholds for "unnecessary" RFI detection
# ---------------------------------------------------------------------------
_RFI_SIMILARITY_THRESHOLD = 0.90
_SPEC_SIMILARITY_THRESHOLD = 0.92
_MEETING_SIMILARITY_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# Safety-related keyword detection
# SECURITY [H-14]: Expanded keyword set with regex word-boundary matching
# to prevent trivial bypasses of safety routing.
# ---------------------------------------------------------------------------

# Multi-word phrases checked via substring match (case-insensitive)
_SAFETY_PHRASES: set[str] = {
    "fall protection",
    "fall arrest",
    "fall prevention",
    "fall hazard",
    "fall restraint",
    "leading edge",
    "confined space",
    "permit required confined space",
    "competent person",
    "qualified person",
    "fire protection",
    "fire watch",
    "fire extinguisher",
    "fire prevention",
    "fire alarm",
    "fire suppression",
    "fire rated",
    "electrical safety",
    "arc flash",
    "arc fault",
    "ground fault",
    "electrical hazard",
    "energized work",
    "de-energize",
    "de-energized",
    "hot work",
    "hot work permit",
    "crane lift plan",
    "lift plan",
    "critical lift",
    "lead paint",
    "lead abatement",
    "lead exposure",
    "safety data sheet",
    "safety plan",
    "safety rail",
    "safety net",
    "safety harness",
    "safety factor",
    "safety cable",
    "personal protective",
    "respiratory protection",
    "hearing protection",
    "eye protection",
    "head protection",
    "hand protection",
    "heat illness",
    "heat stress",
    "heat stroke",
    "cold stress",
    "hazard analysis",
    "job hazard analysis",
    "job safety analysis",
    "activity hazard analysis",
    "hazardous material",
    "hazardous waste",
    "hazardous atmosphere",
    "hazardous energy",
    "hazardous substance",
    "danger zone",
    "exclusion zone",
    "barricade tape",
    "cave-in",
    "cave in",
    "trench box",
    "shoring system",
    "sloping and benching",
    "emergency action plan",
    "emergency evacuation",
    "emergency response",
    "exposure limit",
    "permissible exposure",
    "threshold limit",
    "lockout tagout",
    "lockout/tagout",
    "lock out tag out",
    "life safety",
    "means of egress",
}

# Single-word or short terms checked via regex word boundaries
_SAFETY_WORD_PATTERNS: set[str] = {
    "osha",
    "safety",
    "guardrail",
    "guardrails",
    "handrail",
    "handrails",
    "harness",
    "lanyard",
    "lifeline",
    "scaffold",
    "scaffolding",
    "scaffolds",
    "excavation",
    "excavations",
    "trench",
    "trenching",
    "trenches",
    "hazmat",
    "hazardous",
    "crane",
    "cranes",
    "rigging",
    "demolition",
    "ppe",
    "lockout",
    "tagout",
    "loto",
    "welding",
    "silica",
    "asbestos",
    "respirator",
    "respiratory",
    "hardhat",
    "hard-hat",
    "hi-vis",
    "high-vis",
    "barricade",
    "shoring",
    "underpinning",
    "dewatering",
    "blasting",
    "explosive",
    "explosives",
    "flammable",
    "combustible",
    "voltage",
    "electrocution",
    "engulfment",
    "asphyxiation",
    "suffocation",
    "struck-by",
    "caught-in",
    "caught-between",
    "fatality",
    "fatalities",
    "incident",
    "injury",
    "injuries",
    "citation",
    "violation",
    "non-compliance",
    "noncompliance",
    "abatement",
    "msds",
    "sds",
    "ghs",
    "ansi",
    "nfpa",
    "niosh",
    "msha",
    "osha1926",
    "1926",
    "1910",
    "cfr",
}

# Pre-compiled regex for word-boundary matching of single-word patterns
_SAFETY_WORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in sorted(_SAFETY_WORD_PATTERNS)) + r")\b",
    re.IGNORECASE,
)


def _is_safety_related(text: str) -> bool:
    """Check whether an RFI question relates to safety/OSHA topics.

    SECURITY [H-14]: Uses regex word-boundary matching to prevent trivial
    bypasses (e.g. embedding safety terms inside other words). Also checks
    multi-word phrases via substring match.
    """
    # SECURITY: Normalize Unicode to NFKC before keyword matching to prevent
    # bypasses using fullwidth characters, combining marks, or homoglyphs
    # (e.g. "ｓａｆｅｔｙ" or "saf\u0065ty" evading detection).
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    lower = text.lower()
    # Check multi-word phrases first (substring match is appropriate here)
    if any(phrase in lower for phrase in _SAFETY_PHRASES):
        return True
    # Check single-word patterns with word-boundary regex
    return bool(_SAFETY_WORD_RE.search(text))


# ---------------------------------------------------------------------------
# Sub-question extraction
# ---------------------------------------------------------------------------

_QUESTION_SPLIT_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+[\.\)]\s*|[-•]\s*|[a-zA-Z][\.\)]\s*)",
)


def _extract_sub_questions(question: str) -> list[str]:
    """Split a multi-part RFI question into individual sub-questions."""
    parts = _QUESTION_SPLIT_RE.split(question)
    subs = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
    return subs if len(subs) > 1 else [question.strip()]


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class RFIResolutionState(TypedDict):
    """State schema for the RFI resolution pipeline."""

    # --- Input ---
    rfi_id: str
    project_id: str
    subject: str
    question: str
    spec_section: str | None
    drawing_reference: str | None

    # --- Stage 1 outputs ---
    similar_rfis: list[dict]
    spec_matches: list[dict]
    meeting_matches: list[dict]
    is_unnecessary: bool
    unnecessary_reason: str | None
    unnecessary_source: str | None  # "rfi" | "spec" | "meeting"
    safety_override: bool | None  # SEC: safety RFIs bypass "unnecessary" determination

    # --- Stage 2 outputs ---
    context_chunks: list[dict]
    osha_chunks: list[dict]
    draft_response: str | None
    draft_confidence: float
    draft_sources: list[dict]
    draft_model: str | None

    # --- Stage 3 outputs ---
    hallucination_flags: list[dict]
    contradiction_flags: list[dict]
    completeness_flags: list[dict]
    verification_passed: bool
    final_response: str | None

    # --- Control ---
    stage_reached: int
    status: str
    error: str | None


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1 — Unnecessary RFI Detection
# ═══════════════════════════════════════════════════════════════════════════


async def search_existing_sources_node(state: RFIResolutionState) -> dict:
    """Search historical RFIs, specifications, and meeting minutes."""
    try:
        from app.database import async_session
        from app.services.rag.embeddings import embed_query
        from app.services.rag.retrieval import (
            hybrid_search,
            search_similar_rfis,
        )

        project_id = uuid.UUID(state["project_id"])
        question = state["question"]

        async with async_session() as db:
            # 1. Search similar historical RFIs
            similar_rfis = await search_similar_rfis(
                db,
                question,
                project_id,
                similarity_threshold=_RFI_SIMILARITY_THRESHOLD,
                limit=5,
            )

            # 2. Search project specifications and documents via hybrid search
            query_embedding = await embed_query(question)
            spec_matches = await hybrid_search(
                db,
                question,
                query_embedding,
                project_id,
                limit=10,
            )

            # 3. Search meeting minutes (keyword search in document chunks
            #    where parent doc type is meeting_minutes)
            from sqlalchemy import text as sa_text

            meeting_stmt = sa_text("""
                SELECT
                    dc.id AS chunk_id,
                    dc.content AS content,
                    d.title AS document_title,
                    d.id AS document_id,
                    similarity(dc.content, :query) AS score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE d.project_id = :project_id
                  AND d.type IN ('meeting_minutes', 'daily_report')
                  AND (dc.content % :query OR dc.content ILIKE :pattern)
                ORDER BY similarity(dc.content, :query) DESC
                LIMIT 5
            """)
            question_escaped = (
                question[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            result = await db.execute(
                meeting_stmt,
                {
                    "query": question,
                    "project_id": str(project_id),
                    "pattern": f"%{question_escaped}%",
                },
            )
            meeting_rows = result.mappings().all()
            meeting_matches = [
                {
                    "chunk_id": str(r["chunk_id"]),
                    "content": r["content"],
                    "document_title": r["document_title"],
                    "document_id": str(r["document_id"]),
                    "score": float(r["score"]),
                }
                for r in meeting_rows
            ]

        return {
            "similar_rfis": similar_rfis,
            "spec_matches": spec_matches,
            "meeting_matches": meeting_matches,
        }
    except Exception as exc:
        logger.error("Source search failed: %s", exc)
        return {
            "similar_rfis": [],
            "spec_matches": [],
            "meeting_matches": [],
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Source search failed due to an internal error",
        }


def _apply_safety_override(update: dict, question: str) -> dict:
    """Safety-related RFIs are never auto-resolved as unnecessary.

    When ``update`` flags the RFI as unnecessary but the question is
    safety-related, flip ``is_unnecessary`` back to ``False`` and record
    ``safety_override=True`` so downstream nodes/human reviewers can see
    why it wasn't auto-resolved.
    """
    if update.get("is_unnecessary") and _is_safety_related(question):
        logger.info(
            "Safety-related RFI marked as unnecessary — overriding to "
            "proceed to Stage 2 for human review"
        )
        update = {
            **update,
            "is_unnecessary": False,
            "safety_override": True,
        }
    return update


async def evaluate_unnecessary_node(state: RFIResolutionState) -> dict:
    """Evaluate whether the RFI is already answered by existing sources."""
    question = state.get("question", "")
    try:
        # Check 1: Highly similar past RFI with an answer
        for rfi in state.get("similar_rfis", []):
            if rfi.get("similarity_score", 0) >= _RFI_SIMILARITY_THRESHOLD and rfi.get("answer"):
                return _apply_safety_override(
                    {
                        "is_unnecessary": True,
                        "unnecessary_reason": (
                            f"RFI {rfi.get('rfi_number', '?')} ({rfi['subject']}) "
                            f"asked the same question and was answered: "
                            f'"{rfi["answer"][:200]}..."'
                        ),
                        "unnecessary_source": "rfi",
                        "stage_reached": 1,
                    },
                    question,
                )

        # Check 2: Spec match with high relevance that directly addresses
        #          the question
        spec_matches = state.get("spec_matches") or []
        top_spec: dict | None = spec_matches[0] if spec_matches else None
        if top_spec and top_spec.get("score", 0) >= _SPEC_SIMILARITY_THRESHOLD:
            content_preview = top_spec.get("content", "")[:300]
            return _apply_safety_override(
                {
                    "is_unnecessary": True,
                    "unnecessary_reason": (
                        f"The answer appears to be in "
                        f"{top_spec.get('document_title', 'project documents')}"
                        f" (CSI {top_spec.get('csi_section', 'N/A')}): "
                        f'"{content_preview}..."'
                    ),
                    "unnecessary_source": "spec",
                    "stage_reached": 1,
                },
                question,
            )

        # Check 3: Meeting minutes with high relevance
        for meeting in state.get("meeting_matches", []):
            if meeting.get("score", 0) >= _MEETING_SIMILARITY_THRESHOLD:
                return _apply_safety_override(
                    {
                        "is_unnecessary": True,
                        "unnecessary_reason": (
                            f"This was discussed in "
                            f"{meeting.get('document_title', 'meeting minutes')}: "
                            f'"{meeting["content"][:200]}..."'
                        ),
                        "unnecessary_source": "meeting",
                        "stage_reached": 1,
                    },
                    question,
                )

        # Not unnecessary — proceed to Stage 2
        return {
            "is_unnecessary": False,
            "unnecessary_reason": None,
            "unnecessary_source": None,
            "safety_override": None,
            "stage_reached": 1,
        }
    except Exception as exc:
        logger.error("Unnecessary evaluation failed: %s", exc)
        return {
            "is_unnecessary": False,
            "unnecessary_reason": None,
            "unnecessary_source": None,
            "safety_override": None,
            "stage_reached": 1,
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Evaluation failed due to an internal error",
        }


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2 — AI-Assisted Response Drafting
# ═══════════════════════════════════════════════════════════════════════════


async def retrieve_context_node(state: RFIResolutionState) -> dict:
    """Retrieve top RAG chunks + OSHA standards for response generation."""
    try:
        from app.database import async_session
        from app.services.rag.embeddings import embed_query
        from app.services.rag.retrieval import (
            hybrid_search,
            search_osha_standards,
        )

        project_id = uuid.UUID(state["project_id"])
        question = state["question"]

        async with async_session() as db:
            # Get top 10 RAG context chunks
            query_embedding = await embed_query(question)
            context_chunks = await hybrid_search(
                db,
                question,
                query_embedding,
                project_id,
                limit=10,
            )

            # If safety-related, also search OSHA standards.
            # M-15: resolve org_id from project_id so the OSHA search is
            # correctly scoped (system content + this org's safety docs).
            osha_chunks: list[dict] = []
            if _is_safety_related(question):
                from sqlalchemy import text as _text

                org_row = await db.execute(
                    _text("SELECT org_id FROM projects WHERE id = :pid"),
                    {"pid": str(project_id)},
                )
                org_id = org_row.scalar_one_or_none()
                osha_chunks = await search_osha_standards(
                    db,
                    question,
                    query_embedding,
                    limit=5,
                    org_id=org_id,
                )

        return {
            "context_chunks": context_chunks,
            "osha_chunks": osha_chunks,
        }
    except Exception as exc:
        logger.error("Context retrieval failed: %s", exc)
        return {
            "context_chunks": state.get("spec_matches", []),
            "osha_chunks": [],
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Context retrieval failed due to an internal error",
        }


_RFI_DRAFT_PROMPT = """\
You are ConstructAI, an expert assistant for the Architecture, Engineering, and \
Construction (AEC) industry. You are drafting a response to a Request for \
Information (RFI).

RULES:
1. Base your answer strictly on the provided context from project documents \
and standards. Do NOT hallucinate or invent information.
2. If the context does not contain enough information, say so explicitly and \
recommend what additional information is needed.
3. Cite every source using [Document Title, p. X] format.
4. Use precise construction terminology (CSI divisions, specification sections, \
OSHA standards) when relevant.
5. For quantities, measurements, or specifications, quote exact values.
6. Address ALL parts of the question if it contains multiple sub-questions.
7. If OSHA standards apply, cite the specific regulation (e.g., 29 CFR 1926.502).

RESPONSE FORMAT:
Return your response as valid JSON:
{
  "answer": "Your detailed RFI response with [Source, p. X] citations.",
  "confidence": 0.85,
  "sources": [
    {"document_title": "...", "page_number": ..., "section": "..."}
  ],
  "requires_expert": false,
  "expert_reason": null
}
"""


async def generate_draft_node(state: RFIResolutionState) -> dict:
    """Generate a draft RFI response using the RAG pipeline."""
    try:
        from app.services.rag.generator import (
            _build_context_block,
            _parse_llm_response,
        )

        # Combine context chunks and OSHA chunks
        all_chunks = list(state.get("context_chunks", []))
        for osha in state.get("osha_chunks", []):
            all_chunks.append(
                {
                    "content": osha.get("content", ""),
                    "document_title": f"OSHA {osha.get('standard_number', '')}",
                    "page_number": None,
                    "section_hierarchy": osha.get("topic", ""),
                    "csi_section": None,
                }
            )

        if not all_chunks:
            return {
                "draft_response": (
                    "Insufficient project documentation to draft a response. "
                    "Please consult the design team directly."
                ),
                "draft_confidence": 0.0,
                "draft_sources": [],
                "draft_model": None,
                "stage_reached": 2,
            }

        # M-21: The context block is built from RAG chunks which are system-
        # curated, but defense-in-depth: re-run sanitize_for_prompt on the
        # assembled block so any prompt-injection markers that slipped into
        # the DB (e.g. from a malicious document upload) are neutralized
        # before they reach the LLM.
        context_block = sanitize_for_prompt(_build_context_block(all_chunks), max_length=16000)
        safe_subject = sanitize_for_prompt(state["subject"], max_length=500)
        safe_question = sanitize_for_prompt(state["question"], max_length=4000)
        user_message = (
            f"CONTEXT:\n{context_block}\n\n"
            f"RFI SUBJECT: <user_input>{safe_subject}</user_input>\n"
            f"RFI QUESTION:\n<user_input>{safe_question}</user_input>"
        )

        spec_section = state.get("spec_section")
        if spec_section:
            safe_spec = sanitize_for_prompt(spec_section, max_length=200)
            user_message += f"\nREFERENCED SPEC SECTION: {safe_spec}"
        drawing_reference = state.get("drawing_reference")
        if drawing_reference:
            safe_drawing = sanitize_for_prompt(drawing_reference, max_length=200)
            user_message += f"\nREFERENCED DRAWING: {safe_drawing}"

        # Generate via LLM Gateway or LangChain fallback.
        # H-7: model name read from config so it can be swapped without a
        # code change (e.g. when OpenAI is down, operators can set
        # LLM_MODEL_RFI_AGENT to an Anthropic model via env).
        from app.config import settings as _cfg

        model = _cfg.LLM_MODEL_RFI_AGENT or "gpt-4o"
        fallback_model = _cfg.LLM_MODEL_FALLBACK or model
        try:
            from app.services.reliability.llm_gateway import get_llm_gateway

            gateway = await get_llm_gateway()
            result = await gateway.complete(
                messages=[
                    {"role": "system", "content": _RFI_DRAFT_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                agent_name="rfi_resolution",
                temperature=0,
                max_tokens=2048,
            )
            raw_text = result.get("content", "")
            model_used = result.get("model", model)
        except Exception:
            # Fallback to direct LangChain with the configured fallback model
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model=fallback_model, temperature=0, max_tokens=2048)  # type: ignore[call-arg]
            response = await llm.ainvoke(
                [
                    SystemMessage(content=_RFI_DRAFT_PROMPT),
                    HumanMessage(content=user_message),
                ]
            )
            raw_text = response.content
            if isinstance(raw_text, list):
                raw_text = "".join(str(c) for c in raw_text)
            model_used = fallback_model

        parsed = _parse_llm_response(raw_text, model_used=model_used)

        return {
            "draft_response": parsed.get("answer", raw_text),
            "draft_confidence": parsed.get("confidence", 0.5),
            "draft_sources": parsed.get("sources", []),
            "draft_model": model_used,
            "stage_reached": 2,
        }
    except Exception as exc:
        logger.error("Draft generation failed: %s", exc)
        return {
            "draft_response": None,
            "draft_confidence": 0.0,
            "draft_sources": [],
            "draft_model": None,
            "stage_reached": 2,
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Draft generation failed due to an internal error",
        }


# ═══════════════════════════════════════════════════════════════════════════
# Stage 3 — Response Verification
# ═══════════════════════════════════════════════════════════════════════════


_CITATION_RE = re.compile(r"\[([^\]]+?)(?:,\s*p\.\s*(\d+))?\]")


async def hallucination_check_node(state: RFIResolutionState) -> dict:
    """Verify that cited document titles and spec sections exist in context."""
    try:
        draft = state.get("draft_response", "") or ""
        flags: list[dict] = []

        # Extract all citations from the draft
        citations = _CITATION_RE.findall(draft)
        if not citations:
            return {"hallucination_flags": flags}

        # Build set of available source titles
        available_titles: set[str] = set()
        for chunk in state.get("context_chunks", []):
            title = chunk.get("document_title", "")
            if title:
                available_titles.add(title.lower().strip())
        for osha in state.get("osha_chunks", []):
            std_num = osha.get("standard_number", "")
            if std_num:
                available_titles.add(f"osha {std_num}".lower())

        # SECURITY [M-19]: Use token-level matching instead of weak substring
        # matching. Require at least 50% of citation words to appear in an
        # available title. This prevents false positives like "concrete" matching
        # "Concrete Specifications Section 03300 Rev 2".
        for cite_title, _page in citations:
            cite_lower = cite_title.lower().strip()
            cite_words = set(re.findall(r"\b[a-zA-Z0-9]{2,}\b", cite_lower))
            found = False
            if cite_words:
                for avail in available_titles:
                    avail_words = set(re.findall(r"\b[a-zA-Z0-9]{2,}\b", avail))
                    matched_words = cite_words & avail_words
                    # Require >= 50% of citation words to appear in the title
                    if len(matched_words) >= max(1, len(cite_words) * 0.5):
                        found = True
                        break
            if not found:
                flags.append(
                    {
                        "type": "hallucinated_source",
                        "citation": cite_title,
                        "severity": "warning",
                        "message": f"Cited source '{cite_title}' not found in retrieved context",
                    }
                )

        return {"hallucination_flags": flags}
    except Exception as exc:
        logger.error("Hallucination check failed: %s", exc)
        return {"hallucination_flags": []}


async def contradiction_check_node(state: RFIResolutionState) -> dict:
    """Search for contradicting information across specs and RFIs."""
    try:
        flags: list[dict] = []
        draft = state.get("draft_response", "") or ""

        if not draft:
            return {"contradiction_flags": flags}

        # Look for contradictions between the draft answer and similar RFI answers
        for rfi in state.get("similar_rfis", []):
            rfi_answer = rfi.get("answer", "")
            if not rfi_answer:
                continue

            # Simple heuristic: if a similar RFI has an answer that gives
            # different numeric values or directly opposing statements, flag it
            # Extract numbers from draft and RFI answer
            draft_numbers = set(
                re.findall(
                    r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|ft|in|psi|ksi|°[FC]|days?|hours?)\b",
                    draft.lower(),
                )
            )
            rfi_numbers = set(
                re.findall(
                    r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|ft|in|psi|ksi|°[FC]|days?|hours?)\b",
                    rfi_answer.lower(),
                )
            )

            # Check for same-unit different-value conflicts
            if draft_numbers and rfi_numbers:
                for dn in draft_numbers:
                    unit = re.search(r"[a-zA-Z°]+", dn)
                    if not unit:
                        continue
                    u = unit.group()
                    for rn in rfi_numbers:
                        if u in rn and dn != rn:
                            flags.append(
                                {
                                    "type": "numeric_contradiction",
                                    "draft_value": dn,
                                    "rfi_value": rn,
                                    "rfi_number": rfi.get("rfi_number", "?"),
                                    "severity": "warning",
                                    "message": (
                                        f"Draft states '{dn}' but RFI "
                                        f"{rfi.get('rfi_number', '?')} answered "
                                        f"with '{rn}'"
                                    ),
                                }
                            )

        return {"contradiction_flags": flags}
    except Exception as exc:
        logger.error("Contradiction check failed: %s", exc)
        return {"contradiction_flags": []}


async def completeness_check_node(state: RFIResolutionState) -> dict:
    """Verify all sub-questions in the RFI are addressed by the draft."""
    try:
        flags: list[dict] = []
        draft = state.get("draft_response", "") or ""
        question = state.get("question", "")

        if not draft or not question:
            return {"completeness_flags": flags}

        sub_questions = _extract_sub_questions(question)

        if len(sub_questions) <= 1:
            # Single question — check if draft is substantive
            if len(draft.strip()) < 50:
                flags.append(
                    {
                        "type": "insufficient_response",
                        "severity": "warning",
                        "message": "Draft response appears too brief to adequately address the question",
                    }
                )
            return {"completeness_flags": flags}

        # Multi-part question: check each sub-question is addressed
        draft_lower = draft.lower()
        for i, sub_q in enumerate(sub_questions, start=1):
            # Extract key terms from sub-question (words > 4 chars)
            key_terms = [
                w
                for w in re.findall(r"\b[a-zA-Z]{4,}\b", sub_q.lower())
                if w
                not in {
                    "what",
                    "where",
                    "when",
                    "which",
                    "does",
                    "have",
                    "will",
                    "should",
                    "could",
                    "would",
                    "about",
                    "this",
                    "that",
                    "these",
                    "those",
                    "with",
                    "from",
                    "been",
                    "being",
                    "they",
                    "their",
                    "there",
                }
            ]
            if not key_terms:
                continue

            # Count how many key terms appear in the draft
            matched = sum(1 for t in key_terms if t in draft_lower)
            coverage = matched / len(key_terms) if key_terms else 1.0

            if coverage < 0.3:
                flags.append(
                    {
                        "type": "unanswered_sub_question",
                        "sub_question_index": i,
                        "sub_question": sub_q[:100],
                        "coverage": round(coverage, 2),
                        "severity": "warning",
                        "message": f"Sub-question {i} may not be addressed: '{sub_q[:80]}...'",
                    }
                )

        return {"completeness_flags": flags}
    except Exception as exc:
        logger.error("Completeness check failed: %s", exc)
        return {"completeness_flags": []}


async def finalize_verification_node(state: RFIResolutionState) -> dict:
    """Combine verification results and produce the final labeled response."""
    try:
        hallucination_flags = state.get("hallucination_flags", [])
        contradiction_flags = state.get("contradiction_flags", [])
        completeness_flags = state.get("completeness_flags", [])

        all_flags = hallucination_flags + contradiction_flags + completeness_flags
        error_count = sum(1 for f in all_flags if f.get("severity") == "error")
        warning_count = sum(1 for f in all_flags if f.get("severity") == "warning")

        # Verification passes if no errors and few warnings
        passed = error_count == 0 and warning_count <= 2

        draft = state.get("draft_response", "")
        confidence = state.get("draft_confidence", 0.0)

        # Adjust confidence based on verification flags
        if error_count > 0:
            confidence = max(0.0, confidence - 0.2 * error_count)
        if warning_count > 0:
            confidence = max(0.0, confidence - 0.05 * warning_count)

        # Build final labeled response
        final_response = None
        if draft:
            warning_section = ""
            if all_flags:
                flag_bullets = "\n".join(f"  - {f['message']}" for f in all_flags)
                warning_section = f"\n\n--- VERIFICATION NOTES ---\n{flag_bullets}\n"

            final_response = (
                f"--- AI-ASSISTED DRAFT ---\n"
                f"Confidence: {confidence:.0%}\n\n"
                f"{draft}"
                f"{warning_section}\n\n"
                f"--- END AI-ASSISTED DRAFT ---\n"
                f"This response was generated by ConstructAI and requires "
                f"human review before being sent."
            )

        return {
            "verification_passed": passed,
            "final_response": final_response,
            "draft_confidence": round(confidence, 3),
            "stage_reached": 3,
            "status": "completed",
        }
    except Exception as exc:
        logger.error("Finalize verification failed: %s", exc)
        return {
            "verification_passed": False,
            "final_response": state.get("draft_response"),
            "stage_reached": 3,
            "status": "completed",
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Verification finalization failed due to an internal error",
        }


# ═══════════════════════════════════════════════════════════════════════════
# Conditional routing
# ═══════════════════════════════════════════════════════════════════════════


def _should_continue_to_stage2(state: RFIResolutionState) -> str:
    """After Stage 1: proceed to Stage 2 unless the RFI is unnecessary.

    Pure router — the safety override is applied inside
    ``evaluate_unnecessary_node`` so state is already correct here.
    Mutating state from a conditional-edge function corrupts LangGraph
    checkpoints.
    """
    return "end_unnecessary" if state.get("is_unnecessary") else "retrieve_context"


# ═══════════════════════════════════════════════════════════════════════════
# Graph construction
# ═══════════════════════════════════════════════════════════════════════════


def build_rfi_resolution_agent(checkpointer=None):
    """Build the 3-stage RFI resolution graph.

    Flow:
        search_existing_sources → evaluate_unnecessary
            → (if unnecessary) → END
            → (else) → retrieve_context → generate_draft
                → hallucination_check → contradiction_check
                → completeness_check → finalize_verification → END
    """
    workflow = StateGraph(RFIResolutionState)

    # Stage 1 nodes
    workflow.add_node("search_existing_sources", search_existing_sources_node)
    workflow.add_node("evaluate_unnecessary", evaluate_unnecessary_node)

    # Stage 2 nodes
    workflow.add_node("retrieve_context", retrieve_context_node)
    workflow.add_node("generate_draft", generate_draft_node)

    # Stage 3 nodes
    workflow.add_node("hallucination_check", hallucination_check_node)
    workflow.add_node("contradiction_check", contradiction_check_node)
    workflow.add_node("completeness_check", completeness_check_node)
    workflow.add_node("finalize_verification", finalize_verification_node)

    # Stage 1 flow
    workflow.set_entry_point("search_existing_sources")
    workflow.add_edge("search_existing_sources", "evaluate_unnecessary")

    # Conditional: unnecessary → END, else → Stage 2
    workflow.add_conditional_edges(
        "evaluate_unnecessary",
        _should_continue_to_stage2,
        {
            "end_unnecessary": END,
            "retrieve_context": "retrieve_context",
        },
    )

    # Stage 2 flow
    workflow.add_edge("retrieve_context", "generate_draft")

    # Stage 2 → Stage 3 flow
    workflow.add_edge("generate_draft", "hallucination_check")
    workflow.add_edge("hallucination_check", "contradiction_check")
    workflow.add_edge("contradiction_check", "completeness_check")
    workflow.add_edge("completeness_check", "finalize_verification")
    workflow.add_edge("finalize_verification", END)

    return workflow.compile(checkpointer=checkpointer)


# ═══════════════════════════════════════════════════════════════════════════
# Public entry points
# ═══════════════════════════════════════════════════════════════════════════


async def run_rfi_resolution(
    rfi_id: str | uuid.UUID,
    project_id: str | uuid.UUID,
    subject: str,
    question: str,
    spec_section: str | None = None,
    drawing_reference: str | None = None,
) -> dict:
    """Run the full 3-stage RFI resolution pipeline.

    Returns the final state dict with resolution results, always routed
    through human review per the routing_decision policy (rfi_draft auto=None).
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_rfi_resolution_agent(checkpointer=checkpointer)
    # SECURITY [L-09]: Use a cryptographically random thread ID to prevent
    # prediction or enumeration of LangGraph thread IDs.
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"rfi_resolution_{uuid.uuid4().hex}"}}
    )

    initial_state: RFIResolutionState = {
        "rfi_id": str(rfi_id),
        "project_id": str(project_id),
        "subject": subject,
        "question": question,
        "spec_section": spec_section,
        "drawing_reference": drawing_reference,
        "similar_rfis": [],
        "spec_matches": [],
        "meeting_matches": [],
        "is_unnecessary": False,
        "unnecessary_reason": None,
        "unnecessary_source": None,
        "safety_override": None,
        "context_chunks": [],
        "osha_chunks": [],
        "draft_response": None,
        "draft_confidence": 0.0,
        "draft_sources": [],
        "draft_model": None,
        "hallucination_flags": [],
        "contradiction_flags": [],
        "completeness_flags": [],
        "verification_passed": False,
        "final_response": None,
        "stage_reached": 0,
        "status": "processing",
        "error": None,
    }

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        if final_state.get("error") is None:
            if final_state.get("is_unnecessary"):
                final_state["status"] = "unnecessary"
            else:
                final_state["status"] = "completed"
        return final_state
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "rfi_resolution"})
        return {
            **initial_state,
            "status": "timeout",
            "error": "Agent execution timed out",
        }
    except Exception as exc:
        logger.error(
            "RFI resolution agent failed for %s: %s",
            rfi_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "RFI resolution pipeline failed due to an internal error",
        }


async def run_rfi_unnecessary_check(
    rfi_id: str | uuid.UUID,
    project_id: str | uuid.UUID,
    subject: str,
    question: str,
) -> dict:
    """Run only Stage 1 (unnecessary RFI detection).

    Lighter-weight check that can be triggered automatically on RFI creation.
    Returns dict with is_unnecessary, unnecessary_reason, unnecessary_source.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_rfi_resolution_agent(checkpointer=checkpointer)
    # SECURITY [L-09]: Use a cryptographically random thread ID.
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"rfi_unnecessary_{uuid.uuid4().hex}"}}
    )

    initial_state: RFIResolutionState = {
        "rfi_id": str(rfi_id),
        "project_id": str(project_id),
        "subject": subject,
        "question": question,
        "spec_section": None,
        "drawing_reference": None,
        "similar_rfis": [],
        "spec_matches": [],
        "meeting_matches": [],
        "is_unnecessary": False,
        "unnecessary_reason": None,
        "unnecessary_source": None,
        "safety_override": None,
        "context_chunks": [],
        "osha_chunks": [],
        "draft_response": None,
        "draft_confidence": 0.0,
        "draft_sources": [],
        "draft_model": None,
        "hallucination_flags": [],
        "contradiction_flags": [],
        "completeness_flags": [],
        "verification_passed": False,
        "final_response": None,
        "stage_reached": 0,
        "status": "processing",
        "error": None,
    }

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        return {
            "rfi_id": str(rfi_id),
            "is_unnecessary": final_state.get("is_unnecessary", False),
            "unnecessary_reason": final_state.get("unnecessary_reason"),
            "unnecessary_source": final_state.get("unnecessary_source"),
            "similar_rfis": final_state.get("similar_rfis", []),
            "status": "unnecessary" if final_state.get("is_unnecessary") else "novel",
        }
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "rfi_unnecessary_check"})
        return {
            "rfi_id": str(rfi_id),
            "is_unnecessary": False,
            "unnecessary_reason": None,
            "status": "timeout",
            "error": "Agent execution timed out",
        }
    except Exception as exc:
        logger.error("RFI unnecessary check failed for %s: %s", rfi_id, exc)
        return {
            "rfi_id": str(rfi_id),
            "is_unnecessary": False,
            "unnecessary_reason": None,
            "unnecessary_source": None,
            "safety_override": None,
            "similar_rfis": [],
            "status": "error",
            # SECURITY [M-27]: Generic error; full details logged above
            "error": "Unnecessary RFI check failed due to an internal error",
        }
