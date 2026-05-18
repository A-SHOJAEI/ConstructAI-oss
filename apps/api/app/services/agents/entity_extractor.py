"""Entity extraction from construction document text using LLM."""

from __future__ import annotations

import json
import logging

from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

ENTITY_EXTRACTION_PROMPT = """\
You are an expert construction document analyst. Extract all notable entities \
from the following construction document text.

**Text (user-supplied document content):**
<user_document>
{text}
</user_document>

Extract entities of the following types:
- **product**: Specific products, materials, or equipment mentioned.
- **manufacturer**: Manufacturer or brand names.
- **standard**: Referenced standards (e.g., ASTM, ACI, ANSI, NFPA codes).
- **requirement**: Specific performance requirements or criteria.
- **submittal_required**: Items that require submittal (shop drawings, samples, etc.).
- **test_required**: Required tests or inspections.
- **risk_clause**: Clauses involving liability, indemnification, or risk allocation.

For each entity, provide:
- **entity_type**: One of the types listed above.
- **entity_value**: The extracted value or description.
- **section_reference**: The section number or heading where this was found (if identifiable). \
Use null if not identifiable.
- **confidence**: A float between 0.0 and 1.0 indicating extraction confidence.

Respond ONLY with valid JSON in this exact format:
{{
  "entities": [
    {{
      "entity_type": "<type>",
      "entity_value": "<value>",
      "section_reference": "<section or null>",
      "confidence": <float>
    }}
  ]
}}

If no entities are found, return: {{"entities": []}}
"""


async def extract_entities(text: str) -> list[dict]:
    """Extract construction-relevant entities from document text.

    Args:
        text: The document text to analyze.

    Returns:
        A list of dicts, each with keys: entity_type, entity_value,
        section_reference, confidence.
    """
    model_name = "gpt-4o-mini"
    llm = ChatOpenAI(model_name=model_name, temperature=0)

    prompt = ENTITY_EXTRACTION_PROMPT.format(text=sanitize_for_prompt(text))

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
        entities = result.get("entities", [])

        return [
            {
                "entity_type": e.get("entity_type", "unknown"),
                "entity_value": e.get("entity_value", ""),
                "section_reference": e.get("section_reference"),
                # Clamp LLM confidence to [0.0, 0.95] — never fully trust model self-scores
                "confidence": max(0.0, min(0.95, float(e.get("confidence", 0.0)))),
            }
            for e in entities
            if e.get("entity_value")
        ]
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM entity extraction response: %s", exc)
        return []
    except Exception as exc:
        logger.error("Entity extraction failed: %s", exc)
        return []
