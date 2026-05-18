#!/usr/bin/env python3
"""Ingest OSHA 29 CFR 1926 construction safety standards into the RAG knowledge base.

Parses the eCFR XML file, chunks by section/subpart, embeds each chunk,
and stores in the pgvector table with document_type="osha_standard" metadata.

Usage:
    python scripts/ingest_osha_standards.py \
        --xml ~/constructai-data/osha/cfr-title29-chapterXVII.xml

    python scripts/ingest_osha_standards.py \
        --xml ~/constructai-data/osha/cfr-title29-chapterXVII.xml \
        --dry-run

    python scripts/ingest_osha_standards.py \
        --xml ~/constructai-data/osha/cfr-title29-chapterXVII.xml \
        --db-url postgresql+asyncpg://user:pass@localhost/constructai
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Add parent to path for app imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ml.training.construction_embeddings import (
    OshaSection,
    _assign_subparts,
    parse_osha_xml,
)

# ---------------------------------------------------------------------------
# OSHA activity-to-standard mapping
# ---------------------------------------------------------------------------

# Maps common construction activities to relevant OSHA 1926 standard sections.
ACTIVITY_STANDARD_MAP: dict[str, list[dict]] = {
    "excavation": [
        {
            "standard": "1926.650",
            "subpart": "Subpart P",
            "topic": "Excavations - General Requirements",
        },
        {
            "standard": "1926.651",
            "subpart": "Subpart P",
            "topic": "Excavations - Specific Requirements",
        },
        {
            "standard": "1926.652",
            "subpart": "Subpart P",
            "topic": "Excavations - Protective Systems",
        },
    ],
    "fall_protection": [
        {
            "standard": "1926.500",
            "subpart": "Subpart M",
            "topic": "Fall Protection - Scope/Definitions",
        },
        {"standard": "1926.501", "subpart": "Subpart M", "topic": "Fall Protection - Duty to Have"},
        {
            "standard": "1926.502",
            "subpart": "Subpart M",
            "topic": "Fall Protection - Systems Criteria",
        },
        {"standard": "1926.503", "subpart": "Subpart M", "topic": "Fall Protection - Training"},
    ],
    "scaffolding": [
        {"standard": "1926.450", "subpart": "Subpart L", "topic": "Scaffolds - Scope/Definitions"},
        {
            "standard": "1926.451",
            "subpart": "Subpart L",
            "topic": "Scaffolds - General Requirements",
        },
        {
            "standard": "1926.452",
            "subpart": "Subpart L",
            "topic": "Scaffolds - Additional Requirements",
        },
        {"standard": "1926.453", "subpart": "Subpart L", "topic": "Aerial Lifts"},
        {"standard": "1926.454", "subpart": "Subpart L", "topic": "Scaffolds - Training"},
    ],
    "electrical_work": [
        {"standard": "1926.400", "subpart": "Subpart K", "topic": "Electrical - General"},
        {"standard": "1926.402", "subpart": "Subpart K", "topic": "Electrical - Applicability"},
        {"standard": "1926.404", "subpart": "Subpart K", "topic": "Electrical - Wiring Design"},
        {"standard": "1926.405", "subpart": "Subpart K", "topic": "Electrical - Wiring Methods"},
        {
            "standard": "1926.416",
            "subpart": "Subpart K",
            "topic": "Electrical - Safety-Related Work",
        },
        {"standard": "1926.431", "subpart": "Subpart K", "topic": "Electrical - Maintenance"},
        {"standard": "1926.449", "subpart": "Subpart K", "topic": "Electrical - Definitions"},
    ],
    "steel_erection": [
        {"standard": "1926.750", "subpart": "Subpart R", "topic": "Steel Erection - Scope"},
        {"standard": "1926.751", "subpart": "Subpart R", "topic": "Steel Erection - Definitions"},
        {"standard": "1926.752", "subpart": "Subpart R", "topic": "Steel Erection - Site Layout"},
        {
            "standard": "1926.753",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Hoisting and Rigging",
        },
        {
            "standard": "1926.754",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Structural Steel Assembly",
        },
        {
            "standard": "1926.755",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Column Anchorage",
        },
        {
            "standard": "1926.756",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Beams and Columns",
        },
        {
            "standard": "1926.757",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Open Web Joists",
        },
        {
            "standard": "1926.760",
            "subpart": "Subpart R",
            "topic": "Steel Erection - Fall Protection",
        },
        {"standard": "1926.761", "subpart": "Subpart R", "topic": "Steel Erection - Training"},
    ],
    "crane_operations": [
        {"standard": "1926.1400", "subpart": "Subpart CC", "topic": "Cranes and Derricks - Scope"},
        {"standard": "1926.1401", "subpart": "Subpart CC", "topic": "Cranes - Definitions"},
        {"standard": "1926.1402", "subpart": "Subpart CC", "topic": "Cranes - Ground Conditions"},
        {
            "standard": "1926.1404",
            "subpart": "Subpart CC",
            "topic": "Cranes - Assembly/Disassembly",
        },
        {"standard": "1926.1407", "subpart": "Subpart CC", "topic": "Cranes - Power Line Safety"},
        {
            "standard": "1926.1408",
            "subpart": "Subpart CC",
            "topic": "Cranes - Power Line Safety (>350kV)",
        },
        {"standard": "1926.1412", "subpart": "Subpart CC", "topic": "Cranes - Inspections"},
        {"standard": "1926.1424", "subpart": "Subpart CC", "topic": "Cranes - Work Area Control"},
        {
            "standard": "1926.1425",
            "subpart": "Subpart CC",
            "topic": "Cranes - Keeping Clear of Power Lines",
        },
        {
            "standard": "1926.1427",
            "subpart": "Subpart CC",
            "topic": "Cranes - Operator Qualification",
        },
        {"standard": "1926.1431", "subpart": "Subpart CC", "topic": "Cranes - Hoisting Personnel"},
    ],
    "concrete_construction": [
        {"standard": "1926.700", "subpart": "Subpart Q", "topic": "Concrete and Masonry - Scope"},
        {
            "standard": "1926.701",
            "subpart": "Subpart Q",
            "topic": "Concrete - General Requirements",
        },
        {"standard": "1926.702", "subpart": "Subpart Q", "topic": "Concrete - Equipment and Tools"},
        {"standard": "1926.703", "subpart": "Subpart Q", "topic": "Concrete - Formwork"},
        {"standard": "1926.704", "subpart": "Subpart Q", "topic": "Concrete - Precast"},
        {
            "standard": "1926.705",
            "subpart": "Subpart Q",
            "topic": "Concrete - Lift-Slab Operations",
        },
        {"standard": "1926.706", "subpart": "Subpart Q", "topic": "Masonry Construction"},
    ],
    "demolition": [
        {
            "standard": "1926.850",
            "subpart": "Subpart T",
            "topic": "Demolition - Preparatory Operations",
        },
        {
            "standard": "1926.851",
            "subpart": "Subpart T",
            "topic": "Demolition - Stairs, Passageways, Ladders",
        },
        {"standard": "1926.852", "subpart": "Subpart T", "topic": "Demolition - Chutes"},
        {
            "standard": "1926.853",
            "subpart": "Subpart T",
            "topic": "Demolition - Removal of Materials",
        },
        {
            "standard": "1926.854",
            "subpart": "Subpart T",
            "topic": "Demolition - Removal of Walls/Floors",
        },
        {
            "standard": "1926.855",
            "subpart": "Subpart T",
            "topic": "Demolition - Manual Removal of Floors",
        },
        {
            "standard": "1926.856",
            "subpart": "Subpart T",
            "topic": "Demolition - Removal of Walls/Chimneys",
        },
        {"standard": "1926.858", "subpart": "Subpart T", "topic": "Demolition - Removal of Steel"},
        {"standard": "1926.860", "subpart": "Subpart T", "topic": "Demolition - Selective"},
    ],
    "confined_spaces": [
        {"standard": "1926.1200", "subpart": "Subpart AA", "topic": "Confined Spaces - Scope"},
        {
            "standard": "1926.1201",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Scope (cont.)",
        },
        {
            "standard": "1926.1202",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Definitions",
        },
        {
            "standard": "1926.1203",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - General Requirements",
        },
        {
            "standard": "1926.1204",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Permit-Required",
        },
        {
            "standard": "1926.1206",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Entry by Others",
        },
        {"standard": "1926.1207", "subpart": "Subpart AA", "topic": "Confined Spaces - Training"},
        {
            "standard": "1926.1208",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Duties of Entry Employers",
        },
        {
            "standard": "1926.1211",
            "subpart": "Subpart AA",
            "topic": "Confined Spaces - Rescue and Emergency",
        },
        {"standard": "1926.1213", "subpart": "Subpart AA", "topic": "Confined Spaces - Permit"},
    ],
    "welding": [
        {"standard": "1926.350", "subpart": "Subpart J", "topic": "Gas Welding and Cutting"},
        {"standard": "1926.351", "subpart": "Subpart J", "topic": "Arc Welding and Cutting"},
        {"standard": "1926.352", "subpart": "Subpart J", "topic": "Fire Prevention"},
        {"standard": "1926.353", "subpart": "Subpart J", "topic": "Ventilation and Protection"},
        {
            "standard": "1926.354",
            "subpart": "Subpart J",
            "topic": "Welding/Cutting - Preservative Coatings",
        },
    ],
    "ladders": [
        {"standard": "1926.1050", "subpart": "Subpart X", "topic": "Stairways and Ladders - Scope"},
        {
            "standard": "1926.1051",
            "subpart": "Subpart X",
            "topic": "Stairways - General Requirements",
        },
        {"standard": "1926.1052", "subpart": "Subpart X", "topic": "Stairways"},
        {"standard": "1926.1053", "subpart": "Subpart X", "topic": "Ladders"},
        {"standard": "1926.1060", "subpart": "Subpart X", "topic": "Stairways/Ladders - Training"},
    ],
    "fire_protection": [
        {
            "standard": "1926.150",
            "subpart": "Subpart F",
            "topic": "Fire Protection - General Requirements",
        },
        {"standard": "1926.151", "subpart": "Subpart F", "topic": "Fire Prevention"},
        {"standard": "1926.152", "subpart": "Subpart F", "topic": "Flammable Liquids"},
        {"standard": "1926.153", "subpart": "Subpart F", "topic": "Liquefied Petroleum Gas"},
        {"standard": "1926.154", "subpart": "Subpart F", "topic": "Temporary Heating Devices"},
        {"standard": "1926.155", "subpart": "Subpart F", "topic": "Fire Protection - Definitions"},
    ],
    "ppe": [
        {"standard": "1926.95", "subpart": "Subpart E", "topic": "PPE - Criteria"},
        {"standard": "1926.100", "subpart": "Subpart E", "topic": "Head Protection"},
        {"standard": "1926.101", "subpart": "Subpart E", "topic": "Hearing Protection"},
        {"standard": "1926.102", "subpart": "Subpart E", "topic": "Eye and Face Protection"},
        {"standard": "1926.103", "subpart": "Subpart E", "topic": "Respiratory Protection"},
        {"standard": "1926.106", "subpart": "Subpart E", "topic": "Working Over/Near Water"},
    ],
    "trenching": [
        {"standard": "1926.650", "subpart": "Subpart P", "topic": "Excavations - General"},
        {
            "standard": "1926.651",
            "subpart": "Subpart P",
            "topic": "Excavations - Specific Requirements",
        },
        {
            "standard": "1926.652",
            "subpart": "Subpart P",
            "topic": "Excavations - Protective Systems",
        },
    ],
    "roofing": [
        {"standard": "1926.500", "subpart": "Subpart M", "topic": "Fall Protection - Scope"},
        {"standard": "1926.501", "subpart": "Subpart M", "topic": "Fall Protection - Duty to Have"},
        {"standard": "1926.502", "subpart": "Subpart M", "topic": "Fall Protection - Systems"},
    ],
    "hazardous_materials": [
        {"standard": "1926.1100", "subpart": "Subpart Z", "topic": "Toxic Substances - General"},
        {"standard": "1926.1101", "subpart": "Subpart Z", "topic": "Asbestos"},
        {"standard": "1926.1126", "subpart": "Subpart Z", "topic": "Chromium (VI)"},
        {"standard": "1926.1127", "subpart": "Subpart Z", "topic": "Cadmium"},
        {"standard": "1926.1152", "subpart": "Subpart Z", "topic": "Methylene Chloride"},
        {"standard": "1926.1153", "subpart": "Subpart Z", "topic": "Respirable Crystalline Silica"},
    ],
    "underground_construction": [
        {"standard": "1926.800", "subpart": "Subpart S", "topic": "Underground Construction"},
        {"standard": "1926.803", "subpart": "Subpart S", "topic": "Compressed Air"},
    ],
    "blasting": [
        {"standard": "1926.900", "subpart": "Subpart U", "topic": "Blasting - General Provisions"},
        {
            "standard": "1926.901",
            "subpart": "Subpart U",
            "topic": "Blasting - Blaster Qualifications",
        },
        {
            "standard": "1926.902",
            "subpart": "Subpart U",
            "topic": "Blasting - Surface Transportation",
        },
        {
            "standard": "1926.903",
            "subpart": "Subpart U",
            "topic": "Blasting - Underground Transport",
        },
        {"standard": "1926.905", "subpart": "Subpart U", "topic": "Blasting - Loading"},
        {"standard": "1926.909", "subpart": "Subpart U", "topic": "Blasting - Firing"},
        {"standard": "1926.914", "subpart": "Subpart U", "topic": "Blasting - Definitions"},
    ],
    "material_handling": [
        {"standard": "1926.250", "subpart": "Subpart H", "topic": "Materials Handling - General"},
        {"standard": "1926.251", "subpart": "Subpart H", "topic": "Rigging Equipment"},
        {"standard": "1926.252", "subpart": "Subpart H", "topic": "Material Disposal"},
    ],
}

# Alias mapping for flexible input
_ACTIVITY_ALIASES: dict[str, str] = {
    "digging": "excavation",
    "trench": "trenching",
    "scaffold": "scaffolding",
    "scaffolds": "scaffolding",
    "electrical": "electrical_work",
    "electric": "electrical_work",
    "steel": "steel_erection",
    "ironwork": "steel_erection",
    "crane": "crane_operations",
    "cranes": "crane_operations",
    "derrick": "crane_operations",
    "hoist": "crane_operations",
    "concrete": "concrete_construction",
    "masonry": "concrete_construction",
    "formwork": "concrete_construction",
    "demo": "demolition",
    "confined_space": "confined_spaces",
    "weld": "welding",
    "cutting": "welding",
    "ladder": "ladders",
    "stairway": "ladders",
    "stairs": "ladders",
    "fire": "fire_protection",
    "fall": "fall_protection",
    "falls": "fall_protection",
    "guardrail": "fall_protection",
    "harness": "fall_protection",
    "roof": "roofing",
    "hazmat": "hazardous_materials",
    "asbestos": "hazardous_materials",
    "silica": "hazardous_materials",
    "lead": "hazardous_materials",
    "tunnel": "underground_construction",
    "blast": "blasting",
    "explosives": "blasting",
    "rigging": "material_handling",
    "hard_hat": "ppe",
    "safety_glasses": "ppe",
    "respirator": "ppe",
    "hearing": "ppe",
}


def get_applicable_osha_standards(activity_type: str) -> list[dict]:
    """Return relevant OSHA 1926 standards for a construction activity.

    Parameters
    ----------
    activity_type:
        Activity name like "excavation", "steel erection", "scaffolding",
        "electrical work", "crane operations", etc. Fuzzy matching via
        aliases is supported.

    Returns
    -------
    list[dict]
        Each dict: {standard, subpart, topic}. Returns empty list if no
        match found.

    Examples
    --------
    >>> get_applicable_osha_standards("excavation")
    [{"standard": "1926.650", "subpart": "Subpart P", ...}, ...]

    >>> get_applicable_osha_standards("crane")
    [{"standard": "1926.1400", "subpart": "Subpart CC", ...}, ...]
    """
    # Normalize input
    key = activity_type.lower().strip().replace(" ", "_").replace("-", "_")

    # Direct match
    if key in ACTIVITY_STANDARD_MAP:
        return ACTIVITY_STANDARD_MAP[key]

    # Alias match
    if key in _ACTIVITY_ALIASES:
        return ACTIVITY_STANDARD_MAP.get(_ACTIVITY_ALIASES[key], [])

    # Partial match — try substring
    for activity_key in ACTIVITY_STANDARD_MAP:
        if key in activity_key or activity_key in key:
            return ACTIVITY_STANDARD_MAP[activity_key]

    for alias, canonical in _ACTIVITY_ALIASES.items():
        if key in alias or alias in key:
            return ACTIVITY_STANDARD_MAP.get(canonical, [])

    return []


# ---------------------------------------------------------------------------
# Chunking OSHA sections for RAG storage
# ---------------------------------------------------------------------------


def chunk_osha_sections(
    sections: list[OshaSection],
    *,
    max_tokens: int = 512,
) -> list[dict]:
    """Chunk OSHA sections for embedding and storage.

    Each chunk includes structured metadata for retrieval.

    Returns
    -------
    list[dict]
        Each dict: {content, metadata}
    """
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    chunks: list[dict] = []

    for section in sections:
        text = f"OSHA {section.standard_number} - {section.title}\n\n{section.text}"
        tokens = enc.encode(text)

        metadata = {
            "document_type": "osha_standard",
            "standard_number": section.standard_number,
            "subpart": section.subpart,
            "title": section.title,
            "topic": _infer_topic(section),
            "applicability": _infer_applicability(section),
        }

        if len(tokens) <= max_tokens:
            chunks.append({"content": text, "metadata": metadata, "token_count": len(tokens)})
        else:
            # Split long sections
            start = 0
            part_num = 0
            while start < len(tokens):
                end = min(start + max_tokens, len(tokens))
                chunk_text = enc.decode(tokens[start:end])
                part_num += 1
                part_metadata = {**metadata, "part": part_num}
                chunks.append(
                    {
                        "content": chunk_text,
                        "metadata": part_metadata,
                        "token_count": end - start,
                    }
                )
                start = end - 50  # overlap
                if end >= len(tokens):
                    break

    logger.info("Created %d OSHA chunks from %d sections", len(chunks), len(sections))
    return chunks


def _infer_topic(section: OshaSection) -> str:
    """Infer a human-readable topic from an OSHA section."""
    if section.subpart:
        # Extract topic from subpart name: "Subpart M - Fall Protection" -> "Fall Protection"
        parts = section.subpart.split(" - ", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return section.title


def _infer_applicability(section: OshaSection) -> str:
    """Infer applicability description from section content."""
    text_lower = section.text.lower()

    applicability_parts = []

    if "all construction" in text_lower or "each employer" in text_lower:
        applicability_parts.append("All construction sites")

    height_indicators = ["6 feet", "6-foot", "six feet"]
    if any(h in text_lower for h in height_indicators):
        applicability_parts.append("fall hazards > 6 ft")

    if "excavation" in text_lower or "trench" in text_lower:
        applicability_parts.append("excavation/trenching work")

    if "scaffold" in text_lower:
        applicability_parts.append("scaffolding operations")

    if "crane" in text_lower or "derrick" in text_lower:
        applicability_parts.append("crane/derrick operations")

    if "electrical" in text_lower or "wiring" in text_lower:
        applicability_parts.append("electrical installations")

    if not applicability_parts:
        applicability_parts.append("Construction sites as specified")

    return "; ".join(applicability_parts)


# ---------------------------------------------------------------------------
# Database storage
# ---------------------------------------------------------------------------


async def store_osha_chunks(
    session,
    project_id: str,
    chunks: list[dict],
    embed_fn=None,
) -> dict:
    """Store OSHA chunks in the document_chunks and document_embeddings tables.

    Creates a synthetic Document record with type="osha_standard" to hold
    the chunks, then embeds and stores them.

    Parameters
    ----------
    session:
        SQLAlchemy AsyncSession.
    project_id:
        Project UUID to associate OSHA standards with (or a system-level project).
    chunks:
        Output from chunk_osha_sections().
    embed_fn:
        Optional async function to generate embeddings. If None, uses the
        default Voyage AI embedder.

    Returns
    -------
    dict with counts.
    """
    from sqlalchemy import text

    # Create a document record for the OSHA standards
    doc_id = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO documents (id, project_id, type, title, original_filename,
                s3_key, processing_status, data_source, metadata)
            VALUES (:id, :project_id, :type, :title, :filename,
                :s3_key, :status, :source, :metadata::jsonb)
            ON CONFLICT DO NOTHING
        """),
        {
            "id": doc_id,
            "project_id": project_id,
            "type": "osha_standard",
            "title": "OSHA 29 CFR 1926 - Construction Safety Standards",
            "filename": "cfr-title29-chapterXVII.xml",
            "s3_key": "osha/cfr-title29-chapterXVII.xml",
            "status": "complete",
            "source": "osha_ingestion",
            "metadata": '{"document_type": "osha_standard", "cfr_title": "29", "cfr_part": "1926"}',
        },
    )

    # Insert chunks
    chunk_ids: list[str] = []
    chunk_texts: list[str] = []

    for idx, chunk in enumerate(chunks):
        chunk_id = str(uuid.uuid4())
        chunk_ids.append(chunk_id)
        chunk_texts.append(chunk["content"])

        import json

        await session.execute(
            text("""
                INSERT INTO document_chunks
                    (id, document_id, chunk_index, content, chunk_type,
                     section_hierarchy, csi_section, token_count, metadata)
                VALUES
                    (:id, :doc_id, :idx, :content, :chunk_type,
                     :hierarchy::jsonb, :csi, :tokens, :meta::jsonb)
            """),
            {
                "id": chunk_id,
                "doc_id": doc_id,
                "idx": idx,
                "content": chunk["content"],
                "chunk_type": "osha_standard",
                "hierarchy": json.dumps(
                    [
                        chunk["metadata"].get("subpart", ""),
                        chunk["metadata"].get("standard_number", ""),
                    ]
                ),
                "csi": None,
                "tokens": chunk.get("token_count"),
                "meta": json.dumps(chunk["metadata"]),
            },
        )

    await session.flush()
    logger.info("Inserted %d OSHA chunks for document %s", len(chunk_ids), doc_id)

    # Embed chunks
    embedding_count = 0
    if embed_fn and chunk_texts:
        try:
            batch_size = 128
            for start in range(0, len(chunk_texts), batch_size):
                batch_texts = chunk_texts[start : start + batch_size]
                batch_ids = chunk_ids[start : start + batch_size]

                embeddings = await embed_fn(batch_texts)

                for cid, emb in zip(batch_ids, embeddings, strict=False):
                    vec_str = "[" + ", ".join(str(v) for v in emb) + "]"
                    await session.execute(
                        text("""
                            INSERT INTO document_embeddings (chunk_id, model_name, embedding)
                            VALUES (:chunk_id, :model, :embedding)
                        """),
                        {
                            "chunk_id": cid,
                            "model": "voyage-3-large",
                            "embedding": vec_str,
                        },
                    )
                embedding_count += len(batch_texts)

            await session.flush()
            logger.info("Stored %d OSHA embeddings", embedding_count)
        except Exception as exc:
            logger.warning("OSHA embedding failed (chunks still stored): %s", exc)

    await session.commit()

    return {
        "document_id": doc_id,
        "chunks_stored": len(chunk_ids),
        "embeddings_stored": embedding_count,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def run_ingestion(args: argparse.Namespace) -> None:
    """Main ingestion pipeline."""
    # Parse OSHA XML
    sections = parse_osha_xml(args.xml)
    if not sections:
        logger.error("No OSHA sections parsed. Check the XML file.")
        return

    _assign_subparts(sections)
    logger.info("Parsed %d OSHA 1926 sections", len(sections))

    # Log subpart distribution
    subpart_counts: dict[str, int] = {}
    for s in sections:
        sp = s.subpart or "Unknown"
        subpart_counts[sp] = subpart_counts.get(sp, 0) + 1
    for sp, count in sorted(subpart_counts.items()):
        logger.info("  %s: %d sections", sp, count)

    # Chunk
    chunks = chunk_osha_sections(sections, max_tokens=args.max_tokens)

    if args.dry_run:
        logger.info("[DRY RUN] Would store %d chunks", len(chunks))
        # Print sample
        for chunk in chunks[:3]:
            logger.info("  Sample: %s...", chunk["content"][:100])
            logger.info("  Metadata: %s", chunk["metadata"])
        return

    # Connect to database
    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("No database URL. Set DATABASE_URL or use --db-url / --dry-run")
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    # Set up embedding function
    embed_fn = None
    if not args.skip_embeddings:
        try:
            import voyageai

            voyage_client = voyageai.AsyncClient()

            async def _embed(texts: list[str]) -> list[list[float]]:
                response = await voyage_client.embed(
                    texts=texts, model="voyage-3-large", input_type="document"
                )
                return [[float(v) for v in emb] for emb in response.embeddings]

            embed_fn = _embed
        except Exception as exc:
            logger.warning("Voyage AI not available, skipping embeddings: %s", exc)

    # Store
    async with async_session() as session:
        result = await store_osha_chunks(
            session,
            project_id=args.project_id,
            chunks=chunks,
            embed_fn=embed_fn,
        )

    await engine.dispose()
    logger.info("Ingestion complete: %s", result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest OSHA 29 CFR 1926 construction standards into RAG knowledge base."
    )
    parser.add_argument(
        "--xml",
        type=Path,
        required=True,
        help="Path to OSHA XML file (cfr-title29-chapterXVII.xml)",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default="00000000-0000-0000-0000-000000000000",
        help="Project UUID to associate standards with (default: system project)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Max tokens per chunk (default: 512)",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Store chunks without generating embeddings",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk but do not write to database",
    )

    args = parser.parse_args()

    if not args.xml.exists():
        logger.error("OSHA XML file not found: %s", args.xml)
        sys.exit(1)

    asyncio.run(run_ingestion(args))


if __name__ == "__main__":
    main()
