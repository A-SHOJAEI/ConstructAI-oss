"""Citation extraction and formatting for RAG answers."""

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def _text_similarity(a: str, b: str) -> float:
    """Compute a rough similarity ratio between two strings.

    Uses :class:`difflib.SequenceMatcher` which is fast enough for the short
    text comparisons needed during citation extraction.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def extract_citations(
    answer: str,
    context_chunks: list[dict],
) -> list[dict]:
    """Match referenced content in the generated answer back to source chunks.

    The function uses two complementary strategies:

    1. **Explicit bracket references** -- Looks for patterns like
       ``[Document Title, p. 12]`` that were requested by the system prompt and
       matches them to chunks by document title and page number.
    2. **Content overlap** -- For each context chunk, measures how much of its
       content appears verbatim (or near-verbatim) in the answer.  Chunks with
       a similarity score above a threshold are included.

    Args:
        answer: The generated answer text.
        context_chunks: The context chunks that were fed to the LLM.

    Returns:
        Deduplicated list of citation dicts, each containing:
        ``chunk_id``, ``document_name``, ``page_number``, ``section``,
        ``relevance_score``.
    """
    if not answer or not context_chunks:
        return []

    cited: dict[str, dict] = {}  # keyed by chunk_id for dedup

    # --- Strategy 1: explicit bracket references ---
    # Pattern matches "[Document Title, p. 12]" or "[Document Title, p.12]"
    bracket_refs = re.findall(
        r"\[([^\]]+?),\s*p\.?\s*(\d+)\]",
        answer,
        flags=re.IGNORECASE,
    )

    for ref_title, ref_page in bracket_refs:
        ref_page_int = int(ref_page)
        ref_title_lower = ref_title.strip().lower()

        for chunk in context_chunks:
            cid = chunk.get("chunk_id", "")
            doc_title = (chunk.get("document_title") or "").lower()
            page = chunk.get("page_number")

            if cid in cited:
                continue

            title_match = ref_title_lower in doc_title or doc_title in ref_title_lower
            page_match = page is not None and page == ref_page_int

            if title_match and page_match:
                section = _format_section(chunk)
                cited[cid] = {
                    "chunk_id": cid,
                    "document_name": chunk.get("document_title", "Unknown"),
                    "page_number": page,
                    "section": section,
                    "relevance_score": 1.0,
                }

    # --- Strategy 2: content overlap ---
    # Raised from 0.3 to reduce false attributions
    similarity_threshold = 0.45

    for chunk in context_chunks:
        cid = chunk.get("chunk_id", "")
        if cid in cited:
            continue

        content = chunk.get("content", "")
        if not content:
            continue

        # Check if meaningful fragments of the chunk appear in the answer.
        # Use sentences from the chunk as probes.
        sentences = _split_sentences(content)
        max_sim = 0.0
        for sentence in sentences:
            if len(sentence) < 20:
                continue
            sim = _text_similarity(sentence, answer)
            max_sim = max(max_sim, sim)

        # Also check overall content similarity as a fallback.
        overall_sim = _text_similarity(content[:500], answer[:2000])
        max_sim = max(max_sim, overall_sim)

        if max_sim >= similarity_threshold:
            section = _format_section(chunk)
            cited[cid] = {
                "chunk_id": cid,
                "document_name": chunk.get("document_title", "Unknown"),
                "page_number": chunk.get("page_number"),
                "section": section,
                "relevance_score": round(max_sim, 4),
            }

    # Sort by relevance descending
    citations = sorted(cited.values(), key=lambda c: c["relevance_score"], reverse=True)
    return citations


def _format_section(chunk: dict) -> str:
    """Build a human-readable section string from a chunk dict."""
    parts: list[str] = []

    hierarchy = chunk.get("section_hierarchy")
    if hierarchy:
        if isinstance(hierarchy, list):
            parts.append(" > ".join(str(s) for s in hierarchy))
        else:
            parts.append(str(hierarchy))

    csi = chunk.get("csi_section")
    if csi:
        parts.append(f"CSI {csi}")

    return " | ".join(parts) if parts else ""


def _split_sentences(text: str) -> list[str]:
    """Naively split text into sentences on period/question/exclamation marks."""
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def format_citations_markdown(citations: list[dict]) -> str:
    """Format a list of citation dicts as Markdown footnotes.

    Example output::

        **Sources:**

        [^1]: *Structural Specifications*, p. 42 -- Section 03 30 00 > Cast-in-Place Concrete
        [^2]: *Site Plan Rev B*, p. 7

    Args:
        citations: List of citation dicts as returned by
            :func:`extract_citations`.

    Returns:
        A Markdown string with footnote-style citations, or an empty string
        if there are no citations.
    """
    if not citations:
        return ""

    lines: list[str] = ["**Sources:**", ""]
    for i, cite in enumerate(citations, start=1):
        doc_name = cite.get("document_name", "Unknown")
        page = cite.get("page_number")
        section = cite.get("section", "")

        ref = f"[^{i}]: *{doc_name}*"
        if page is not None:
            ref += f", p. {page}"
        if section:
            ref += f" -- {section}"

        lines.append(ref)

    return "\n".join(lines)
