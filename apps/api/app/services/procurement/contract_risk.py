"""Contract risk scoring using LLM analysis for construction contracts."""

from __future__ import annotations

import json
import logging
import os

from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk analysis prompt templates
# ---------------------------------------------------------------------------

_RISK_ANALYSIS_PROMPT = """\
You are an expert construction contract attorney and risk analyst. Analyze the \
following construction contract text for risk factors.

**Project Type:** {project_type}

**Contract Text:**
<user_data>{contract_text}</user_data>

Identify all significant risk clauses. For each risk found, provide:
- clause: the relevant contract language (quoted or paraphrased)
- risk_type: one of "liquidated_damages", "indemnification", "scope_creep", \
"payment_terms", "change_order_process", "dispute_resolution", \
"insurance_requirements", "warranty"
- severity: "low", "medium", "high", or "critical"
- explanation: why this clause is risky for the contractor
- mitigation: recommended action to mitigate the risk

Also provide:
- overall_risk_score: integer 0-100 (higher = more risk)
- recommendations: list of top-level strategic recommendations

Respond ONLY with valid JSON in this exact format:
{{
  "overall_risk_score": <int>,
  "risk_items": [
    {{
      "clause": "<contract clause text>",
      "risk_type": "<risk type>",
      "severity": "<severity>",
      "explanation": "<why this is risky>",
      "mitigation": "<recommended mitigation>"
    }}
  ],
  "recommendations": [
    "<recommendation 1>",
    "<recommendation 2>"
  ]
}}
"""

_COMPARISON_PROMPT = """\
You are an expert construction contract attorney. Compare the following two \
construction contracts and highlight differences in risk allocation.

**Contract A:**
<user_data>{contract_a}</user_data>

**Contract B:**
<user_data>{contract_b}</user_data>

For each major topic area, compare the terms in both contracts and assess \
which contract is more favorable for the contractor.

Topics to compare:
- Payment terms and retainage
- Liquidated damages
- Indemnification and liability
- Change order process
- Warranty and guarantee obligations
- Insurance requirements
- Dispute resolution
- Scope definition and exclusions

Respond ONLY with valid JSON in this exact format:
{{
  "comparison": [
    {{
      "topic": "<topic area>",
      "contract_a_terms": "<summary of Contract A terms>",
      "contract_b_terms": "<summary of Contract B terms>",
      "risk_difference": "<which contract is riskier and why>"
    }}
  ],
  "recommendation": "<overall recommendation on which contract is more favorable>"
}}
"""

# ---------------------------------------------------------------------------
# Risk type severity weighting
# ---------------------------------------------------------------------------

_RISK_TYPE_WEIGHTS = {
    "indemnification": 1.5,
    "liquidated_damages": 1.3,
    "insurance_requirements": 1.2,
    "payment_terms": 1.1,
    "change_order_process": 1.0,
    "scope_creep": 1.0,
    "warranty": 0.9,
    "dispute_resolution": 0.8,
}

_SEVERITY_SCORES = {
    "critical": 25,
    "high": 18,
    "medium": 10,
    "low": 5,
}

# ---------------------------------------------------------------------------
# Text limits
# ---------------------------------------------------------------------------

_RISK_ANALYSIS_CHAR_LIMIT = 32000
_COMPARISON_CHAR_LIMIT = 16000
_CHUNK_SIZE = 30000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _calculate_weighted_score(risk_items: list[dict]) -> float:
    """Compute a risk score from individual risk items using severity weighting.

    Uses ``_RISK_TYPE_WEIGHTS`` and ``_SEVERITY_SCORES`` to produce a
    conservative score (0-100).
    """
    if not risk_items:
        return 0.0

    total = 0.0
    for item in risk_items:
        risk_type = item.get("risk_type", "scope_creep")
        severity = item.get("severity", "medium")
        weight = _RISK_TYPE_WEIGHTS.get(risk_type, 1.0)
        base = _SEVERITY_SCORES.get(severity, 10)
        total += base * weight

    # Normalise into 0-100 range.  The formula caps at 100 and scales so
    # that a handful of critical/high items already produce a high score.
    score = min(100.0, total)
    return round(score, 1)


def _split_into_chunks(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Split *text* into roughly *chunk_size*-character pieces at paragraph
    boundaries (double-newline).  Falls back to hard split if no boundary
    is found within a reasonable window.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to find a paragraph boundary near the end of the chunk
        separator_len = 0
        boundary = text.rfind("\n\n", start, end)
        if boundary != -1 and boundary > start:
            separator_len = 2  # len("\n\n")
        else:
            # No paragraph boundary found; try a single newline
            boundary = text.rfind("\n", start, end)
            if boundary != -1 and boundary > start:
                separator_len = 1  # len("\n")
            else:
                # Hard split as last resort
                boundary = end
                separator_len = 0

        chunks.append(text[start:boundary])
        start = boundary + separator_len
    return chunks


def _strip_code_fences(content: str) -> str:
    """Remove markdown code fences from LLM response content."""
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    return content


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def score_contract_risk(contract_text: str, project_type: str = "commercial") -> dict:
    """Analyze contract text for risk factors using LLM.

    Parameters
    ----------
    contract_text:
        The full or relevant excerpt of the construction contract.
    project_type:
        Type of construction project (e.g., "commercial", "residential",
        "industrial", "infrastructure").

    Returns
    -------
    dict with:
        - overall_risk_score: float (0-100, higher = more risk) or None on error
        - risk_items: list of {clause, risk_type, severity, explanation, mitigation}
        - recommendations: list[str]
        - model_used: str
        - analysis_available: bool
        - chunks_analyzed: int (only present when chunked analysis was used)
    """
    model_name = os.environ.get("LLM_CONTRACT_MODEL", "gpt-4o-mini")

    if not contract_text or not contract_text.strip():
        return {
            "overall_risk_score": 0.0,
            "risk_items": [],
            "analysis_available": True,
            "recommendations": ["No contract text provided for analysis."],
            "model_used": "none",
        }

    # Determine whether we need chunked analysis
    trimmed_text = contract_text.strip()
    if len(trimmed_text) > _RISK_ANALYSIS_CHAR_LIMIT:
        return await _chunked_risk_analysis(trimmed_text, project_type, model_name)

    return await _single_risk_analysis(
        trimmed_text[:_RISK_ANALYSIS_CHAR_LIMIT], project_type, model_name
    )


async def _llm_invoke(prompt: str, model_name: str) -> str:
    """Invoke LLM via gateway (preferred) or direct ChatOpenAI (fallback)."""
    try:
        from app.services.reliability.llm_gateway import get_llm_gateway

        gateway = await get_llm_gateway()
        result = await gateway.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name="contract_risk",
            temperature=0,
        )
        return result.get("content", "")
    except ImportError:
        llm = ChatOpenAI(model_name=model_name, temperature=0)
        response = await llm.ainvoke(prompt)
        raw = response.content
        return "".join(str(c) for c in raw) if isinstance(raw, list) else str(raw)


async def _single_risk_analysis(contract_text: str, project_type: str, model_name: str) -> dict:
    """Run a single (non-chunked) risk analysis pass."""
    prompt = _RISK_ANALYSIS_PROMPT.format(
        project_type=sanitize_for_prompt(project_type, max_length=100),
        contract_text=sanitize_for_prompt(contract_text, max_length=35000),
    )

    try:
        raw_content = await _llm_invoke(prompt, model_name)
        content = _strip_code_fences(raw_content.strip())

        parsed = json.loads(content)

        llm_score = float(parsed.get("overall_risk_score", 50))
        llm_score = max(0.0, min(100.0, llm_score))

        risk_items = parsed.get("risk_items", [])
        recommendations = parsed.get("recommendations", [])

        # Validate risk item structure
        validated_items: list[dict] = []
        valid_risk_types = {
            "liquidated_damages",
            "indemnification",
            "scope_creep",
            "payment_terms",
            "change_order_process",
            "dispute_resolution",
            "insurance_requirements",
            "warranty",
        }
        valid_severities = {"low", "medium", "high", "critical"}

        for item in risk_items:
            risk_type = item.get("risk_type", "scope_creep")
            severity = item.get("severity", "medium")
            validated_items.append(
                {
                    "clause": item.get("clause", ""),
                    "risk_type": risk_type if risk_type in valid_risk_types else "scope_creep",
                    "severity": severity if severity in valid_severities else "medium",
                    "explanation": item.get("explanation", ""),
                    "mitigation": item.get("mitigation", ""),
                }
            )

        # Conservative approach: use max of LLM score and calculated score
        calculated_score = _calculate_weighted_score(validated_items)
        overall_score = max(llm_score, calculated_score)

        logger.info(
            "Contract risk analysis: score=%.1f (llm=%.1f, calc=%.1f), "
            "%d risk items found, project_type=%s",
            overall_score,
            llm_score,
            calculated_score,
            len(validated_items),
            project_type,
        )

        return {
            "overall_risk_score": overall_score,
            "risk_items": validated_items,
            "analysis_available": True,
            "recommendations": recommendations,
            "model_used": model_name,
        }

    except json.JSONDecodeError:
        logger.error("Failed to parse LLM contract risk response")
        return {
            "overall_risk_score": None,
            "risk_items": [],
            "analysis_available": False,
            "error": "LLM response could not be parsed. Manual review required.",
            "recommendations": ["Manual review of all contract terms required."],
            "model_used": model_name,
        }
    except Exception as exc:
        logger.error("Contract risk analysis failed: %s", exc, exc_info=True)
        return {
            "overall_risk_score": None,
            "risk_items": [],
            "analysis_available": False,
            "recommendations": ["Automated analysis unavailable. Manual review required."],
            "model_used": model_name,
        }


async def _chunked_risk_analysis(contract_text: str, project_type: str, model_name: str) -> dict:
    """Split a long contract into chunks, analyze each, and aggregate."""
    chunks = _split_into_chunks(contract_text, _CHUNK_SIZE)

    all_risk_items: list[dict] = []
    all_recommendations: list[str] = []
    max_score: float = 0.0
    any_success = False

    for chunk in chunks:
        result = await _single_risk_analysis(chunk, project_type, model_name)
        if result.get("analysis_available"):
            any_success = True
            score = result.get("overall_risk_score")
            if score is not None and score > max_score:
                max_score = score
            all_risk_items.extend(result.get("risk_items", []))
            all_recommendations.extend(result.get("recommendations", []))

    if not any_success:
        return {
            "overall_risk_score": None,
            "risk_items": [],
            "analysis_available": False,
            "error": "All chunk analyses failed. Manual review required.",
            "recommendations": ["Automated analysis unavailable. Manual review required."],
            "model_used": model_name,
            "chunks_analyzed": len(chunks),
        }

    # Deduplicate recommendations while preserving order
    seen_recs: set[str] = set()
    unique_recs: list[str] = []
    for rec in all_recommendations:
        if rec not in seen_recs:
            seen_recs.add(rec)
            unique_recs.append(rec)

    # Recalculate overall score conservatively from all aggregated items
    calculated_score = _calculate_weighted_score(all_risk_items)
    overall_score = max(max_score, calculated_score)

    logger.info(
        "Chunked contract risk analysis: score=%.1f, %d risk items, "
        "%d chunks analyzed, project_type=%s",
        overall_score,
        len(all_risk_items),
        len(chunks),
        project_type,
    )

    return {
        "overall_risk_score": overall_score,
        "risk_items": all_risk_items,
        "analysis_available": True,
        "recommendations": unique_recs,
        "model_used": model_name,
        "chunks_analyzed": len(chunks),
    }


async def compare_contract_terms(contract_a: str, contract_b: str) -> dict:
    """Compare two contracts and highlight differences in risk.

    Parameters
    ----------
    contract_a:
        Text of the first contract.
    contract_b:
        Text of the second contract.

    Returns
    -------
    dict with:
        - comparison: list of {topic, contract_a_terms, contract_b_terms,
          risk_difference}
        - recommendation: str
        - model_used: str
    """
    model_name = os.environ.get("LLM_CONTRACT_MODEL", "gpt-4o-mini")

    if not contract_a.strip() or not contract_b.strip():
        return {
            "comparison": [],
            "recommendation": "Both contract texts must be provided for comparison.",
            "model_used": "none",
        }

    prompt = _COMPARISON_PROMPT.format(
        contract_a=sanitize_for_prompt(contract_a[:_COMPARISON_CHAR_LIMIT], max_length=18000),
        contract_b=sanitize_for_prompt(contract_b[:_COMPARISON_CHAR_LIMIT], max_length=18000),
    )

    try:
        raw_content = await _llm_invoke(prompt, model_name)
        content = _strip_code_fences(raw_content.strip())

        parsed = json.loads(content)

        comparison = parsed.get("comparison", [])
        recommendation = parsed.get("recommendation", "No recommendation generated.")

        # Validate comparison structure
        validated_comparison: list[dict] = []
        for item in comparison:
            validated_comparison.append(
                {
                    "topic": item.get("topic", ""),
                    "contract_a_terms": item.get("contract_a_terms", ""),
                    "contract_b_terms": item.get("contract_b_terms", ""),
                    "risk_difference": item.get("risk_difference", ""),
                }
            )

        logger.info(
            "Contract comparison complete: %d topics compared",
            len(validated_comparison),
        )

        return {
            "comparison": validated_comparison,
            "recommendation": recommendation,
            "model_used": model_name,
        }

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM contract comparison response: %s", exc)
        return {
            "comparison": [],
            "recommendation": (
                "Automated comparison failed due to response parsing error. "
                "Manual side-by-side review recommended."
            ),
            "model_used": model_name,
        }
    except Exception as exc:
        logger.error("Contract comparison failed: %s", exc, exc_info=True)
        return {
            "comparison": [],
            "recommendation": "Automated comparison failed. Manual side-by-side review recommended.",
            "model_used": model_name,
        }
