"""Construction-domain embedding fine-tuning pipeline.

Trains a sentence-transformer model (BAAI/bge-large-en-v1.5) on construction-
specific QA pairs from three sources:

1. IFC BIM QA dataset (~13,485 pairs)
2. OSHA 29 CFR 1926 XML → LLM-generated QA (~3,000 pairs)
3. Synthetic construction QA via LLM prompts (~5,000+ pairs)

Total target: 20,000+ construction-domain QA pairs.

Fine-tuning uses contrastive learning with hard negatives:
  - positive pair = (question, correct_answer)
  - hard negatives = (question, answer_from_different_section)

Usage:
    python -m app.ml.training.construction_embeddings \
        --ifc-dir ~/constructai-data/ifc-bim/ifc-bim-qa/ \
        --osha-xml ~/constructai-data/osha/cfr-title29-chapterXVII.xml \
        --output-dir models/construction-bge-large \
        --epochs 3 --batch-size 32

    # Generate data only (no training):
    python -m app.ml.training.construction_embeddings \
        --ifc-dir ~/constructai-data/ifc-bim/ifc-bim-qa/ \
        --osha-xml ~/constructai-data/osha/cfr-title29-chapterXVII.xml \
        --data-only --output-dir data/construction-qa
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as DefusedET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class QAPair:
    """A single question-answer pair with source metadata."""

    question: str
    answer: str
    source: str  # "ifc_bim", "osha", "synthetic"
    category: str = ""  # e.g., "1926.501", "concrete", "MEP"
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. IFC BIM QA dataset loader
# ---------------------------------------------------------------------------


def load_ifc_bim_qa(data_dir: str | Path) -> list[QAPair]:
    """Load the IFC BIM QA dataset from a directory of JSON/JSONL files.

    Expected formats:
    - JSONL: one JSON object per line with "question" and "answer" keys
    - JSON: array of objects or object with "data"/"qa_pairs" key

    Parameters
    ----------
    data_dir:
        Path to ~/constructai-data/ifc-bim/ifc-bim-qa/

    Returns
    -------
    list[QAPair]
        Parsed QA pairs (target: ~13,485).
    """
    data_dir = Path(data_dir)
    if not data_dir.exists():
        logger.warning("IFC BIM QA directory not found: %s", data_dir)
        return []

    pairs: list[QAPair] = []

    # Collect all JSON/JSONL files
    files = sorted(data_dir.glob("*.json")) + sorted(data_dir.glob("*.jsonl"))
    if not files:
        # Try CSV format
        csv_files = sorted(data_dir.glob("*.csv"))
        if csv_files:
            return _load_ifc_csv(csv_files)
        logger.warning("No JSON/JSONL/CSV files found in %s", data_dir)
        return []

    for filepath in files:
        try:
            if filepath.suffix == ".jsonl":
                pairs.extend(_parse_jsonl_qa(filepath))
            else:
                pairs.extend(_parse_json_qa(filepath))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", filepath, exc)

    logger.info("Loaded %d IFC BIM QA pairs from %s", len(pairs), data_dir)
    return pairs


def _parse_jsonl_qa(filepath: Path) -> list[QAPair]:
    """Parse a JSONL file with one QA object per line."""
    pairs: list[QAPair] = []
    with open(filepath, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                q, a = _extract_qa_fields(obj)
                if q and a:
                    pairs.append(
                        QAPair(
                            question=q,
                            answer=a,
                            source="ifc_bim",
                            category=obj.get("category", obj.get("topic", "")),
                            metadata={"file": filepath.name, "line": line_num},
                        )
                    )
            except json.JSONDecodeError:
                continue
    return pairs


def _parse_json_qa(filepath: Path) -> list[QAPair]:
    """Parse a JSON file containing QA pairs."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    # Handle different JSON structures
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Try common keys for the array
        for key in ("data", "qa_pairs", "questions", "items", "rows"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            items = [data]
    else:
        return []

    pairs: list[QAPair] = []
    for obj in items:
        if not isinstance(obj, dict):
            continue
        q, a = _extract_qa_fields(obj)
        if q and a:
            pairs.append(
                QAPair(
                    question=q,
                    answer=a,
                    source="ifc_bim",
                    category=obj.get("category", obj.get("topic", "")),
                    metadata={"file": filepath.name},
                )
            )
    return pairs


def _load_ifc_csv(csv_files: list[Path]) -> list[QAPair]:
    """Fallback: load QA pairs from CSV files."""
    import csv

    pairs: list[QAPair] = []
    for filepath in csv_files:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                q, a = _extract_qa_fields(row)
                if q and a:
                    pairs.append(
                        QAPair(
                            question=q,
                            answer=a,
                            source="ifc_bim",
                            category=row.get("category", ""),
                            metadata={"file": filepath.name},
                        )
                    )
    logger.info("Loaded %d IFC BIM QA pairs from CSV", len(pairs))
    return pairs


def _extract_qa_fields(obj: dict) -> tuple[str, str]:
    """Extract question and answer from a dict, handling key variations."""
    q_keys = ("question", "q", "query", "input", "prompt")
    a_keys = ("answer", "a", "response", "output", "completion")

    q = ""
    for k in q_keys:
        if obj.get(k):
            q = str(obj[k]).strip()
            break

    a = ""
    for k in a_keys:
        if obj.get(k):
            a = str(obj[k]).strip()
            break

    return q, a


# ---------------------------------------------------------------------------
# 2. OSHA 29 CFR 1926 XML parser + QA generation
# ---------------------------------------------------------------------------


@dataclass
class OshaSection:
    """A parsed OSHA standard section."""

    standard_number: str  # e.g., "1926.501"
    title: str
    text: str
    subpart: str = ""


def parse_osha_xml(xml_path: str | Path) -> list[OshaSection]:
    """Parse OSHA 29 CFR 1926 XML into individual standard sections.

    Handles the eCFR XML format where the hierarchy is:
    <CHAPTER> -> <SUBCHAP> -> <PART> -> <SUBPART> -> <SECTION>

    Parameters
    ----------
    xml_path:
        Path to cfr-title29-chapterXVII.xml

    Returns
    -------
    list[OshaSection]
        Parsed sections from Part 1926 (Construction).
    """
    xml_path = Path(xml_path)
    if not xml_path.exists():
        logger.warning("OSHA XML file not found: %s", xml_path)
        return []

    try:
        tree = DefusedET.parse(xml_path)
    except ET.ParseError as exc:
        logger.error("Failed to parse OSHA XML: %s", exc)
        return []

    root = tree.getroot()
    sections: list[OshaSection] = []

    # Try multiple XML structures - eCFR format varies
    # Strategy 1: Look for <SECTION> elements directly
    for section_el in root.iter("SECTION"):
        section = _parse_section_element(section_el)
        if section and "1926" in section.standard_number:
            sections.append(section)

    # Strategy 2: Look for <DIV8> elements (eCFR API format)
    if not sections:
        for div8 in root.iter("DIV8"):
            section = _parse_div8_element(div8)
            if section and "1926" in section.standard_number:
                sections.append(section)

    # Strategy 3: Generic section/part tags
    if not sections:
        sections = _parse_generic_xml(root)

    logger.info("Parsed %d OSHA 1926 sections from %s", len(sections), xml_path)
    return sections


def _parse_section_element(el: ET.Element) -> OshaSection | None:
    """Parse a <SECTION> element from eCFR XML."""
    # Get section number
    sectno = el.find("SECTNO")
    subject = el.find("SUBJECT")

    if sectno is None:
        return None

    number = (sectno.text or "").strip().replace("§ ", "").replace("§", "")
    title = (subject.text or "").strip() if subject is not None else ""

    # Collect all text content
    text_parts: list[str] = []
    for child in el:
        if child.tag in ("SECTNO", "SUBJECT"):
            continue
        text_parts.append(_element_text(child))

    text = "\n".join(t for t in text_parts if t.strip())
    if not text.strip():
        return None

    # Determine subpart from parent
    subpart = ""
    for _ancestor_tag in ("SUBPART", "SUBJGRP"):
        # Walk up if possible (ElementTree doesn't have parent links,
        # so we rely on the subpart being set later during post-processing)
        pass

    return OshaSection(
        standard_number=number,
        title=title,
        text=text,
        subpart=subpart,
    )


def _parse_div8_element(el: ET.Element) -> OshaSection | None:
    """Parse a <DIV8> element (eCFR API XML format)."""
    head = el.find("HEAD")
    if head is None:
        return None

    head_text = (head.text or "").strip()

    # Extract section number: "§ 1926.501 Duty to have fall protection."
    m = re.match(r"§\s*([\d.]+)\s*(.*)", head_text)
    if not m:
        return None

    number = m.group(1)
    title = m.group(2).strip().rstrip(".")

    # Collect paragraph text
    text_parts: list[str] = []
    for p in el.iter("P"):
        if p.text:
            text_parts.append(p.text.strip())

    text = "\n".join(text_parts)
    if not text.strip():
        return None

    return OshaSection(standard_number=number, title=title, text=text)


def _parse_generic_xml(root: ET.Element) -> list[OshaSection]:
    """Fallback parser for non-standard XML layouts."""
    sections: list[OshaSection] = []

    # Look for any element with section-number-like attributes or text
    section_pattern = re.compile(r"1926\.\d+")

    for el in root.iter():
        text_content = _element_text(el)
        if not text_content or len(text_content) < 50:
            continue

        # Check for section number in text
        m = section_pattern.search(text_content[:200])
        if m:
            number = m.group(0)
            # Use first line as title
            lines = text_content.strip().split("\n")
            title = lines[0][:200] if lines else ""
            body = "\n".join(lines[1:]) if len(lines) > 1 else text_content

            sections.append(
                OshaSection(
                    standard_number=number,
                    title=title,
                    text=body,
                )
            )

    return sections


def _element_text(el: ET.Element) -> str:
    """Recursively extract all text from an XML element."""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_element_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(parts)


def _assign_subparts(sections: list[OshaSection]) -> None:
    """Post-process sections to assign subpart labels based on standard numbers."""
    # OSHA 1926 subpart ranges
    subpart_ranges = [
        ("Subpart C - General Safety and Health Provisions", 1926.20, 1926.35),
        ("Subpart D - Occupational Health and Environmental Controls", 1926.50, 1926.66),
        ("Subpart E - Personal Protective and Life Saving Equipment", 1926.95, 1926.107),
        ("Subpart F - Fire Protection and Prevention", 1926.150, 1926.159),
        ("Subpart G - Signs, Signals, and Barricades", 1926.200, 1926.203),
        ("Subpart H - Materials Handling, Storage, Use, and Disposal", 1926.250, 1926.252),
        ("Subpart I - Tools - Hand and Power", 1926.300, 1926.307),
        ("Subpart J - Welding and Cutting", 1926.350, 1926.354),
        ("Subpart K - Electrical", 1926.400, 1926.449),
        ("Subpart L - Scaffolds", 1926.450, 1926.454),
        ("Subpart M - Fall Protection", 1926.500, 1926.503),
        ("Subpart N - Helicopters, Hoists, Elevators, and Conveyors", 1926.550, 1926.556),
        ("Subpart O - Motor Vehicles", 1926.600, 1926.606),
        ("Subpart P - Excavations", 1926.650, 1926.652),
        ("Subpart Q - Concrete and Masonry Construction", 1926.700, 1926.706),
        ("Subpart R - Steel Erection", 1926.750, 1926.761),
        ("Subpart S - Underground Construction", 1926.800, 1926.803),
        ("Subpart T - Demolition", 1926.850, 1926.860),
        ("Subpart U - Blasting and Use of Explosives", 1926.900, 1926.914),
        ("Subpart V - Power Transmission and Distribution", 1926.950, 1926.966),
        ("Subpart W - Rollover Protective Structures", 1926.1000, 1926.1003),
        ("Subpart X - Stairways and Ladders", 1926.1050, 1926.1060),
        ("Subpart Z - Toxic and Hazardous Substances", 1926.1100, 1926.1153),
        ("Subpart AA - Confined Spaces in Construction", 1926.1200, 1926.1213),
        ("Subpart CC - Cranes and Derricks", 1926.1400, 1926.1442),
    ]

    for section in sections:
        try:
            num = float(section.standard_number)
        except ValueError:
            continue

        # Find best-matching range (narrowest span) to handle float
        # overlap (e.g. Subpart D 1926.50-1926.66 vs M 1926.500-1926.503
        # both match 1926.501 as floats; M is the correct, narrower match)
        best_match = None
        best_span = float("inf")
        for subpart_name, low, high in subpart_ranges:
            if low <= num <= high:
                span = high - low
                if span < best_span:
                    best_span = span
                    best_match = subpart_name
        if best_match:
            section.subpart = best_match


async def generate_osha_qa_pairs(
    sections: list[OshaSection],
    *,
    pairs_per_section: int = 3,
    model: str = "gpt-4o-mini",
) -> list[QAPair]:
    """Generate QA pairs from OSHA sections using an LLM.

    For each OSHA section, calls the LLM with a prompt to generate realistic
    questions a construction professional might ask.

    Parameters
    ----------
    sections:
        Parsed OSHA standard sections.
    pairs_per_section:
        Number of QA pairs to generate per section (default 3).
    model:
        LLM model to use for generation.

    Returns
    -------
    list[QAPair]
        Generated QA pairs (target: ~3,000).
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    all_pairs: list[QAPair] = []

    prompt_template = (
        "Given this OSHA construction safety standard, generate {n} realistic "
        "questions a construction professional might ask that this standard "
        "answers. The questions should sound like actual field questions from "
        "superintendents, safety managers, or project engineers.\n\n"
        "Standard: {standard_number} - {title}\n\n"
        "Text:\n{text}\n\n"
        "Format your response as a JSON array: "
        '[{{"question": "...", "answer": "..."}}]\n'
        "Each answer should cite the standard number and be 2-4 sentences."
    )

    # L-28: Process in batches AND cap concurrency with a semaphore. The
    # previous `asyncio.gather` over a batch could still fan out 5 calls
    # simultaneously; with many batches back-to-back we've seen OpenAI
    # RateLimitError. Keeping concurrency at 3 is well under the tier-1
    # limit while still 3x faster than serial.
    batch_size = 5
    _sem = asyncio.Semaphore(3)

    async def _with_sem(section):
        async with _sem:
            return await _generate_qa_for_section(
                client, section, prompt_template, pairs_per_section, model
            )

    for i in range(0, len(sections), batch_size):
        batch = sections[i : i + batch_size]
        tasks = [_with_sem(section) for section in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for section, result in zip(batch, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning("QA generation failed for %s: %s", section.standard_number, result)
                continue
            # `result` narrowed to the success type here; extend with the QA pairs.
            all_pairs.extend(result)  # type: ignore[arg-type]

        if (i + batch_size) % 50 == 0:
            logger.info(
                "Generated QA for %d / %d sections",
                min(i + batch_size, len(sections)),
                len(sections),
            )

    logger.info("Generated %d OSHA QA pairs from %d sections", len(all_pairs), len(sections))
    return all_pairs


async def _generate_qa_for_section(
    client,
    section: OshaSection,
    prompt_template: str,
    n: int,
    model: str,
) -> list[QAPair]:
    """Generate QA pairs for a single OSHA section."""
    # Truncate very long sections to fit context
    text = section.text[:3000]

    prompt = prompt_template.format(
        n=n,
        standard_number=section.standard_number,
        title=section.title,
        text=text,
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a construction safety expert. Generate realistic QA pairs.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""

    # Parse JSON response
    try:
        data = json.loads(content)
        # Handle both array and object-wrapped responses
        if isinstance(data, dict):
            for key in ("pairs", "questions", "qa_pairs", "data"):
                if key in data:
                    data = data[key]
                    break
            else:
                data = [data]
        if not isinstance(data, list):
            return []
    except json.JSONDecodeError:
        # Try to extract JSON array from text
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    pairs: list[QAPair] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = item.get("question", "").strip()
        a = item.get("answer", "").strip()
        if q and a:
            pairs.append(
                QAPair(
                    question=q,
                    answer=a,
                    source="osha",
                    category=section.standard_number,
                    metadata={
                        "subpart": section.subpart,
                        "title": section.title,
                    },
                )
            )

    return pairs


# ---------------------------------------------------------------------------
# 3. Synthetic construction QA generation
# ---------------------------------------------------------------------------

# Diverse prompt templates for synthetic QA generation
_SYNTHETIC_PROMPTS = [
    (
        "Generate 20 realistic RFI questions and answers for a commercial building "
        "project. Cover: concrete work, structural steel, and foundations. "
        "Questions should reference specific spec sections and drawing details."
    ),
    (
        "Generate 20 realistic RFI questions and answers about MEP (mechanical, "
        "electrical, plumbing) coordination on a large construction project. "
        "Include conflicts between trades, routing questions, and clearance issues."
    ),
    (
        "Generate 20 realistic RFI questions and answers about interior finishes "
        "on a commercial project: drywall, painting, flooring, ceiling tiles, "
        "and millwork. Include material substitution requests."
    ),
    (
        "Generate 20 realistic RFI questions and answers about sitework and "
        "earthwork: grading, drainage, utilities, paving, and landscaping."
    ),
    (
        "Generate 20 construction submittal review questions and answers. "
        "Cover shop drawings for structural steel, product data for roofing, "
        "material samples for finishes, and LEED documentation."
    ),
    (
        "Generate 20 questions a field engineer might ask about reading "
        "construction drawings: plan notes, detail references, dimension "
        "conflicts, and spec vs drawing discrepancies."
    ),
    (
        "Generate 20 realistic questions about construction scheduling: "
        "critical path, activity sequencing, weather delays, resource "
        "leveling, and schedule recovery strategies."
    ),
    (
        "Generate 20 questions about construction safety requirements: "
        "fall protection, excavation safety, scaffolding, electrical safety, "
        "crane operations, and confined spaces."
    ),
    (
        "Generate 20 questions about waterproofing and building envelope: "
        "below-grade waterproofing, air barriers, flashing details, "
        "window installation, and roofing systems."
    ),
    (
        "Generate 20 questions about concrete construction: mix designs, "
        "cold/hot weather concreting, formwork, reinforcement placement, "
        "post-tensioning, curing requirements, and testing."
    ),
    (
        "Generate 20 questions about structural steel construction: "
        "connection details, bolt torque requirements, welding procedures, "
        "field modifications, and steel erection sequencing."
    ),
    (
        "Generate 20 questions about fire protection systems in buildings: "
        "sprinkler layouts, standpipe systems, fire-rated assemblies, "
        "firestopping, and fire alarm systems."
    ),
    (
        "Generate 20 questions about construction cost estimating: "
        "quantity takeoffs, unit pricing, contingencies, change order "
        "pricing, and value engineering proposals."
    ),
    (
        "Generate 20 BIM coordination questions: clash detection, model "
        "element LOD, federated model issues, BIM execution plans, "
        "and IFC data exchange problems."
    ),
    (
        "Generate 20 questions about masonry construction: CMU walls, "
        "brick veneer, mortar types, reinforcement, control joints, "
        "and flashing at masonry-to-frame transitions."
    ),
]


async def generate_synthetic_qa(
    *,
    target_count: int = 5000,
    model: str = "gpt-4o-mini",
    spec_pdfs_dir: str | Path | None = None,
) -> list[QAPair]:
    """Generate synthetic construction QA pairs using LLM prompts.

    Parameters
    ----------
    target_count:
        Minimum number of QA pairs to generate.
    model:
        LLM model to use.
    spec_pdfs_dir:
        Optional directory with sample spec PDFs to ingest. If not available
        or empty, uses fully synthetic generation.

    Returns
    -------
    list[QAPair]
        Synthetic QA pairs (target: 5,000+).
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    all_pairs: list[QAPair] = []

    # Calculate how many rounds we need
    pairs_per_prompt = 20
    rounds_needed = max(1, target_count // (len(_SYNTHETIC_PROMPTS) * pairs_per_prompt)) + 1

    for round_num in range(rounds_needed):
        if len(all_pairs) >= target_count:
            break

        for prompt_text in _SYNTHETIC_PROMPTS:
            if len(all_pairs) >= target_count:
                break

            # Add variation for subsequent rounds
            if round_num > 0:
                prompt_text = (
                    f"{prompt_text}\n\nThis is variation {round_num + 1}. "
                    f"Use different scenarios, building types, and specific details "
                    f"than previous rounds. Focus on edge cases and unusual situations."
                )

            try:
                pairs = await _generate_synthetic_batch(client, prompt_text, model)
                all_pairs.extend(pairs)
            except Exception as exc:
                logger.warning("Synthetic QA generation failed: %s", exc)
                continue

        logger.info(
            "Synthetic QA round %d complete: %d total pairs",
            round_num + 1,
            len(all_pairs),
        )

    logger.info("Generated %d synthetic construction QA pairs", len(all_pairs))
    return all_pairs


async def _generate_synthetic_batch(
    client,
    prompt_text: str,
    model: str,
) -> list[QAPair]:
    """Generate a batch of synthetic QA pairs from a single prompt."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an experienced construction project manager and engineer. "
                    "Generate realistic, detailed question-answer pairs that would appear "
                    "in actual construction project RFIs, submittals, and coordination meetings. "
                    "Include specific materials, dimensions, standards (ASTM, ACI, AISC), "
                    "and CSI MasterFormat section references where appropriate."
                ),
            },
            {
                "role": "user",
                "content": prompt_text
                + '\n\nFormat as JSON array: [{"question": "...", "answer": "..."}]',
            },
        ],
        temperature=0.8,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""

    try:
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("pairs", "questions", "qa_pairs", "data", "items"):
                if key in data:
                    data = data[key]
                    break
            else:
                data = [data]
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            return []

    pairs: list[QAPair] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = item.get("question", "").strip()
        a = item.get("answer", "").strip()
        if q and a and len(q) > 10 and len(a) > 20:
            # Detect category from content
            category = _detect_category(q + " " + a)
            pairs.append(
                QAPair(
                    question=q,
                    answer=a,
                    source="synthetic",
                    category=category,
                )
            )

    return pairs


def _detect_category(text: str) -> str:
    """Simple keyword-based category detection for synthetic QA pairs."""
    text_lower = text.lower()
    categories = [
        ("concrete", ["concrete", "rebar", "formwork", "slab", "footing", "pour"]),
        ("structural_steel", ["steel", "beam", "column", "weld", "bolt", "erection"]),
        ("mep", ["mechanical", "electrical", "plumbing", "hvac", "duct", "conduit"]),
        ("finishes", ["drywall", "paint", "tile", "flooring", "ceiling"]),
        ("sitework", ["grading", "excavation", "drainage", "paving", "utility"]),
        ("roofing", ["roof", "membrane", "flashing", "insulation"]),
        ("safety", ["osha", "fall protection", "scaffold", "safety", "ppe"]),
        ("scheduling", ["schedule", "critical path", "delay", "sequence"]),
        ("bim", ["bim", "clash", "model", "ifc", "revit"]),
        ("masonry", ["masonry", "brick", "block", "mortar", "cmu"]),
    ]

    for cat_name, keywords in categories:
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits >= 2:
            return cat_name

    return "general"


# ---------------------------------------------------------------------------
# 4. Dataset preparation for contrastive learning
# ---------------------------------------------------------------------------


def prepare_training_data(
    pairs: list[QAPair],
    *,
    test_split: float = 0.1,
    hard_negatives_per_pair: int = 3,
    seed: int = 42,
) -> dict:
    """Prepare QA pairs for contrastive learning training.

    Creates training examples with:
    - Positive pair: (question, correct_answer)
    - Hard negatives: answers from different sections/categories

    Parameters
    ----------
    pairs:
        All collected QA pairs.
    test_split:
        Fraction of data to hold out for evaluation.
    hard_negatives_per_pair:
        Number of hard negative answers per question.
    seed:
        Random seed for reproducibility.

    Returns
    -------
    dict with keys: train_examples, eval_examples, stats
    """
    # M-31: Seed every RNG we might hit during training, not just `random`.
    # Without numpy + torch seeds the augmentation, hard-negative sampling,
    # and PyTorch dataloader workers all behave non-deterministically —
    # defeating the point of accepting a ``seed`` argument.
    random.seed(seed)
    try:
        import numpy as _np

        _np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch as _torch

        _torch.manual_seed(seed)
        if _torch.cuda.is_available():
            _torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass

    # Shuffle and split
    shuffled = list(pairs)
    random.shuffle(shuffled)

    split_idx = int(len(shuffled) * (1 - test_split))
    train_pairs = shuffled[:split_idx]
    eval_pairs = shuffled[split_idx:]

    # Group answers by category for hard negative mining
    category_answers: dict[str, list[str]] = {}
    for pair in train_pairs:
        cat = pair.category or "general"
        if cat not in category_answers:
            category_answers[cat] = []
        category_answers[cat].append(pair.answer)

    all_answers = [p.answer for p in train_pairs]

    # Build training examples with hard negatives
    train_examples = []
    for pair in train_pairs:
        # Find hard negatives: answers from different categories
        negatives = []
        other_categories = [c for c in category_answers if c != (pair.category or "general")]

        # Sample from other categories first (harder negatives)
        for _ in range(hard_negatives_per_pair):
            if other_categories:
                neg_cat = random.choice(other_categories)
                neg_answer = random.choice(category_answers[neg_cat])
            else:
                neg_answer = random.choice(all_answers)
            # Ensure negative is different from positive
            if neg_answer != pair.answer:
                negatives.append(neg_answer)

        train_examples.append(
            {
                "query": pair.question,
                "positive": pair.answer,
                "negatives": negatives,
                "source": pair.source,
                "category": pair.category,
            }
        )

    eval_examples = [
        {"query": p.question, "positive": p.answer, "source": p.source} for p in eval_pairs
    ]

    stats: dict[str, Any] = {
        "total_pairs": len(pairs),
        "train_pairs": len(train_examples),
        "eval_pairs": len(eval_examples),
        "sources": {},
        "categories": {},
    }
    for p in pairs:
        stats["sources"][p.source] = stats["sources"].get(p.source, 0) + 1
        cat = p.category or "general"
        stats["categories"][cat] = stats["categories"].get(cat, 0) + 1

    logger.info(
        "Prepared training data: %d train, %d eval, sources=%s",
        len(train_examples),
        len(eval_examples),
        stats["sources"],
    )
    return {
        "train_examples": train_examples,
        "eval_examples": eval_examples,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# 5. Fine-tuning with sentence-transformers
# ---------------------------------------------------------------------------


def fine_tune_embeddings(
    training_data: dict,
    *,
    base_model: str = "BAAI/bge-large-en-v1.5",
    output_dir: str = "models/construction-bge-large",
    epochs: int = 3,
    batch_size: int = 32,
    learning_rate: float = 2e-5,
    warmup_ratio: float = 0.1,
) -> dict:
    """Fine-tune a sentence-transformer model with contrastive learning.

    Uses MultipleNegativesRankingLoss (InfoNCE/contrastive loss) which
    treats the (query, positive) as the positive pair and all other
    positives in the batch as in-batch negatives.

    Parameters
    ----------
    training_data:
        Output from prepare_training_data().
    base_model:
        HuggingFace model ID for the base embedding model.
    output_dir:
        Directory to save the fine-tuned model.
    epochs:
        Number of training epochs.
    batch_size:
        Training batch size.
    learning_rate:
        Learning rate for AdamW optimizer.
    warmup_ratio:
        Fraction of steps for learning rate warmup.

    Returns
    -------
    dict with training results and evaluation metrics.
    """
    try:
        from sentence_transformers import InputExample, SentenceTransformer, losses
        from sentence_transformers.evaluation import InformationRetrievalEvaluator
        from torch.utils.data import DataLoader
    except ImportError:
        raise ImportError(
            "sentence-transformers is required for fine-tuning. "
            "Install with: pip install sentence-transformers torch"
        )

    logger.info("Loading base model: %s", base_model)
    model = SentenceTransformer(base_model)

    # Prepare training examples for MultipleNegativesRankingLoss
    # Format: InputExample(texts=[query, positive, negative1, negative2, ...])
    train_examples_st = []
    for example in training_data["train_examples"]:
        texts = [example["query"], example["positive"]]
        texts.extend(example.get("negatives", []))
        train_examples_st.append(InputExample(texts=texts))

    train_dataloader = DataLoader(
        train_examples_st,
        shuffle=True,
        batch_size=batch_size,
    )

    # Loss function: MultipleNegativesRankingLoss (contrastive)
    train_loss = losses.MultipleNegativesRankingLoss(model)

    # Prepare evaluation
    eval_examples = training_data["eval_examples"]
    evaluator = None

    if eval_examples:
        # Build IR evaluation data
        queries = {}
        corpus = {}
        relevant_docs = {}

        for idx, ex in enumerate(eval_examples):
            q_id = f"q_{idx}"
            c_id = f"c_{idx}"
            queries[q_id] = ex["query"]
            corpus[c_id] = ex["positive"]
            relevant_docs[q_id] = {c_id}

        evaluator = InformationRetrievalEvaluator(
            queries=queries,
            corpus=corpus,
            relevant_docs=relevant_docs,
            name="construction-eval",
            show_progress_bar=True,
        )

    # Calculate training steps
    total_steps = len(train_dataloader) * epochs
    warmup_steps = int(total_steps * warmup_ratio)

    logger.info(
        "Starting fine-tuning: %d examples, %d epochs, batch_size=%d, lr=%s",
        len(train_examples_st),
        epochs,
        batch_size,
        learning_rate,
    )

    # Train
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=epochs,
        warmup_steps=warmup_steps,
        evaluator=evaluator,
        evaluation_steps=max(100, len(train_dataloader) // 2),
        output_path=output_dir,
        optimizer_params={"lr": learning_rate},
        show_progress_bar=True,
        use_amp=True,  # Mixed precision for speed
    )

    logger.info("Fine-tuning complete. Model saved to: %s", output_dir)

    # Run final evaluation
    results = {}
    if evaluator:
        results = evaluator(model, output_path=output_dir)
        logger.info("Final evaluation: %s", results)

    return {
        "model_path": output_dir,
        "base_model": base_model,
        "train_examples": len(train_examples_st),
        "eval_examples": len(eval_examples),
        "epochs": epochs,
        "evaluation_results": results,
    }


# ---------------------------------------------------------------------------
# 6. Embedding inference with fine-tuned model
# ---------------------------------------------------------------------------


class ConstructionEmbedder:
    """Embedding generator using the fine-tuned construction model.

    Falls back to Voyage AI if the local model is not available.
    """

    def __init__(
        self,
        model_path: str = "models/construction-bge-large",
        fallback_to_voyage: bool = True,
        device: str | None = None,
    ):
        self._model_path = model_path
        self._fallback_to_voyage = fallback_to_voyage
        self._device = device
        self._model = None
        self._use_local = False

        self._try_load_local()

    def _try_load_local(self) -> None:
        """Attempt to load the fine-tuned local model."""
        if not Path(self._model_path).exists():
            logger.info(
                "Fine-tuned model not found at %s, will use fallback",
                self._model_path,
            )
            return

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._model_path,
                device=self._device,
            )
            self._use_local = True
            logger.info("Loaded fine-tuned construction embedding model from %s", self._model_path)
        except Exception as exc:
            logger.warning("Failed to load fine-tuned model: %s", exc)

    @property
    def model_name(self) -> str:
        """Return the active model name for metadata tracking."""
        if self._use_local:
            return f"construction-bge-finetuned:{self._model_path}"
        return "voyage-3-large"

    @property
    def dimensions(self) -> int:
        """Return the embedding dimension count."""
        if self._use_local and self._model is not None:
            return self._model.get_sentence_embedding_dimension()
        return 1024  # Voyage AI default

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for document chunks (synchronous).

        Parameters
        ----------
        texts:
            List of document text chunks.

        Returns
        -------
        list[list[float]]
            Embedding vectors.
        """
        if not texts:
            return []

        if self._use_local and self._model is not None:
            embeddings = self._model.encode(
                texts,
                batch_size=64,
                show_progress_bar=len(texts) > 100,
                normalize_embeddings=True,
            )
            return embeddings.tolist()

        if self._fallback_to_voyage:
            return asyncio.get_event_loop().run_until_complete(
                self._embed_via_voyage(texts, input_type="document")
            )

        raise RuntimeError("No embedding model available")

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """Generate embeddings for search queries (synchronous).

        Uses query-specific encoding if available (BGE uses instruction prefix).
        """
        if not queries:
            return []

        if self._use_local and self._model is not None:
            # BGE models use an instruction prefix for queries
            prefixed = [
                f"Represent this sentence for searching relevant passages: {q}" for q in queries
            ]
            embeddings = self._model.encode(
                prefixed,
                batch_size=64,
                normalize_embeddings=True,
            )
            return embeddings.tolist()

        if self._fallback_to_voyage:
            return asyncio.get_event_loop().run_until_complete(
                self._embed_via_voyage(queries, input_type="query")
            )

        raise RuntimeError("No embedding model available")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Async version of embed_documents."""
        if self._use_local and self._model is not None:
            # sentence-transformers is sync, run in thread pool
            import asyncio

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.embed_documents, texts)

        if self._fallback_to_voyage:
            return await self._embed_via_voyage(texts, input_type="document")

        raise RuntimeError("No embedding model available")

    async def aembed_queries(self, queries: list[str]) -> list[list[float]]:
        """Async version of embed_queries."""
        if self._use_local and self._model is not None:
            import asyncio

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.embed_queries, queries)

        if self._fallback_to_voyage:
            return await self._embed_via_voyage(queries, input_type="query")

        raise RuntimeError("No embedding model available")

    @staticmethod
    async def _embed_via_voyage(
        texts: list[str],
        input_type: str = "document",
    ) -> list[list[float]]:
        """Fallback: embed via Voyage AI API."""
        import voyageai

        client = voyageai.AsyncClient()
        response = await client.embed(
            texts=texts,
            model="voyage-3-large",
            input_type=input_type,
        )
        # Voyage SDK returns list[list[float|int]]; normalise to list[list[float]].
        return [[float(v) for v in emb] for emb in response.embeddings]


# ---------------------------------------------------------------------------
# 7. Data export / persistence
# ---------------------------------------------------------------------------


def save_qa_dataset(pairs: list[QAPair], output_path: str | Path) -> None:
    """Save QA pairs to a JSONL file for reproducibility."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            obj = {
                "question": pair.question,
                "answer": pair.answer,
                "source": pair.source,
                "category": pair.category,
                "metadata": pair.metadata,
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    logger.info("Saved %d QA pairs to %s", len(pairs), output_path)


def load_qa_dataset(input_path: str | Path) -> list[QAPair]:
    """Load QA pairs from a previously saved JSONL file."""
    input_path = Path(input_path)
    if not input_path.exists():
        return []

    pairs: list[QAPair] = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line.strip())
            pairs.append(
                QAPair(
                    question=obj["question"],
                    answer=obj["answer"],
                    source=obj.get("source", "unknown"),
                    category=obj.get("category", ""),
                    metadata=obj.get("metadata", {}),
                )
            )

    logger.info("Loaded %d QA pairs from %s", len(pairs), input_path)
    return pairs


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


async def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full data collection and training pipeline."""
    all_pairs: list[QAPair] = []

    # 1. Load IFC BIM QA dataset
    if args.ifc_dir:
        ifc_pairs = load_ifc_bim_qa(args.ifc_dir)
        all_pairs.extend(ifc_pairs)
        logger.info("IFC BIM QA: %d pairs", len(ifc_pairs))

    # 2. Parse OSHA XML and generate QA
    if args.osha_xml:
        osha_sections = parse_osha_xml(args.osha_xml)
        _assign_subparts(osha_sections)
        logger.info("Parsed %d OSHA sections", len(osha_sections))

        if osha_sections:
            osha_pairs = await generate_osha_qa_pairs(
                osha_sections,
                pairs_per_section=args.osha_pairs_per_section,
                model=args.model,
            )
            all_pairs.extend(osha_pairs)
            logger.info("OSHA QA: %d pairs", len(osha_pairs))

    # 3. Generate synthetic QA
    if not args.skip_synthetic:
        synthetic_pairs = await generate_synthetic_qa(
            target_count=args.synthetic_target,
            model=args.model,
        )
        all_pairs.extend(synthetic_pairs)
        logger.info("Synthetic QA: %d pairs", len(synthetic_pairs))

    logger.info("TOTAL QA PAIRS: %d", len(all_pairs))

    # Save dataset
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    save_qa_dataset(all_pairs, output_dir / "construction_qa_pairs.jsonl")

    if args.data_only:
        logger.info("Data-only mode. Skipping training.")
        return

    # 4. Prepare training data
    training_data = prepare_training_data(
        all_pairs,
        test_split=args.test_split,
        hard_negatives_per_pair=args.hard_negatives,
    )

    # Save training data
    with open(output_dir / "training_stats.json", "w") as f:
        json.dump(training_data["stats"], f, indent=2)

    # 5. Fine-tune
    results = fine_tune_embeddings(
        training_data,
        base_model=args.base_model,
        output_dir=str(output_dir),
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )

    # Save results
    with open(output_dir / "training_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info("Pipeline complete. Results saved to %s", output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Construction-domain embedding fine-tuning pipeline."
    )
    parser.add_argument(
        "--ifc-dir",
        type=Path,
        default=None,
        help="Path to IFC BIM QA dataset directory (~/constructai-data/ifc-bim/ifc-bim-qa/)",
    )
    parser.add_argument(
        "--osha-xml",
        type=Path,
        default=None,
        help="Path to OSHA 29 CFR 1926 XML file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="models/construction-bge-large",
        help="Output directory for model and data (default: models/construction-bge-large)",
    )
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="Only generate and save QA data, skip fine-tuning",
    )
    parser.add_argument(
        "--skip-synthetic",
        action="store_true",
        help="Skip synthetic QA generation",
    )
    parser.add_argument(
        "--synthetic-target",
        type=int,
        default=5000,
        help="Target number of synthetic QA pairs (default: 5000)",
    )
    parser.add_argument(
        "--osha-pairs-per-section",
        type=int,
        default=3,
        help="QA pairs to generate per OSHA section (default: 3)",
    )
    parser.add_argument(
        "--base-model",
        type=str,
        default="BAAI/bge-large-en-v1.5",
        help="Base HuggingFace model for fine-tuning",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="LLM model for QA generation (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=3,
        help="Training epochs (default: 3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Training batch size (default: 32)",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=2e-5,
        help="Learning rate (default: 2e-5)",
    )
    parser.add_argument(
        "--test-split",
        type=float,
        default=0.1,
        help="Fraction of data for evaluation (default: 0.1)",
    )
    parser.add_argument(
        "--hard-negatives",
        type=int,
        default=3,
        help="Hard negatives per training example (default: 3)",
    )

    args = parser.parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
