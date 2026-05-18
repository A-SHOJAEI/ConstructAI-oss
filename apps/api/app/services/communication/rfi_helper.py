"""AI-assisted RFI response suggestions."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def suggest_rfi_response(
    subject: str,
    question: str,
    project_context: dict | None = None,
    *,
    db: AsyncSession | None = None,
    project_id: str | uuid.UUID | None = None,
) -> dict:
    """Suggest a response for an RFI using project context and RAG search.

    Parameters
    ----------
    subject: RFI subject line
    question: The RFI question text
    project_context: Optional dict with project documents (legacy)
    db: Optional database session for RAG search
    project_id: Optional project ID for scoped search

    Returns dict with suggested_response and references.
    """
    references: list[str] = []
    rag_snippets: list[str] = []

    # Try RAG search if db and project_id are available
    if db is not None and project_id is not None:
        try:
            from app.services.rag.embeddings import embed_query
            from app.services.rag.retrieval import hybrid_search

            query_text = f"{subject} {question}"
            query_embedding = await embed_query(query_text)
            # Coerce project_id to UUID for mypy; hybrid_search accepts str at runtime.
            pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
            results = await hybrid_search(
                db=db,
                query=query_text,
                query_embedding=query_embedding,
                project_id=pid,
                limit=5,
            )
            for result in results:
                ref_title = result.get("title") or result.get("document_title", "Document")
                references.append(ref_title)
                snippet = result.get("content", "")[:300]
                if snippet:
                    rag_snippets.append(snippet)
        except Exception:
            logger.warning("RAG search failed for RFI suggestion, using fallback", exc_info=True)

    # Fall back to legacy project_context if no RAG results
    if not references:
        context = project_context or {}
        specs = context.get("specifications", [])
        drawings = context.get("drawings", [])
        if specs:
            references.extend([f"Spec: {s}" for s in specs[:3]])
        if drawings:
            references.extend([f"Drawing: {d}" for d in drawings[:3]])

    # Build suggested response
    if rag_snippets:
        suggested = (
            f"Regarding '{subject}': Based on project documentation review, "
            f"the following information addresses the question.\n\n"
        )
        for i, snippet in enumerate(rag_snippets[:3], 1):
            suggested += f"[{i}] {snippet}\n\n"
        suggested += "Please confirm this interpretation aligns with design intent."
        confidence = min(0.85, 0.5 + len(rag_snippets) * 0.1)
    else:
        suggested = (
            f"Regarding '{subject}': Based on project "
            f"documentation review, the following "
            f"information addresses the question. "
        )
        if references:
            suggested += "Please refer to: " + ", ".join(references) + ". "
        suggested += "Please confirm this interpretation aligns with design intent."
        confidence = 0.7 if references else 0.3

    logger.info("RFI response suggested for: %s", subject)

    return {
        "suggested_response": suggested,
        "references": references,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# CSI MasterFormat division lookup (simplified keyword matching)
# ---------------------------------------------------------------------------

_CSI_DIVISIONS: list[tuple[str, str, list[str]]] = [
    ("01", "General Requirements", ["submittal", "schedule", "coordination", "general"]),
    ("02", "Existing Conditions", ["demolition", "existing", "abatement", "survey"]),
    ("03", "Concrete", ["concrete", "rebar", "reinforcement", "formwork", "slab", "footing"]),
    ("04", "Masonry", ["masonry", "brick", "block", "mortar", "grout"]),
    ("05", "Metals", ["steel", "metal", "beam", "column", "joist", "welding"]),
    ("06", "Wood, Plastics, and Composites", ["wood", "lumber", "framing", "casework", "millwork"]),
    (
        "07",
        "Thermal and Moisture",
        ["roofing", "insulation", "waterproofing", "membrane", "flashing"],
    ),
    ("08", "Openings", ["door", "window", "glazing", "hardware", "curtain wall"]),
    ("09", "Finishes", ["drywall", "paint", "tile", "flooring", "ceiling", "plaster"]),
    ("10", "Specialties", ["signage", "locker", "partition", "fire extinguisher"]),
    ("11", "Equipment", ["equipment", "kitchen", "appliance"]),
    ("12", "Furnishings", ["furniture", "cabinet", "countertop"]),
    ("13", "Special Construction", ["clean room", "swimming pool", "special"]),
    ("14", "Conveying Equipment", ["elevator", "escalator", "conveyor"]),
    ("21", "Fire Suppression", ["sprinkler", "fire suppression", "standpipe"]),
    ("22", "Plumbing", ["plumbing", "pipe", "fixture", "sanitary", "drain"]),
    ("23", "HVAC", ["hvac", "mechanical", "duct", "air handler", "chiller", "boiler"]),
    ("26", "Electrical", ["electrical", "wiring", "panel", "conduit", "lighting"]),
    ("27", "Communications", ["data", "telecom", "communication", "cable", "network"]),
    ("28", "Electronic Safety", ["fire alarm", "security", "access control"]),
    ("31", "Earthwork", ["excavation", "grading", "fill", "earthwork", "soil"]),
    ("32", "Exterior Improvements", ["paving", "landscape", "sidewalk", "parking"]),
    ("33", "Utilities", ["utility", "storm", "sewer", "water main", "underground"]),
]


async def suggest_spec_section(subject: str, question: str) -> dict:
    """Suggest a CSI MasterFormat spec section based on RFI content.

    Returns dict with spec_section, confidence, and reasoning.
    """
    combined = f"{subject} {question}".lower()

    matches: list[tuple[str, str, int]] = []
    for div_num, div_name, keywords in _CSI_DIVISIONS:
        hits = sum(1 for kw in keywords if kw in combined)
        if hits:
            matches.append((div_num, div_name, hits))

    if not matches:
        return {
            "spec_section": None,
            "confidence": 0.0,
            "reasoning": "No matching CSI division identified from keywords.",
        }

    matches.sort(key=lambda m: m[2], reverse=True)
    best = matches[0]
    confidence = min(0.9, 0.3 + best[2] * 0.2)

    return {
        "spec_section": f"Division {best[0]} - {best[1]}",
        "confidence": confidence,
        "reasoning": f"Matched {best[2]} keyword(s) for {best[1]}.",
    }


# Impact keywords
_COST_KEYWORDS = [
    "change order",
    "additional cost",
    "extra work",
    "premium",
    "upgrade",
    "material cost",
    "price",
    "budget",
    "expensive",
    "substitution",
    "alternate",
    "value engineering",
]
_SCHEDULE_KEYWORDS = [
    "delay",
    "schedule",
    "lead time",
    "expedite",
    "critical path",
    "behind schedule",
    "postpone",
    "reschedule",
    "long lead",
    "procurement",
    "backorder",
    "timeline",
]


async def assess_impact(subject: str, question: str) -> dict:
    """Assess potential cost and schedule impact of an RFI.

    Returns dict with cost_impact, schedule_impact, estimates, and confidence.
    """
    combined = f"{subject} {question}".lower()

    cost_hits = sum(1 for kw in _COST_KEYWORDS if kw in combined)
    schedule_hits = sum(1 for kw in _SCHEDULE_KEYWORDS if kw in combined)

    cost_impact = cost_hits >= 1
    schedule_impact = schedule_hits >= 1
    confidence = min(0.85, 0.2 + (cost_hits + schedule_hits) * 0.15)

    return {
        "cost_impact": cost_impact,
        "schedule_impact": schedule_impact,
        "cost_estimate": None,  # Would need project-specific data
        "schedule_estimate": None,
        "confidence": confidence,
        "reasoning": (
            f"Detected {cost_hits} cost keyword(s) and {schedule_hits} schedule keyword(s)."
        ),
    }
