"""Contract intelligence agent for clause extraction, comparison, and deviation analysis.

Uses LLM-powered extraction to parse construction contract clauses, compare
contracts side-by-side, check for deviations from standard terms, and
auto-populate project settings from extracted clause values.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import ContractClause, ContractComparison
from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUSE_TYPES = {
    "payment_terms",
    "retainage",
    "liquidated_damages",
    "notice_requirements",
    "insurance",
    "change_order_procedures",
    "warranty",
    "dispute_resolution",
    "indemnification",
    "termination",
    "force_majeure",
    "prevailing_wage",
}

DEFAULT_STANDARD_TERMS: dict[str, dict] = {
    "retainage": {
        "description": "Standard retainage percentage",
        "standard_value": 10.0,
        "unit": "percent",
        "max_acceptable": 10.0,
        "notes": "AIA A201-2017 standard retainage is 10%",
    },
    "liquidated_damages": {
        "description": "Liquidated damages rate",
        "standard_value": 500.0,
        "unit": "dollars_per_day",
        "max_acceptable": 1000.0,
        "notes": "Typical LD rates range $200-$1000/day depending on project size",
    },
    "payment_terms": {
        "description": "Payment period after invoice",
        "standard_value": 30,
        "unit": "days",
        "max_acceptable": 45,
        "notes": "AIA standard is 30 days; over 45 days is a red flag",
    },
    "warranty": {
        "description": "General warranty period",
        "standard_value": 12,
        "unit": "months",
        "min_acceptable": 12,
        "notes": "Standard 1-year warranty per AIA A201",
    },
    "notice_requirements": {
        "description": "Written notice period for claims",
        "standard_value": 21,
        "unit": "days",
        "min_acceptable": 7,
        "notes": "Standard 21 days per AIA A201; less than 7 days is unreasonable",
    },
    "insurance": {
        "description": "General liability minimum coverage",
        "standard_value": 1_000_000,
        "unit": "dollars",
        "min_acceptable": 500_000,
        "notes": "Minimum GL coverage; project-specific requirements may be higher",
    },
    "change_order_procedures": {
        "description": "Change order response period",
        "standard_value": 14,
        "unit": "days",
        "max_acceptable": 30,
        "notes": "Owner response time for change orders",
    },
    "dispute_resolution": {
        "description": "Primary dispute resolution method",
        "standard_value": "mediation",
        "unit": "method",
        "acceptable_values": ["mediation", "arbitration", "mediation_then_arbitration"],
        "notes": "AIA A201 uses mediation then arbitration",
    },
    "force_majeure": {
        "description": "Force majeure clause inclusion",
        "standard_value": True,
        "unit": "boolean",
        "notes": "Must be present; absence is a significant deviation",
    },
    "termination": {
        "description": "Termination for convenience cure period",
        "standard_value": 7,
        "unit": "days",
        "min_acceptable": 7,
        "notes": "Minimum cure period before termination for cause",
    },
    "indemnification": {
        "description": "Indemnification scope",
        "standard_value": "proportional",
        "unit": "scope",
        "acceptable_values": ["proportional", "comparative_fault"],
        "notes": "Broad-form indemnification is a red flag in many jurisdictions",
    },
    "prevailing_wage": {
        "description": "Prevailing wage applicability",
        "standard_value": False,
        "unit": "boolean",
        "notes": "Required on public/government projects",
    },
}


# ---------------------------------------------------------------------------
# SV-22: Org-configurable standard terms
# ---------------------------------------------------------------------------


async def get_org_standard_terms(
    db: AsyncSession,
    org_id: str | None,
) -> dict[str, dict]:
    """Return standard contract terms for an organization.

    Checks for org-specific overrides stored in the Organization settings JSONB
    under the key ``contract_standard_terms``. Falls back to
    ``DEFAULT_STANDARD_TERMS`` if no overrides are configured.

    The override format matches DEFAULT_STANDARD_TERMS: a dict of
    clause_type -> {description, standard_value, unit, max_acceptable, ...}.
    Partial overrides are merged with the defaults (org values take precedence).
    """
    if not org_id:
        return dict(DEFAULT_STANDARD_TERMS)

    try:
        import uuid as _uuid

        from app.models.organization import Organization

        org_uuid = _uuid.UUID(org_id) if isinstance(org_id, str) else org_id
        org = await db.get(Organization, org_uuid)

        if org is not None and org.settings:
            org_terms = org.settings.get("contract_standard_terms")
            if isinstance(org_terms, dict) and org_terms:
                # Merge: start with defaults, overlay org-specific overrides
                merged = dict(DEFAULT_STANDARD_TERMS)
                for term_type, term_config in org_terms.items():
                    if isinstance(term_config, dict):
                        if term_type in merged:
                            # Merge individual fields within the term
                            merged[term_type] = {**merged[term_type], **term_config}
                        else:
                            merged[term_type] = term_config
                logger.info(
                    "Using org-specific standard terms for org %s (%d overrides)",
                    org_id,
                    len(org_terms),
                )
                return merged
    except Exception:
        logger.warning(
            "Failed to load org standard terms for %s, using defaults",
            org_id,
            exc_info=True,
        )

    return dict(DEFAULT_STANDARD_TERMS)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ExtractedClause:
    """A clause extracted from contract text via LLM analysis."""

    clause_type: str
    clause_text: str
    parsed_value: dict = field(default_factory=dict)
    section_reference: str | None = None
    confidence: float = 0.50
    occurrence_index: int = 1  # SV-20: supports multiple clauses per type


@dataclass
class ContractDeviation:
    """A deviation from standard contract terms."""

    clause_type: str
    description: str
    severity: str  # "low", "medium", "high", "critical"
    contract_value: object = None
    standard_value: object = None
    recommendation: str = ""


@dataclass
class ComparisonResult:
    """Result of comparing two sets of contract clauses."""

    additions: list[dict] = field(default_factory=list)
    removals: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# LLM prompt for clause extraction
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are a construction contract analysis expert. Extract ALL contract clauses from the following document text.

IMPORTANT: A contract may contain MULTIPLE occurrences of the same clause type (e.g., multiple insurance requirements, multiple warranty provisions, multiple notice requirements). Extract EVERY occurrence as a separate object in the array. Do NOT merge or deduplicate — return each distinct clause mention individually.

For each clause found, provide:
- clause_type: One of: {clause_types}
- clause_text: The exact text of the clause (up to 500 characters)
- parsed_value: A structured value extracted from the clause. For example:
  - retainage: {{"percentage": 10.0}}
  - payment_terms: {{"net_days": 30, "invoice_frequency": "monthly"}}
  - liquidated_damages: {{"rate_per_day": 500.0, "cap": null}}
  - warranty: {{"duration_months": 12, "scope": "general"}}
  - notice_requirements: {{"days": 21, "method": "written"}}
  - insurance: {{"gl_amount": 1000000, "auto_amount": 500000}}
  - force_majeure: {{"included": true, "events": ["pandemic", "natural_disaster"]}}
  - termination: {{"for_cause_cure_days": 7, "for_convenience": true}}
  - indemnification: {{"scope": "proportional"}}
  - prevailing_wage: {{"required": false}}
  - change_order_procedures: {{"response_days": 14, "written_required": true}}
  - dispute_resolution: {{"primary_method": "mediation", "secondary_method": "arbitration"}}
- section_reference: The section number or heading where this clause appears (e.g., "Article 9.3.1")
- confidence: A float from 0.0 to 1.0 indicating your confidence in the extraction
- occurrence_index: Integer starting at 1 for each clause_type (e.g., first insurance clause = 1, second = 2)

Contract type: {contract_type}

<user_document>
{document_text}
</user_document>

Return a JSON array of clause objects. Return ONLY the JSON array, no other text.
Example with multiple occurrences of the same type:
[
  {{
    "clause_type": "retainage",
    "clause_text": "Owner shall retain 10% of each progress payment...",
    "parsed_value": {{"percentage": 10.0}},
    "section_reference": "Article 9.3.1",
    "confidence": 0.95,
    "occurrence_index": 1
  }},
  {{
    "clause_type": "insurance",
    "clause_text": "Contractor shall maintain general liability insurance of $1,000,000...",
    "parsed_value": {{"gl_amount": 1000000}},
    "section_reference": "Article 11.1.1",
    "confidence": 0.92,
    "occurrence_index": 1
  }},
  {{
    "clause_type": "insurance",
    "clause_text": "Contractor shall maintain automobile liability insurance of $500,000...",
    "parsed_value": {{"auto_amount": 500000}},
    "section_reference": "Article 11.1.2",
    "confidence": 0.90,
    "occurrence_index": 2
  }}
]
"""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_retainage(parsed_value: dict) -> dict:
    """Validate retainage parsed value: percentage must be 0-100."""
    pct = parsed_value.get("percentage")
    if pct is not None:
        try:
            pct = float(pct)
            if pct < 0 or pct > 100:
                logger.warning("Retainage percentage out of range: %s", pct)
                parsed_value["percentage"] = max(0, min(100, pct))
        except (TypeError, ValueError):
            logger.warning("Invalid retainage percentage: %s", pct)
            parsed_value.pop("percentage", None)
    return parsed_value


def _validate_payment_terms(parsed_value: dict) -> dict:
    """Validate payment terms: net_days must be positive integer."""
    days = parsed_value.get("net_days")
    if days is not None:
        try:
            days = int(days)
            if days < 1 or days > 365:
                logger.warning("Payment net_days out of range: %s", days)
                parsed_value["net_days"] = max(1, min(365, days))
            else:
                parsed_value["net_days"] = days
        except (TypeError, ValueError):
            logger.warning("Invalid payment net_days: %s", days)
            parsed_value.pop("net_days", None)
    return parsed_value


def _validate_liquidated_damages(parsed_value: dict) -> dict:
    """Validate LD: rate_per_day must be non-negative."""
    rate = parsed_value.get("rate_per_day")
    if rate is not None:
        try:
            rate = float(rate)
            if rate < 0:
                parsed_value["rate_per_day"] = 0.0
            else:
                parsed_value["rate_per_day"] = rate
        except (TypeError, ValueError):
            parsed_value.pop("rate_per_day", None)
    return parsed_value


def _validate_warranty(parsed_value: dict) -> dict:
    """Validate warranty: duration_months must be positive."""
    months = parsed_value.get("duration_months")
    if months is not None:
        try:
            months = int(months)
            if months < 1:
                parsed_value["duration_months"] = 1
            else:
                parsed_value["duration_months"] = months
        except (TypeError, ValueError):
            parsed_value.pop("duration_months", None)
    return parsed_value


def _validate_insurance(parsed_value: dict) -> dict:
    """Validate insurance amounts are non-negative."""
    for key in ("gl_amount", "auto_amount", "umbrella_amount", "workers_comp_amount"):
        val = parsed_value.get(key)
        if val is not None:
            try:
                val = float(val)
                if val < 0:
                    parsed_value[key] = 0.0
                else:
                    parsed_value[key] = val
            except (TypeError, ValueError):
                parsed_value.pop(key, None)
    return parsed_value


def _validate_notice_requirements(parsed_value: dict) -> dict:
    """Validate notice period days."""
    days = parsed_value.get("days")
    if days is not None:
        try:
            days = int(days)
            if days < 1:
                parsed_value["days"] = 1
            else:
                parsed_value["days"] = days
        except (TypeError, ValueError):
            parsed_value.pop("days", None)
    return parsed_value


_CLAUSE_VALIDATORS = {
    "retainage": _validate_retainage,
    "payment_terms": _validate_payment_terms,
    "liquidated_damages": _validate_liquidated_damages,
    "warranty": _validate_warranty,
    "insurance": _validate_insurance,
    "notice_requirements": _validate_notice_requirements,
}


def _validate_clause(clause_type: str, parsed_value: dict) -> dict:
    """Run type-specific validation on a parsed clause value."""
    validator = _CLAUSE_VALIDATORS.get(clause_type)
    if validator:
        return validator(parsed_value)
    return parsed_value


def _parse_llm_json(raw_text: str) -> list[dict]:
    """Parse JSON array from LLM response, handling common formatting issues."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as JSON: %s", text[:200])
    return []


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


async def extract_contract_clauses(
    document_text: str,
    contract_type: str = "prime",
    llm_gateway: Any | None = None,
    org_id: str | None = None,
) -> list[ExtractedClause]:
    """Extract contract clauses from document text using LLM analysis.

    Args:
        document_text: The full text of the contract document.
        contract_type: One of prime/subcontract/purchase_order/consulting.
        llm_gateway: Optional LLMGateway instance. If None, imports singleton.
        org_id: Organization ID for LLM usage tracking.

    Returns:
        List of ExtractedClause with validated parsed values.
    """
    if not document_text or not document_text.strip():
        return []

    # SECURITY: Sanitize document text before prompt interpolation
    sanitized_text = sanitize_for_prompt(document_text, max_length=50_000)

    prompt = _EXTRACTION_PROMPT.format(
        clause_types=", ".join(sorted(CLAUSE_TYPES)),
        contract_type=contract_type,
        document_text=sanitized_text,
    )

    # Get LLM gateway
    if llm_gateway is None:
        from app.services.reliability.llm_gateway import get_llm_gateway

        llm_gateway = await get_llm_gateway()

    messages = [
        {"role": "system", "content": "You are a construction contract analysis expert."},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm_gateway.complete(
            messages=messages,
            agent_name="contract_intelligence",
            org_id=org_id,
            temperature=0.1,
            max_tokens=4096,
        )
        raw_content = result.get("content", "")
    except Exception as exc:
        logger.error("LLM clause extraction failed: %s", exc)
        return []

    raw_clauses = _parse_llm_json(raw_content)

    extracted: list[ExtractedClause] = []
    for raw in raw_clauses:
        clause_type = raw.get("clause_type", "").lower().strip()
        if clause_type not in CLAUSE_TYPES:
            logger.warning("Skipping unknown clause type: %s", clause_type)
            continue

        clause_text = str(raw.get("clause_text", "")).strip()
        if not clause_text:
            continue

        parsed_value = raw.get("parsed_value", {})
        if not isinstance(parsed_value, dict):
            parsed_value = {}

        # Validate parsed values per clause type
        parsed_value = _validate_clause(clause_type, parsed_value)

        confidence = raw.get("confidence", 0.50)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.50

        section_ref = raw.get("section_reference")
        if section_ref:
            section_ref = str(section_ref).strip()[:100]

        # SV-20: Parse occurrence_index for multi-occurrence clauses
        occurrence_index = raw.get("occurrence_index", 1)
        try:
            occurrence_index = max(1, int(occurrence_index))
        except (TypeError, ValueError):
            occurrence_index = 1

        extracted.append(
            ExtractedClause(
                clause_type=clause_type,
                clause_text=clause_text[:2000],
                parsed_value=parsed_value,
                section_reference=section_ref,
                confidence=confidence,
                occurrence_index=occurrence_index,
            )
        )

    logger.info(
        "Extracted %d clauses from %d chars of contract text",
        len(extracted),
        len(document_text),
    )
    return extracted


def compare_contracts(
    clauses_a: list[ExtractedClause],
    clauses_b: list[ExtractedClause],
) -> ComparisonResult:
    """Compare two sets of contract clauses to identify differences.

    Groups clauses by type, compares parsed_value fields, and identifies
    additions, removals, and changes.

    Args:
        clauses_a: Clauses from contract A (baseline).
        clauses_b: Clauses from contract B (comparison).

    Returns:
        ComparisonResult with additions, removals, and changes.
    """
    # Group by clause type
    types_a: dict[str, list[ExtractedClause]] = {}
    types_b: dict[str, list[ExtractedClause]] = {}

    for c in clauses_a:
        types_a.setdefault(c.clause_type, []).append(c)
    for c in clauses_b:
        types_b.setdefault(c.clause_type, []).append(c)

    all_types = set(types_a.keys()) | set(types_b.keys())

    additions: list[dict] = []
    removals: list[dict] = []
    changes: list[dict] = []

    for ctype in sorted(all_types):
        a_list = types_a.get(ctype, [])
        b_list = types_b.get(ctype, [])

        if not a_list and b_list:
            # Present in B but not A => addition
            for clause in b_list:
                additions.append(
                    {
                        "clause_type": ctype,
                        "clause_text": clause.clause_text,
                        "parsed_value": clause.parsed_value,
                        "section_reference": clause.section_reference,
                    }
                )
        elif a_list and not b_list:
            # Present in A but not B => removal
            for clause in a_list:
                removals.append(
                    {
                        "clause_type": ctype,
                        "clause_text": clause.clause_text,
                        "parsed_value": clause.parsed_value,
                        "section_reference": clause.section_reference,
                    }
                )
        else:
            # SV-23: Both have this type — compare ALL clauses of the same
            # type, matching by occurrence_index when available, instead of
            # only comparing the first clause per type.

            # Index by occurrence_index for pairing
            a_by_idx = {c.occurrence_index: c for c in a_list}
            b_by_idx = {c.occurrence_index: c for c in b_list}
            all_indices = sorted(set(a_by_idx.keys()) | set(b_by_idx.keys()))

            for occ_idx in all_indices:
                a_clause = a_by_idx.get(occ_idx)
                b_clause = b_by_idx.get(occ_idx)

                if a_clause and not b_clause:
                    # This occurrence was removed in B
                    removals.append(
                        {
                            "clause_type": ctype,
                            "occurrence_index": occ_idx,
                            "clause_text": a_clause.clause_text,
                            "parsed_value": a_clause.parsed_value,
                            "section_reference": a_clause.section_reference,
                        }
                    )
                elif b_clause and not a_clause:
                    # This occurrence was added in B
                    additions.append(
                        {
                            "clause_type": ctype,
                            "occurrence_index": occ_idx,
                            "clause_text": b_clause.clause_text,
                            "parsed_value": b_clause.parsed_value,
                            "section_reference": b_clause.section_reference,
                        }
                    )
                elif a_clause and b_clause:
                    if a_clause.parsed_value != b_clause.parsed_value:
                        diff_fields = _diff_parsed_values(
                            a_clause.parsed_value, b_clause.parsed_value
                        )
                        changes.append(
                            {
                                "clause_type": ctype,
                                "occurrence_index": occ_idx,
                                "contract_a": {
                                    "clause_text": a_clause.clause_text,
                                    "parsed_value": a_clause.parsed_value,
                                    "section_reference": a_clause.section_reference,
                                },
                                "contract_b": {
                                    "clause_text": b_clause.clause_text,
                                    "parsed_value": b_clause.parsed_value,
                                    "section_reference": b_clause.section_reference,
                                },
                                "changed_fields": diff_fields,
                            }
                        )

    total_diffs = len(additions) + len(removals) + len(changes)
    summary = (
        f"Found {total_diffs} differences: "
        f"{len(additions)} additions, {len(removals)} removals, {len(changes)} changes."
    )

    return ComparisonResult(
        additions=additions,
        removals=removals,
        changes=changes,
        summary=summary,
    )


def _diff_parsed_values(a: dict, b: dict) -> list[dict]:
    """Compute field-level differences between two parsed value dicts."""
    all_keys = set(a.keys()) | set(b.keys())
    diffs: list[dict] = []
    for key in sorted(all_keys):
        val_a = a.get(key)
        val_b = b.get(key)
        if val_a != val_b:
            diffs.append(
                {
                    "field": key,
                    "value_a": val_a,
                    "value_b": val_b,
                }
            )
    return diffs


def check_deviations(
    clauses: list[ExtractedClause],
    standard_terms: dict[str, dict] | None = None,
) -> list[ContractDeviation]:
    """Check extracted clauses against standard construction contract terms.

    Identifies significant deviations such as:
    - Retainage > 10%
    - LD > $1000/day
    - No force majeure clause
    - Payment terms > 45 days
    - Warranty < 12 months

    Args:
        clauses: Extracted clauses to check.
        standard_terms: Override standard terms. Defaults to DEFAULT_STANDARD_TERMS.

    Returns:
        List of ContractDeviation sorted by severity (critical first).
    """
    terms = standard_terms or DEFAULT_STANDARD_TERMS
    deviations: list[ContractDeviation] = []

    # Index clauses by type
    clause_map: dict[str, ExtractedClause] = {}
    for c in clauses:
        # Keep the highest-confidence clause of each type
        existing = clause_map.get(c.clause_type)
        if existing is None or c.confidence > existing.confidence:
            clause_map[c.clause_type] = c

    # Check each standard term
    for term_type, standard in terms.items():
        clause = clause_map.get(term_type)

        if clause is None:
            # Missing clause — may or may not be a problem
            if term_type == "force_majeure":
                deviations.append(
                    ContractDeviation(
                        clause_type=term_type,
                        description="Force majeure clause is missing from the contract",
                        severity="critical",
                        contract_value=None,
                        standard_value=True,
                        recommendation="Add a force majeure clause covering pandemics, "
                        "natural disasters, government actions, and supply chain disruptions.",
                    )
                )
            elif term_type in ("retainage", "payment_terms", "warranty"):
                deviations.append(
                    ContractDeviation(
                        clause_type=term_type,
                        description=f"No {term_type.replace('_', ' ')} clause found in contract",
                        severity="medium",
                        contract_value=None,
                        standard_value=standard.get("standard_value"),
                        recommendation=f"Ensure {term_type.replace('_', ' ')} "
                        f"is explicitly defined in the contract.",
                    )
                )
            continue

        pv = clause.parsed_value

        # Type-specific deviation checks
        if term_type == "retainage":
            pct = pv.get("percentage")
            if pct is not None:
                max_ok = standard.get("max_acceptable", 10.0)
                if float(pct) > float(max_ok):
                    severity = "critical" if float(pct) > 15 else "high"
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"Retainage of {pct}% exceeds standard maximum of {max_ok}%",
                            severity=severity,
                            contract_value=pct,
                            standard_value=max_ok,
                            recommendation="Negotiate retainage down to 10% or include "
                            "retainage reduction at 50% completion.",
                        )
                    )

        elif term_type == "liquidated_damages":
            rate = pv.get("rate_per_day")
            if rate is not None:
                max_ok = standard.get("max_acceptable", 1000.0)
                if float(rate) > float(max_ok):
                    severity = "high" if float(rate) > 2000 else "medium"
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"LD rate of ${rate}/day exceeds standard maximum of ${max_ok}/day",
                            severity=severity,
                            contract_value=rate,
                            standard_value=max_ok,
                            recommendation="Negotiate a lower LD rate or add a cap on total "
                            "liquidated damages (e.g., 5-10% of contract value).",
                        )
                    )

        elif term_type == "payment_terms":
            days = pv.get("net_days")
            if days is not None:
                max_ok = standard.get("max_acceptable", 45)
                if int(days) > int(max_ok):
                    severity = "high" if int(days) > 60 else "medium"
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"Payment terms of {days} days exceed standard maximum of {max_ok} days",
                            severity=severity,
                            contract_value=days,
                            standard_value=max_ok,
                            recommendation="Negotiate net-30 payment terms. Extended payment "
                            "terms increase cash flow risk for subcontractors.",
                        )
                    )

        elif term_type == "warranty":
            months = pv.get("duration_months")
            if months is not None:
                min_ok = standard.get("min_acceptable", 12)
                if int(months) < int(min_ok):
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"Warranty period of {months} months is below the "
                            f"standard minimum of {min_ok} months",
                            severity="medium",
                            contract_value=months,
                            standard_value=min_ok,
                            recommendation="Ensure warranty period meets or exceeds 12 months.",
                        )
                    )

        elif term_type == "notice_requirements":
            days = pv.get("days")
            if days is not None:
                min_ok = standard.get("min_acceptable", 7)
                if int(days) < int(min_ok):
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"Notice period of {days} days is below the "
                            f"minimum acceptable of {min_ok} days",
                            severity="high",
                            contract_value=days,
                            standard_value=min_ok,
                            recommendation=f"Negotiate a minimum {min_ok}-day notice "
                            "period for claims and changes.",
                        )
                    )

        elif term_type == "insurance":
            gl = pv.get("gl_amount")
            if gl is not None:
                min_ok = standard.get("min_acceptable", 500_000)
                if float(gl) < float(min_ok):
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"GL coverage of ${gl:,.0f} is below minimum "
                            f"acceptable of ${min_ok:,.0f}",
                            severity="high",
                            contract_value=gl,
                            standard_value=min_ok,
                            recommendation="Increase general liability coverage to meet "
                            "project requirements.",
                        )
                    )

        elif term_type == "indemnification":
            scope = pv.get("scope", "").lower()
            acceptable = standard.get("acceptable_values", [])
            if scope and scope not in acceptable:
                deviations.append(
                    ContractDeviation(
                        clause_type=term_type,
                        description=f"Indemnification scope '{scope}' deviates from "
                        f"accepted scopes: {acceptable}",
                        severity="critical" if scope == "broad_form" else "high",
                        contract_value=scope,
                        standard_value=acceptable,
                        recommendation="Negotiate proportional or comparative fault "
                        "indemnification. Broad-form indemnification may be "
                        "unenforceable in many jurisdictions.",
                    )
                )

        elif term_type == "dispute_resolution":
            method = pv.get("primary_method", "").lower()
            acceptable = standard.get("acceptable_values", [])
            if method and method not in acceptable and method != "mediation_then_arbitration":
                deviations.append(
                    ContractDeviation(
                        clause_type=term_type,
                        description=f"Dispute resolution method '{method}' deviates from "
                        f"standard methods: {acceptable}",
                        severity="medium",
                        contract_value=method,
                        standard_value=acceptable,
                        recommendation="Consider mediation as the primary dispute "
                        "resolution method before arbitration.",
                    )
                )

        elif term_type == "termination":
            cure_days = pv.get("for_cause_cure_days")
            if cure_days is not None:
                min_ok = standard.get("min_acceptable", 7)
                if int(cure_days) < int(min_ok):
                    deviations.append(
                        ContractDeviation(
                            clause_type=term_type,
                            description=f"Termination cure period of {cure_days} days is below "
                            f"the standard minimum of {min_ok} days",
                            severity="high",
                            contract_value=cure_days,
                            standard_value=min_ok,
                            recommendation=f"Negotiate a minimum {min_ok}-day cure period "
                            "before termination for cause.",
                        )
                    )

    # Sort by severity: critical > high > medium > low
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    deviations.sort(key=lambda d: severity_order.get(d.severity, 4))

    logger.info(
        "Found %d deviations across %d clause types",
        len(deviations),
        len(clause_map),
    )
    return deviations


async def apply_contract_to_project(
    db: AsyncSession,
    contract_document_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Read extracted clauses and auto-populate project settings.

    Currently applies:
    - retainage_pct from retainage clause
    - payment_terms_days from payment_terms clause
    - ld_rate_per_day from liquidated_damages clause
    - warranty_months from warranty clause

    Args:
        db: Database session.
        contract_document_id: The contract document ID.
        project_id: The project to update.

    Returns:
        Dict describing what was applied.
    """
    from app.models.project import Project

    # Fetch clauses for this contract
    stmt = select(ContractClause).where(ContractClause.contract_document_id == contract_document_id)
    result = await db.execute(stmt)
    clause_rows = result.scalars().all()

    if not clause_rows:
        return {"applied": False, "reason": "No clauses found for this contract"}

    # Get project
    project = await db.get(Project, project_id)
    if project is None:
        return {"applied": False, "reason": "Project not found"}

    applied_settings: dict = {}
    settings = dict(project.settings) if project.settings else {}

    # Index clauses by type — keep highest confidence
    clause_map: dict[str, ContractClause] = {}
    for row in clause_rows:
        existing = clause_map.get(row.clause_type)
        if existing is None or float(row.confidence) > float(existing.confidence):
            clause_map[row.clause_type] = row

    # Apply retainage
    retainage = clause_map.get("retainage")
    if retainage and retainage.parsed_value:
        pct = retainage.parsed_value.get("percentage")
        if pct is not None:
            settings["retainage_pct"] = float(pct)
            applied_settings["retainage_pct"] = float(pct)

    # Apply payment terms
    payment = clause_map.get("payment_terms")
    if payment and payment.parsed_value:
        days = payment.parsed_value.get("net_days")
        if days is not None:
            settings["payment_terms_days"] = int(days)
            applied_settings["payment_terms_days"] = int(days)

    # Apply LD rate
    ld = clause_map.get("liquidated_damages")
    if ld and ld.parsed_value:
        rate = ld.parsed_value.get("rate_per_day")
        if rate is not None:
            settings["ld_rate_per_day"] = float(rate)
            applied_settings["ld_rate_per_day"] = float(rate)

    # Apply warranty
    warranty = clause_map.get("warranty")
    if warranty and warranty.parsed_value:
        months = warranty.parsed_value.get("duration_months")
        if months is not None:
            settings["warranty_months"] = int(months)
            applied_settings["warranty_months"] = int(months)

    if applied_settings:
        project.settings = settings
        await db.flush()
        logger.info(
            "Applied contract settings to project %s: %s",
            project_id,
            applied_settings,
        )

    # IG-02: Propagate retainage to PaymentIntegrationConfig if it exists
    pay_config_updated = False
    if "retainage_pct" in applied_settings:
        from app.models.instant_pay import PaymentIntegrationConfig

        pay_config_result = await db.execute(
            select(PaymentIntegrationConfig).where(
                PaymentIntegrationConfig.project_id == project_id
            )
        )
        pay_config = pay_config_result.scalar_one_or_none()
        retainage_val = Decimal(str(applied_settings["retainage_pct"]))

        if pay_config is not None:
            pay_config.retainage_pct = retainage_val
            pay_config_updated = True
            logger.info(
                "Updated PaymentIntegrationConfig retainage to %s%% for project %s",
                retainage_val,
                project_id,
            )
        else:
            # Create a new PaymentIntegrationConfig with the extracted retainage
            new_config = PaymentIntegrationConfig(
                project_id=project_id,
                processor_name="default",
                retainage_pct=retainage_val,
            )
            db.add(new_config)
            pay_config_updated = True
            logger.info(
                "Created PaymentIntegrationConfig with retainage %s%% for project %s",
                retainage_val,
                project_id,
            )

        # Also propagate payment_terms_days if available
        if "payment_terms_days" in applied_settings:
            if pay_config is not None:
                pay_config.payment_terms_days = int(applied_settings["payment_terms_days"])
            elif pay_config_updated:
                new_config.payment_terms_days = int(applied_settings["payment_terms_days"])

        if pay_config_updated:
            await db.flush()

    return {
        "applied": bool(applied_settings),
        "settings_updated": applied_settings,
        "contract_document_id": str(contract_document_id),
        "project_id": str(project_id),
        "payment_config_updated": pay_config_updated,
    }


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------


async def save_extracted_clauses(
    db: AsyncSession,
    contract_document_id: uuid.UUID,
    clauses: list[ExtractedClause],
) -> list[ContractClause]:
    """Persist extracted clauses to the database.

    SV-20: Now saves ALL clauses including multiple occurrences of the same
    type (e.g., multiple insurance requirements). The occurrence_index is
    stored in the parsed_value JSONB for downstream consumers.

    Returns the created ContractClause ORM objects.
    """
    rows: list[ContractClause] = []
    for clause in clauses:
        # SV-20: Include occurrence_index in parsed_value for multi-occurrence tracking
        pv = dict(clause.parsed_value)
        if clause.occurrence_index > 1:
            pv["_occurrence_index"] = clause.occurrence_index

        row = ContractClause(
            contract_document_id=contract_document_id,
            clause_type=clause.clause_type,
            clause_text=clause.clause_text,
            parsed_value=pv,
            section_reference=clause.section_reference,
            confidence=Decimal(str(clause.confidence)),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def save_comparison(
    db: AsyncSession,
    contract_a_id: uuid.UUID,
    contract_b_id: uuid.UUID,
    comparison: ComparisonResult,
    created_by: uuid.UUID | None = None,
) -> ContractComparison:
    """Persist a contract comparison to the database."""
    row = ContractComparison(
        contract_a_id=contract_a_id,
        contract_b_id=contract_b_id,
        differences={
            "additions": comparison.additions,
            "removals": comparison.removals,
            "changes": comparison.changes,
            "summary": comparison.summary,
        },
        deviations=[],
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row
