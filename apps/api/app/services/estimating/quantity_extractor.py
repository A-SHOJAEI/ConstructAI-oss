"""BIM quantity extraction from IFC data and construction documents."""

from __future__ import annotations

import json
import logging

from langchain_openai import ChatOpenAI

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IFC element type -> CSI MasterFormat code mapping
# ---------------------------------------------------------------------------

IFC_TO_CSI: dict[str, str] = {
    "IfcWall": "03 30 00",
    "IfcColumn": "03 30 00",
    "IfcSlab": "03 30 00",
    "IfcBeam": "05 12 00",
    "IfcDoor": "08 10 00",
    "IfcWindow": "08 50 00",
    "IfcRoof": "07 50 00",
}

# Preferred quantity key per unit type in order of priority
_QUANTITY_KEYS: list[tuple[str, str]] = [
    ("volume", "CY"),
    ("area", "SF"),
    ("length", "LF"),
    ("count", "EA"),
]

# ---------------------------------------------------------------------------
# LLM prompt for document-based extraction
# ---------------------------------------------------------------------------

_DOCUMENT_EXTRACTION_PROMPT = """\
You are an expert construction estimator. Extract material quantities from the \
following construction document text.

**Filename:** {filename}

**Document Text:**
<user_document>
{text_content}
</user_document>

For each quantity found, identify:
- description: what the item is
- quantity: numeric value
- unit: unit of measure (SF, CY, LF, EA, TON, etc.)
- csi_code: CSI MasterFormat code if identifiable (e.g., "03 30 00"), or null

Respond ONLY with valid JSON in this exact format:
{{
  "quantities": [
    {{
      "description": "<item description>",
      "quantity": <number>,
      "unit": "<unit>",
      "csi_code": "<code or null>"
    }}
  ]
}}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_quantities_from_ifc(ifc_data: dict) -> list[dict]:
    """Extract material quantities from IFC/BIM data.

    Processes IFC element data and extracts quantities by CSI division.
    Returns list of dicts with: csi_code, description, quantity, unit,
    element_type, element_id.
    """
    elements = ifc_data.get("elements", [])
    results: list[dict] = []

    for element in elements:
        element_type: str = element.get("type", "")
        csi_code = IFC_TO_CSI.get(element_type)
        if csi_code is None:
            logger.debug("Unmapped IFC type: %s", element_type)
            continue

        element_id: str = element.get("id", "")
        properties: dict = element.get("properties", {})
        quantities: dict = element.get("quantities", {})

        # Merge properties and quantities dicts for lookup
        combined = {**properties, **quantities}

        # Find the best available quantity measurement
        qty_value: float | None = None
        qty_unit: str = "EA"

        for key, unit in _QUANTITY_KEYS:
            val = combined.get(key)
            if val is not None:
                try:
                    qty_value = float(val)
                    qty_unit = unit
                    break
                except (ValueError, TypeError):
                    continue

        if qty_value is None:
            # Default to count of 1 for elements without explicit quantities
            qty_value = 1.0
            qty_unit = "EA"

        description = combined.get("name", "") or combined.get("description", "") or element_type

        results.append(
            {
                "csi_code": csi_code,
                "description": description,
                "quantity": qty_value,
                "unit": qty_unit,
                "element_type": element_type,
                "element_id": element_id,
            }
        )

    # Sort by CSI code for consistent ordering
    results.sort(key=lambda item: item["csi_code"])
    logger.info("Extracted %d quantities from %d IFC elements", len(results), len(elements))
    return results


async def extract_quantities_from_document(text_content: str, filename: str) -> list[dict]:
    """Use LLM to extract quantities from document text (specifications, BOQs)."""
    import os

    model_name = os.environ.get("LLM_QUANTITY_EXTRACTOR_MODEL", "gpt-4o-mini")

    prompt = _DOCUMENT_EXTRACTION_PROMPT.format(
        filename=sanitize_for_prompt(filename, max_length=255),
        text_content=sanitize_for_prompt(text_content),
    )

    try:
        try:
            from app.services.reliability.llm_gateway import get_llm_gateway

            gateway = await get_llm_gateway()
            result = await gateway.complete(
                messages=[{"role": "user", "content": prompt}],
                agent_name="quantity_extractor",
                temperature=0,
            )
            content = result.get("content", "").strip()
        except ImportError:
            llm = ChatOpenAI(model_name=model_name, temperature=0)
            response = await llm.ainvoke(prompt)
            content = str(response.content).strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        parsed = json.loads(content)
        raw_quantities = parsed.get("quantities", [])

        results: list[dict] = []
        for item in raw_quantities:
            results.append(
                {
                    "description": item.get("description", ""),
                    "quantity": float(item.get("quantity", 0)),
                    "unit": item.get("unit", "EA"),
                    "csi_code": item.get("csi_code"),
                    # Clamp LLM-derived confidence to [0.0, 0.95]
                    "confidence": max(0.0, min(0.95, float(item.get("confidence", 0.7)))),
                }
            )

        logger.info("Extracted %d quantities from document '%s'", len(results), filename)
        return results

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM quantity extraction response: %s", exc)
        return []
    except Exception as exc:
        logger.error("Document quantity extraction failed: %s", exc)
        return []
