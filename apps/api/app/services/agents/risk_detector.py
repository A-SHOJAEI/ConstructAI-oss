"""Risk clause detection in construction document text using LLM."""

from __future__ import annotations

import json
import logging

from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

RISK_DETECTION_PROMPT = """\
You are an expert construction contract and risk analyst. Analyze the following \
construction document text and identify any risk-related clauses or provisions.

**Text (user-supplied document content):**
<user_document>
{text}
</user_document>

Identify risk clauses of the following types:
- **liability**: Clauses allocating or limiting liability.
- **indemnification**: Indemnification or hold-harmless provisions.
- **liquidated_damages**: Liquidated damages or delay penalty clauses.
- **warranty**: Warranty obligations, durations, or exclusions.
- **insurance_requirement**: Insurance coverage requirements (e.g., CGL, \
professional liability, builder's risk).
- **safety_hazard**: Safety requirements, hazard notifications, or OSHA compliance clauses.

For each risk clause, provide:
- **risk_type**: One of the types listed above.
- **description**: A concise summary of the risk clause.
- **section_reference**: The section number or heading where this was found (if identifiable). \
Use null if not identifiable.
- **severity**: One of "low", "medium", "high", or "critical".
- **confidence**: A float between 0.0 and 1.0 indicating detection confidence.

Respond ONLY with valid JSON in this exact format:
{{
  "risks": [
    {{
      "risk_type": "<type>",
      "description": "<description>",
      "section_reference": "<section or null>",
      "severity": "<low|medium|high|critical>",
      "confidence": <float>
    }}
  ]
}}

If no risk clauses are found, return: {{"risks": []}}
"""


async def detect_risks(text: str) -> list[dict]:
    """Detect risk clauses in construction document text.

    Args:
        text: The document text to analyze.

    Returns:
        A list of dicts, each with keys: risk_type, description,
        section_reference, severity, confidence.
    """
    model_name = "gpt-4o-mini"
    llm = ChatOpenAI(model_name=model_name, temperature=0)

    prompt = RISK_DETECTION_PROMPT.format(text=sanitize_for_prompt(text))

    try:
        response = await llm.ainvoke(prompt)
        content = (
            response.content if isinstance(response.content, str) else str(response.content)
        ).strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        result = json.loads(content)
        risks = result.get("risks", [])

        valid_severities = {"low", "medium", "high", "critical"}

        return [
            {
                "risk_type": r.get("risk_type", "unknown"),
                "description": r.get("description", ""),
                "section_reference": r.get("section_reference"),
                "severity": r.get("severity", "medium")
                if r.get("severity") in valid_severities
                else "medium",
                # Clamp LLM confidence to [0.0, 0.95] — never fully trust model self-scores
                "confidence": max(0.0, min(0.95, float(r.get("confidence", 0.0)))),
            }
            for r in risks
            if r.get("description")
        ]
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM risk detection response: %s", exc)
        return []
    except Exception as exc:
        logger.error("Risk detection failed: %s", exc)
        return []
