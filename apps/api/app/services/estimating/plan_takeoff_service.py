"""AI-powered plan takeoff service: extract quantities from construction drawings.

Parses uploaded PDF plans, uses LLM to identify construction elements,
maps them to CSI MasterFormat codes, and enriches with cost data from
the cost database. Produces a PlanTakeoff that can be converted into
a CostEstimate for bidding or budgeting.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Element type → CSI MasterFormat mapping (40+ entries)
# ---------------------------------------------------------------------------

ELEMENT_TO_CSI_MAP: dict[str, str] = {
    # Division 03 — Concrete
    "concrete_slab": "03 30 00",
    "concrete_footing": "03 30 00",
    "concrete_wall": "03 30 00",
    "concrete_beam": "03 30 00",
    "concrete_column": "03 30 00",
    "formwork": "03 10 00",
    "rebar": "03 20 00",
    # Division 04 — Masonry
    "brick_wall": "04 21 13",
    "cmu_wall": "04 22 00",
    "stone_veneer": "04 42 00",
    # Division 05 — Metals
    "steel_beam": "05 12 00",
    "steel_column": "05 12 00",
    "steel_joist": "05 21 00",
    "metal_decking": "05 31 00",
    "misc_metals": "05 50 00",
    # Division 06 — Wood, Plastics, Composites
    "wood_framing": "06 10 00",
    "wood_sheathing": "06 16 00",
    "millwork": "06 40 00",
    "casework": "06 41 00",
    # Division 07 — Thermal & Moisture Protection
    "batt_insulation": "07 21 00",
    "rigid_insulation": "07 21 00",
    "spray_foam": "07 21 00",
    "asphalt_shingle": "07 31 13",
    "metal_roofing": "07 41 00",
    "membrane_roofing": "07 52 00",
    "waterproofing": "07 10 00",
    # Division 08 — Openings
    "interior_door": "08 11 13",
    "exterior_door": "08 11 16",
    "overhead_door": "08 33 00",
    "window": "08 51 13",
    "curtain_wall": "08 44 00",
    "storefront": "08 41 00",
    # Division 09 — Finishes
    "drywall": "09 29 00",
    "plaster": "09 22 00",
    "ceramic_tile": "09 30 00",
    "carpet": "09 68 00",
    "vinyl_flooring": "09 65 00",
    "hardwood": "09 64 00",
    "painting": "09 91 00",
    "acoustic_ceiling": "09 51 00",
    # Division 14 — Conveying Equipment
    "elevator": "14 21 00",
    # Division 22 — Plumbing
    "plumbing_fixture": "22 40 00",
    "copper_pipe": "22 11 00",
    "pvc_pipe": "22 11 00",
    # Division 23 — HVAC
    "hvac_ductwork": "23 31 00",
    "hvac_unit": "23 81 00",
    # Division 26 — Electrical
    "electrical_panel": "26 24 16",
    "lighting": "26 51 13",
    "wiring": "26 05 00",
    # Division 31–32 — Earthwork & Exterior
    "sitework": "31 00 00",
    "paving": "32 12 00",
    "landscaping": "32 90 00",
}

# Reverse keyword lookup for fuzzy matching
_CSI_KEYWORDS: dict[str, list[str]] = {}
for _elem, _csi in ELEMENT_TO_CSI_MAP.items():
    # Split element type name into keywords
    keywords = _elem.replace("_", " ").lower().split()
    _CSI_KEYWORDS.setdefault(_csi, []).extend(keywords)

# Regional cost factors by US region (composite labor + material)
REGIONAL_COST_FACTORS: dict[str, Decimal] = {
    "northeast": Decimal("1.15"),
    "southeast": Decimal("0.90"),
    "midwest": Decimal("0.95"),
    "west": Decimal("1.10"),
    "northwest": Decimal("1.05"),
    "southwest": Decimal("0.92"),
    "mountain": Decimal("0.98"),
    "pacific": Decimal("1.18"),
    "national": Decimal("1.00"),
}

# State → region mapping for automatic region detection
_STATE_TO_REGION: dict[str, str] = {
    "CT": "northeast",
    "ME": "northeast",
    "MA": "northeast",
    "NH": "northeast",
    "RI": "northeast",
    "VT": "northeast",
    "NJ": "northeast",
    "NY": "northeast",
    "PA": "northeast",
    "DE": "northeast",
    "MD": "northeast",
    "DC": "northeast",
    "AL": "southeast",
    "AR": "southeast",
    "FL": "southeast",
    "GA": "southeast",
    "KY": "southeast",
    "LA": "southeast",
    "MS": "southeast",
    "NC": "southeast",
    "SC": "southeast",
    "TN": "southeast",
    "VA": "southeast",
    "WV": "southeast",
    "IL": "midwest",
    "IN": "midwest",
    "IA": "midwest",
    "KS": "midwest",
    "MI": "midwest",
    "MN": "midwest",
    "MO": "midwest",
    "NE": "midwest",
    "ND": "midwest",
    "OH": "midwest",
    "SD": "midwest",
    "WI": "midwest",
    "AZ": "southwest",
    "NM": "southwest",
    "OK": "southwest",
    "TX": "southwest",
    "CO": "mountain",
    "ID": "mountain",
    "MT": "mountain",
    "NV": "mountain",
    "UT": "mountain",
    "WY": "mountain",
    "OR": "northwest",
    "WA": "northwest",
    "CA": "pacific",
    "HI": "pacific",
    "AK": "pacific",
}

# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are a construction quantity takeoff specialist. Analyze the following
construction plan text and extract all identifiable construction elements.

Drawing type: {drawing_type}
Page number: {page_number}

Return a JSON array of objects. Each object MUST have these fields:
- "element_type": string (e.g. "concrete_slab", "interior_door", "drywall", "window", "steel_beam")
- "description": string (detailed description of the element)
- "quantity": number (estimated quantity based on the drawing)
- "unit": string (measurement unit: SF, LF, EA, CY, SY, TON, etc.)
- "dimensions": object or null (e.g. {{"length_ft": 20, "width_ft": 12, "thickness_in": 4}})
- "material": string or null (specific material if identifiable)

IMPORTANT:
- Extract EVERY identifiable construction element
- Use standard construction abbreviations for units (SF, LF, EA, CY, SY, TON, GAL, etc.)
- If quantity cannot be determined, estimate based on typical construction practice
- Include structural, architectural, mechanical, electrical, and plumbing elements
- Be specific about materials (e.g. "3/4 inch plywood sheathing" not just "sheathing")

Plan text:
<user_input>
{page_text}
</user_input>

Return ONLY the JSON array, no additional text."""


# ---------------------------------------------------------------------------
# CSI Mapping
# ---------------------------------------------------------------------------


def _map_element_to_csi(element: dict) -> str | None:
    """Map an extracted element to a CSI MasterFormat code.

    Tries exact match on element_type first, then keyword fuzzy match
    against the element description and material fields.

    Returns the CSI code string or None if no match found.
    """
    element_type = (element.get("element_type") or "").lower().strip().replace(" ", "_")

    # 1. Exact match on element_type
    if element_type in ELEMENT_TO_CSI_MAP:
        return ELEMENT_TO_CSI_MAP[element_type]

    # 2. Fuzzy match: search description and material for CSI keywords
    description = (element.get("description") or "").lower()
    material = (element.get("material") or "").lower()
    search_text = f"{element_type} {description} {material}"

    best_match: str | None = None
    best_score = 0

    for csi_code, keywords in _CSI_KEYWORDS.items():
        # Count how many keywords match
        unique_keywords = set(keywords)
        matched = sum(1 for kw in unique_keywords if kw in search_text)
        if matched > best_score:
            best_score = matched
            best_match = csi_code

    # Require at least 1 keyword match
    if best_score >= 1:
        return best_match

    return None


# ---------------------------------------------------------------------------
# LLM Extraction
# ---------------------------------------------------------------------------


async def _extract_elements_from_page(
    page_text: str,
    page_number: int,
    drawing_type: str | None,
) -> list[dict]:
    """Extract construction elements from a single page's text via LLM.

    Uses the LLM Gateway for structured JSON extraction with prompt sanitization.
    Returns a list of element dicts with: element_type, description, quantity,
    unit, dimensions, material.
    """
    from app.services.reliability.llm_gateway import get_llm_gateway
    from app.utils.prompt_sanitizer import sanitize_for_prompt

    if not page_text or not page_text.strip():
        return []

    sanitized_text = sanitize_for_prompt(page_text, max_length=6000)
    if not sanitized_text.strip():
        return []

    prompt = _EXTRACTION_PROMPT.format(
        drawing_type=drawing_type or "unknown",
        page_number=page_number,
        page_text=sanitized_text,
    )

    try:
        gateway = await get_llm_gateway()
        result = await gateway.complete(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a construction quantity takeoff AI. "
                        "Return ONLY valid JSON arrays. No markdown, no explanation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            agent_name="plan_takeoff",
            temperature=0.1,
            max_tokens=4096,
        )
        content = result.get("content", "")
    except Exception as exc:
        logger.error("LLM extraction failed for page %d: %s", page_number, exc)
        return []

    # Parse JSON from the LLM response
    elements = _parse_llm_json(content)
    return elements


def _parse_llm_json(content: str) -> list[dict]:
    """Parse a JSON array from LLM response content.

    Handles common LLM response quirks: markdown code fences, trailing commas,
    extra text before/after the JSON.
    """
    if not content:
        return []

    # Strip markdown code fences
    text = content.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_validate_element(e) for e in parsed if isinstance(e, dict)]
        return []
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [_validate_element(e) for e in parsed if isinstance(e, dict)]
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM JSON response: %.200s", text)
    return []


def _validate_element(elem: dict) -> dict:
    """Validate and normalize a single extracted element dict."""
    return {
        "element_type": str(elem.get("element_type", "unknown")).strip(),
        "description": str(elem.get("description", "")).strip(),
        "quantity": max(float(elem.get("quantity", 0)), 0),
        "unit": str(elem.get("unit", "EA")).strip().upper(),
        "dimensions": elem.get("dimensions") if isinstance(elem.get("dimensions"), dict) else None,
        "material": str(elem.get("material", "")) if elem.get("material") else None,
    }


# ---------------------------------------------------------------------------
# Cost Enrichment
# ---------------------------------------------------------------------------


async def _enrich_with_costs(
    db: AsyncSession | None,
    line_items: list[dict],
    location: dict | None = None,
) -> list[dict]:
    """Enrich takeoff line items with costs from the cost database.

    Uses ``match_costs`` from ``cost_database`` which handles the full
    CSI-to-cost mapping pipeline: hardcoded reference costs, DB search
    by CSI code, keyword matching, and BLS PPI adjustment.

    Applies regional cost factors if a location is provided.

    Mutates and returns the line_items list with cost fields populated.
    """
    from app.services.estimating.cost_database import match_costs

    # Determine region from location
    region_name = "national"
    if location:
        state = (location.get("state") or "").upper().strip()
        region_name = _STATE_TO_REGION.get(state, location.get("region", "national"))

    # Build quantities list in the format match_costs expects
    quantities = []
    for item in line_items:
        quantities.append(
            {
                "csi_code": item.get("csi_code", ""),
                "description": item.get("description", ""),
                "quantity": item.get("quantity", 0),
                "unit": item.get("unit", "EA"),
            }
        )

    try:
        enriched = await match_costs(
            quantities,
            region=region_name,
            db=db,
            location=location,
        )
    except Exception as exc:
        logger.error("Batch cost enrichment failed: %s", exc)
        return line_items

    # Map enriched costs back to line items
    for i, item in enumerate(line_items):
        if i >= len(enriched):
            break

        cost_data = enriched[i]
        # match_costs returns 'unit_cost' (= adjusted_cost) and optional breakdowns
        raw_unit_cost = Decimal(
            str(cost_data.get("unit_cost", cost_data.get("adjusted_unit_cost", 0)))
        )
        raw_material = Decimal(str(cost_data.get("material_cost", 0)))
        raw_labor = Decimal(str(cost_data.get("labor_cost", 0)))

        if raw_unit_cost <= ZERO:
            # Fallback: sum material + labor if unit cost is zero
            raw_unit_cost = raw_material + raw_labor

        if raw_unit_cost <= ZERO:
            continue

        quantity = Decimal(str(item.get("quantity", 0)))
        total = _round2(raw_unit_cost * quantity)

        item["unit_cost"] = raw_unit_cost
        item["material_cost"] = raw_material
        item["labor_cost"] = raw_labor
        item["total_cost"] = total
        item["source"] = "cost_db"
        item["confidence"] = Decimal("0.90")

    return line_items


def _resolve_region(location: dict | None) -> tuple[str, Decimal]:
    """Resolve a location dict to a region name and cost factor."""
    if not location:
        return "national", Decimal("1.00")
    state = (location.get("state") or "").upper().strip()
    region = _STATE_TO_REGION.get(state, location.get("region", "national"))
    factor = REGIONAL_COST_FACTORS.get(region, Decimal("1.00"))
    return region, factor


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def compute_takeoff_confidence(line_items: list[dict]) -> Decimal:
    """Compute weighted average confidence for a takeoff.

    Weights by total_cost: items with cost_db source get 0.9,
    llm_extracted items get 0.6, items without CSI match get 0.3.
    """
    if not line_items:
        return Decimal("0.00")

    total_weight = Decimal("0")
    weighted_confidence = Decimal("0")

    for item in line_items:
        cost = Decimal(str(item.get("total_cost", 0)))
        if cost <= ZERO:
            cost = Decimal("1")  # minimum weight

        source = item.get("source", "llm_extracted")
        csi_code = item.get("csi_code")

        if source == "cost_db":
            conf = Decimal("0.90")
        elif source == "manual":
            conf = Decimal("0.95")
        elif csi_code:
            conf = Decimal("0.60")
        else:
            conf = Decimal("0.30")

        weighted_confidence += conf * cost
        total_weight += cost

    if total_weight <= ZERO:
        return Decimal("0.00")

    result = (weighted_confidence / total_weight).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return min(result, Decimal("0.999"))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def process_plan_upload(
    db: AsyncSession,
    project_id: uuid.UUID,
    file_bytes: bytes,
    file_name: str,
    drawing_type: str | None = None,
    location: dict | None = None,
    created_by: uuid.UUID | None = None,
) -> Any:
    """Full AI plan takeoff pipeline.

    1. Parse PDF
    2. For each page, extract elements via LLM
    3. Map elements to CSI codes
    4. Query cost database for pricing
    5. Apply regional factors
    6. Save PlanTakeoff + TakeoffLineItem records

    Args:
        db: Async database session.
        project_id: Project to associate the takeoff with.
        file_bytes: Raw PDF bytes of the uploaded plan.
        file_name: Original file name for the plan.
        drawing_type: Optional hint (floor_plan, elevation, section, etc.).
        location: Optional dict with state/region for regional cost factors.
        created_by: Optional user ID who initiated the takeoff.

    Returns:
        The persisted PlanTakeoff ORM instance with line items loaded.
    """
    from app.models.plan_takeoff import PlanTakeoff, TakeoffLineItem
    from app.services.ingestion.pdf_parser import parse_pdf

    # Create the takeoff record in processing state
    takeoff = PlanTakeoff(
        project_id=project_id,
        name=f"Takeoff: {file_name}",
        file_name=file_name,
        status="processing",
        drawing_type=drawing_type,
        extraction_metadata={},
        created_by=created_by,
    )
    db.add(takeoff)
    await db.flush()
    await db.refresh(takeoff)

    try:
        # Step 1: Parse PDF
        pdf_result = parse_pdf(file_bytes)

        all_elements: list[dict] = []
        page_extraction_counts: dict[int, int] = {}

        # Step 2: Extract elements from each page
        for page in pdf_result.pages:
            elements = await _extract_elements_from_page(page.text, page.page_number, drawing_type)
            page_extraction_counts[page.page_number] = len(elements)

            for elem in elements:
                elem["page_number"] = page.page_number
                all_elements.append(elem)

        if not all_elements:
            takeoff.status = "failed"
            takeoff.extraction_metadata = {
                "error": "No construction elements detected in the document",
                "page_count": pdf_result.page_count,
            }
            await db.flush()
            await db.refresh(takeoff)
            return takeoff

        # Step 3: Map to CSI codes
        for elem in all_elements:
            elem["csi_code"] = _map_element_to_csi(elem)

        # Step 4: Enrich with costs
        all_elements = await _enrich_with_costs(db, all_elements, location)

        # Step 5: Build and persist line items
        region_name, region_factor = _resolve_region(location)
        total_cost = ZERO
        sort_order = 0

        for elem in all_elements:
            quantity = Decimal(str(elem.get("quantity", 0)))
            unit_cost = Decimal(str(elem.get("unit_cost", 0))) if elem.get("unit_cost") else None
            item_total = Decimal(str(elem.get("total_cost", 0))) if elem.get("total_cost") else None
            material_cost = (
                Decimal(str(elem.get("material_cost", 0))) if elem.get("material_cost") else None
            )
            labor_cost = Decimal(str(elem.get("labor_cost", 0))) if elem.get("labor_cost") else None

            # Determine element_type category for the model
            raw_type = (elem.get("element_type") or "material").lower()
            element_type = _classify_element_type(raw_type)

            confidence = (
                Decimal(str(elem.get("confidence", "0.600"))) if elem.get("confidence") else None
            )
            source = elem.get("source", "llm_extracted")

            line_item = TakeoffLineItem(
                takeoff_id=takeoff.id,
                element_type=element_type,
                description=elem.get("description", ""),
                csi_code=elem.get("csi_code"),
                quantity=quantity,
                unit=elem.get("unit", "EA"),
                dimensions=elem.get("dimensions"),
                unit_cost=unit_cost,
                total_cost=item_total,
                material_cost=material_cost,
                labor_cost=labor_cost,
                confidence=confidence,
                source=source,
                metadata_={
                    "page_number": elem.get("page_number"),
                    "material": elem.get("material"),
                },
                sort_order=sort_order,
            )
            db.add(line_item)
            sort_order += 1

            if item_total and item_total > ZERO:
                total_cost += item_total

        # Step 6: Compute confidence and update takeoff record
        confidence_score = compute_takeoff_confidence(all_elements)

        takeoff.status = "completed"
        takeoff.total_estimated_cost = _round2(total_cost) if total_cost > ZERO else None
        takeoff.confidence_score = confidence_score
        takeoff.regional_factors = (
            {
                "region": region_name,
                "factor": float(region_factor),
            }
            if location
            else None
        )
        takeoff.extraction_metadata = {
            "page_count": pdf_result.page_count,
            "elements_extracted": len(all_elements),
            "elements_with_csi": sum(1 for e in all_elements if e.get("csi_code")),
            "elements_with_cost": sum(1 for e in all_elements if e.get("total_cost")),
            "page_extraction_counts": page_extraction_counts,
        }

        await db.flush()
        await db.refresh(takeoff)
        return takeoff

    except Exception as exc:
        logger.exception("Plan takeoff processing failed for %s", file_name)
        takeoff.status = "failed"
        takeoff.extraction_metadata = {"error": str(exc)}
        await db.flush()
        await db.refresh(takeoff)
        return takeoff


def _classify_element_type(raw_type: str) -> str:
    """Map a raw element type string to one of the valid TakeoffLineItem element_type values."""
    raw = raw_type.lower().replace(" ", "_")

    # Direct classification keywords
    # NOTE: Order matters — more specific categories (MEP) must come before
    # generic ones (fixture) to avoid e.g. "plumbing_fixture" matching "fixture".
    classification_map = {
        "room": ["room", "space", "area"],
        "wall": ["wall", "partition", "cmu", "brick", "masonry"],
        "door": ["door", "entry", "overhead_door"],
        "window": ["window", "glazing", "storefront", "curtain_wall"],
        "mechanical": ["hvac", "duct", "mechanical"],
        "electrical": ["electr", "panel", "lighting", "wiring", "conduit"],
        "plumbing": ["plumb", "pipe", "pvc", "copper_pipe"],
        "fixture": ["fixture", "sink", "toilet", "faucet"],
        "finish": [
            "paint",
            "carpet",
            "tile",
            "flooring",
            "hardwood",
            "vinyl",
            "acoustic",
            "ceiling",
            "drywall",
            "plaster",
        ],
        "structural": [
            "concrete",
            "steel",
            "beam",
            "column",
            "joist",
            "footing",
            "slab",
            "rebar",
            "formwork",
            "framing",
            "sheathing",
            "decking",
        ],
        "material": [
            "insulation",
            "roofing",
            "waterproof",
            "membrane",
            "shingle",
            "millwork",
            "casework",
            "misc_metal",
            "elevator",
            "sitework",
            "paving",
            "landscaping",
        ],
    }

    for category, keywords in classification_map.items():
        for kw in keywords:
            if kw in raw:
                return category

    return "material"  # default


# ---------------------------------------------------------------------------
# Convert takeoff to estimate
# ---------------------------------------------------------------------------


async def convert_takeoff_to_estimate(
    db: AsyncSession,
    takeoff_id: uuid.UUID,
    estimate_name: str | None = None,
    contingency_pct: Decimal = Decimal("10.0"),
) -> Any:
    """Convert a completed PlanTakeoff into a CostEstimate with EstimateLineItems.

    Creates a new CostEstimate record and copies all priced takeoff line items
    as EstimateLineItems. Adds contingency percentage to total cost.

    Args:
        db: Async database session.
        takeoff_id: ID of the completed PlanTakeoff to convert.
        estimate_name: Optional name for the new estimate.
        contingency_pct: Contingency percentage to add (default 10%).

    Returns:
        The persisted CostEstimate ORM instance.

    Raises:
        ValueError: If the takeoff is not found, not completed, or has no priced items.
    """
    from app.models.estimating import CostEstimate, EstimateLineItem
    from app.models.plan_takeoff import PlanTakeoff

    takeoff = await db.get(PlanTakeoff, takeoff_id)
    if takeoff is None:
        raise ValueError("Plan takeoff not found")
    if takeoff.status not in ("completed",):
        raise ValueError(
            f"Cannot convert takeoff with status '{takeoff.status}'; must be 'completed'"
        )

    # Ensure line items are loaded
    if not takeoff.line_items:
        raise ValueError("Takeoff has no line items to convert")

    # Filter to items with cost data
    priced_items = [li for li in takeoff.line_items if li.total_cost and li.total_cost > ZERO]
    if not priced_items:
        raise ValueError("Takeoff has no priced items to convert")

    # Compute totals
    subtotal = sum(li.total_cost for li in priced_items)
    contingency_amount = _round2(subtotal * contingency_pct / Decimal("100"))
    total_with_contingency = _round2(subtotal + contingency_amount)

    # Create CostEstimate
    name = estimate_name or f"Estimate from {takeoff.name}"
    estimate = CostEstimate(
        project_id=takeoff.project_id,
        name=name,
        estimate_type="detailed",
        status="draft",
        total_cost=total_with_contingency,
        contingency_pct=contingency_pct,
        confidence_low=_round2(total_with_contingency * Decimal("0.85")),
        confidence_high=_round2(total_with_contingency * Decimal("1.20")),
        assumptions={
            "source": "plan_takeoff",
            "takeoff_id": str(takeoff_id),
            "takeoff_confidence": float(takeoff.confidence_score or 0),
            "contingency_pct": float(contingency_pct),
            "subtotal_before_contingency": float(subtotal),
        },
        created_by=takeoff.created_by,
    )
    db.add(estimate)
    await db.flush()
    await db.refresh(estimate)

    # Create EstimateLineItems
    for li in priced_items:
        est_line = EstimateLineItem(
            estimate_id=estimate.id,
            cost_item_id=li.cost_item_id,
            csi_code=li.csi_code,
            description=li.description,
            quantity=li.quantity,
            unit=li.unit,
            unit_cost=li.unit_cost or ZERO,
            total_cost=li.total_cost,
            source=li.source,
            confidence=li.confidence,
            metadata_={
                "takeoff_line_item_id": str(li.id),
                "element_type": li.element_type,
                "material_cost": float(li.material_cost) if li.material_cost else None,
                "labor_cost": float(li.labor_cost) if li.labor_cost else None,
            },
        )
        db.add(est_line)

    # Add contingency line item
    if contingency_amount > ZERO:
        contingency_line = EstimateLineItem(
            estimate_id=estimate.id,
            description=f"Contingency ({contingency_pct}%)",
            quantity=Decimal("1"),
            unit="LS",
            unit_cost=contingency_amount,
            total_cost=contingency_amount,
            source="manual",
            confidence=Decimal("1.000"),
            metadata_={"is_contingency": True},
        )
        db.add(contingency_line)

    # Update takeoff status to converted
    takeoff.status = "converted"

    await db.flush()
    await db.refresh(estimate)
    return estimate


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def get_takeoff(db: AsyncSession, takeoff_id: uuid.UUID) -> Any | None:
    """Fetch a single PlanTakeoff by ID with line items."""
    from app.models.plan_takeoff import PlanTakeoff

    return await db.get(PlanTakeoff, takeoff_id)


# ---------------------------------------------------------------------------
# IG-05: Convert takeoff to schedule activities
# ---------------------------------------------------------------------------

# CSI division → default title and productivity rate (units per crew-day)
_CSI_DIVISION_DEFAULTS: dict[str, dict[str, Any]] = {
    "03": {"title": "Concrete Work", "productivity_rate": 50.0, "default_unit": "CY"},
    "04": {"title": "Masonry Work", "productivity_rate": 80.0, "default_unit": "SF"},
    "05": {"title": "Structural Steel", "productivity_rate": 2.0, "default_unit": "TON"},
    "06": {"title": "Wood & Plastics", "productivity_rate": 200.0, "default_unit": "SF"},
    "07": {
        "title": "Thermal & Moisture Protection",
        "productivity_rate": 300.0,
        "default_unit": "SF",
    },
    "08": {"title": "Openings (Doors/Windows)", "productivity_rate": 8.0, "default_unit": "EA"},
    "09": {"title": "Finishes", "productivity_rate": 400.0, "default_unit": "SF"},
    "14": {"title": "Conveying Equipment", "productivity_rate": 0.1, "default_unit": "EA"},
    "22": {"title": "Plumbing", "productivity_rate": 20.0, "default_unit": "EA"},
    "23": {"title": "HVAC", "productivity_rate": 100.0, "default_unit": "LF"},
    "26": {"title": "Electrical", "productivity_rate": 15.0, "default_unit": "EA"},
    "31": {"title": "Earthwork", "productivity_rate": 200.0, "default_unit": "CY"},
    "32": {"title": "Exterior Improvements", "productivity_rate": 500.0, "default_unit": "SF"},
}


async def convert_takeoff_to_schedule_activities(
    db: AsyncSession,
    takeoff_id: uuid.UUID,
    project_id: uuid.UUID,
) -> list[Any]:
    """Convert a completed takeoff into preliminary ScheduleActivity records.

    Groups takeoff line items by CSI division, estimates a rough duration
    for each division based on total quantity and a default productivity
    rate, then creates ScheduleActivity records that the user can refine.

    Args:
        db: Async database session.
        takeoff_id: ID of the completed PlanTakeoff.
        project_id: Project to create activities in.

    Returns:
        List of created ScheduleActivity ORM instances.

    Raises:
        ValueError: If the takeoff is not found or has no line items.
    """
    from app.models.plan_takeoff import PlanTakeoff, TakeoffLineItem
    from app.models.scheduling import ScheduleActivity

    takeoff = await db.get(PlanTakeoff, takeoff_id)
    if takeoff is None:
        raise ValueError("Plan takeoff not found")
    if takeoff.status not in ("completed", "converted"):
        raise ValueError(
            f"Cannot convert takeoff with status '{takeoff.status}'; "
            "must be 'completed' or 'converted'"
        )

    # Fetch line items
    li_result = await db.execute(
        select(TakeoffLineItem).where(TakeoffLineItem.takeoff_id == takeoff_id)
    )
    line_items = list(li_result.scalars().all())
    if not line_items:
        raise ValueError("Takeoff has no line items to convert")

    # Group by CSI division (first 2 digits of csi_code)
    division_groups: dict[str, list] = {}
    for li in line_items:
        csi = li.csi_code or ""
        # Normalize: remove spaces, take first 2 chars
        division = csi.replace(" ", "")[:2] if csi else "99"
        division_groups.setdefault(division, []).append(li)

    created_activities: list[Any] = []
    sort_order = 0

    for division, items in sorted(division_groups.items()):
        defaults = _CSI_DIVISION_DEFAULTS.get(
            division,
            {
                "title": f"Division {division} Work",
                "productivity_rate": 100.0,
                "default_unit": "EA",
            },
        )

        # Sum total quantity for this division
        total_quantity = sum(float(li.quantity or 0) for li in items)

        # Estimate duration: quantity / productivity_rate, minimum 1 day
        productivity = defaults["productivity_rate"]
        estimated_days = max(1, round(total_quantity / productivity)) if productivity > 0 else 5

        # Cap at reasonable duration (120 days max per activity)
        estimated_days = min(estimated_days, 120)

        # Build CSI code for the division
        csi_code = f"{division} 00 00" if len(division) == 2 else division

        # Build a descriptive name from the items
        item_descriptions = list({li.description for li in items if li.description})[:3]
        desc_suffix = f" ({', '.join(item_descriptions[:2])})" if item_descriptions else ""

        activity = ScheduleActivity(
            project_id=project_id,
            activity_code=f"TKO-{division}-{sort_order:03d}",
            name=f"{defaults['title']}{desc_suffix}",
            duration_days=estimated_days,
            status="not_started",
            wbs_code=csi_code,
            metadata_={
                "source": "plan_takeoff",
                "takeoff_id": str(takeoff_id),
                "csi_division": division,
                "total_quantity": round(total_quantity, 2),
                "line_item_count": len(items),
                "estimated_from_productivity_rate": productivity,
            },
        )
        db.add(activity)
        created_activities.append(activity)
        sort_order += 1

    await db.flush()
    for act in created_activities:
        await db.refresh(act)

    logger.info(
        "Created %d schedule activities from takeoff %s for project %s",
        len(created_activities),
        takeoff_id,
        project_id,
    )
    return created_activities


async def list_takeoffs(
    db: AsyncSession,
    project_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> list[Any]:
    """List takeoffs for a project, ordered by creation date descending."""
    from app.models.plan_takeoff import PlanTakeoff

    result = await db.execute(
        select(PlanTakeoff)
        .where(PlanTakeoff.project_id == project_id)
        .order_by(PlanTakeoff.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())
