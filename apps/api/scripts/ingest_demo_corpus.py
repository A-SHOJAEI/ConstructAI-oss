"""Ingest the public demo corpus into the RAG knowledge base.

Walks data/manifests/manifest.jsonl and ingests each file into the
appropriate demo project. Documents are tagged data_source='public_demo'
so they never collide with Procore-sourced or customer-internal data.

Distributes corpus across the 6 demo tenants so each has a project-relevant
slice (UFGS architectural specs to multifamily, civil specs to highway, etc.).

Run:
    cd apps/api && .venv/bin/python scripts/ingest_demo_corpus.py
    cd apps/api && .venv/bin/python scripts/ingest_demo_corpus.py --tenant demo_session_01
    cd apps/api && .venv/bin/python scripts/ingest_demo_corpus.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import fitz
from app.models.document import Document, DocumentChunk
from app.models.organization import Organization
from app.models.project import Project
from sqlalchemy import select

from app.database import async_session
from app.services.ingestion.chunking import chunk_document_smart
from app.services.ingestion.embedder import embed_chunks, store_chunk_embeddings
from app.services.ingestion.pdf_parser import (
    MAX_PDF_FILE_SIZE,
    MAX_PDF_PAGE_COUNT,
    ParsedPage,
)

DATA_ROOT = Path(__file__).resolve().parents[3] / "data"
MANIFEST_PATH = DATA_ROOT / "manifests" / "manifest.jsonl"
NS = uuid.UUID("00000000-0000-0000-0000-000000000003")

# Tenant-affinity routing — which categories go to which slugs.
# Specs and reference docs are duplicated across all 6 tenants by default
# so any logged-in PM can search "their" project for them.
TENANT_AFFINITY = {
    "demo_session_01": ["ufgs", "gsa", "rfi-samples", "cost", "osha-pubs", "osha-xml"],
    "demo_session_02": ["ufgs", "gsa", "rfi-samples", "cost", "osha-pubs", "osha-xml"],
    "demo_session_03": ["ufgs", "specs-state", "cost", "osha-pubs", "osha-xml"],
    "demo_session_04": ["ufgs", "specs-state", "cost", "osha-pubs", "osha-xml"],
    "demo_session_05": ["ufgs", "gsa", "rfi-samples", "cost", "osha-pubs", "osha-xml"],
    "demo_session_06": ["ufgs", "gsa", "rfi-samples", "cost", "osha-pubs", "osha-xml"],
}

# Map manifest categories to Document.type tags. Must match the
# ck_document_type CHECK constraint: specification, drawing, contract, rfi,
# submittal, daily_log, meeting_minutes, photo, change_order, schedule,
# bim_model, other.
DOC_TYPE_MAP = {
    "ufgs": "specification",
    "specs-state": "specification",
    "gsa": "specification",
    "osha-pubs": "specification",
    "osha-xml": "specification",
    "rfi-samples": "rfi",
    "cost": "other",
    "bim": "bim_model",
    "videos": "photo",
    "vision": "other",
}


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        print(f"No manifest at {MANIFEST_PATH}; run fetch_demo_corpus.py first")
        return []
    out: list[dict] = []
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ---------------------------------------------------------------------------
# PDF splitting (handle files > 500-page limit)
# ---------------------------------------------------------------------------


def _parse_pdf_streaming(file_path: Path) -> list[list[ParsedPage]]:
    """Parse a PDF into one or more page batches each <= MAX_PDF_PAGE_COUNT.

    Uses fitz directly (PyMuPDF) to bypass parse_pdf's enforced limit
    when ingesting deliberately-large reference specs (Caltrans 700+ pages).

    Returns a list of page-batches; one batch per ingested Document.
    """
    file_size = file_path.stat().st_size
    if file_size > MAX_PDF_FILE_SIZE:
        print(f"  WARN {file_path.name} is {file_size} bytes; trimming via fitz")

    doc = fitz.open(str(file_path))
    total_pages = len(doc)

    batch_size = MAX_PDF_PAGE_COUNT - 50  # leave headroom
    batches: list[list[ParsedPage]] = []
    for start in range(0, total_pages, batch_size):
        end = min(start + batch_size, total_pages)
        pages: list[ParsedPage] = []
        for i in range(start, end):
            page = doc[i]
            text = page.get_text("text") or ""
            pages.append(
                ParsedPage(
                    page_number=i + 1,
                    text=text,
                    tables=[],
                    headings=[],
                )
            )
        batches.append(pages)
    doc.close()
    return batches


# ---------------------------------------------------------------------------
# Plain-text / XML ingestion (OSHA XML, JSON cost data)
# ---------------------------------------------------------------------------


def _read_text_as_pages(file_path: Path, max_chars_per_page: int = 4000) -> list[ParsedPage]:
    """Read a non-PDF text file and split into synthetic pages."""
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    if file_path.suffix == ".xml":
        # Strip XML tags but keep tag names as soft markers
        import re

        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()

    pages: list[ParsedPage] = []
    for i in range(0, len(raw), max_chars_per_page):
        chunk_text = raw[i : i + max_chars_per_page]
        pages.append(
            ParsedPage(
                page_number=i // max_chars_per_page + 1,
                text=chunk_text,
                tables=[],
                headings=[],
            )
        )
    return pages


def _read_json_as_pages(file_path: Path) -> list[ParsedPage]:
    """Format JSON (BLS PPI series) as readable text."""
    try:
        data = json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return []
    series_id = file_path.stem.replace("bls-", "")
    body = json.dumps(data, indent=2)
    text = (
        f"BLS Producer Price Index time series — {series_id}\n\n"
        f"This dataset contains monthly producer price index values published by the "
        f"US Bureau of Labor Statistics. Use these values for cost-escalation forecasting "
        f"and bid analysis.\n\n{body[:30000]}"
    )
    return [ParsedPage(page_number=1, text=text, tables=[], headings=[])]


# ---------------------------------------------------------------------------
# Demo project resolution
# ---------------------------------------------------------------------------


async def _list_demo_projects() -> list[tuple[Organization, Project]]:
    async with async_session() as db:
        result = await db.execute(
            select(Organization).where(Organization.slug.like("demo_session_%"))
        )
        orgs = list(result.scalars().all())
        out: list[tuple[Organization, Project]] = []
        for org in orgs:
            r = await db.execute(select(Project).where(Project.org_id == org.id).limit(1))
            project = r.scalar_one_or_none()
            if project is not None:
                out.append((org, project))
        return out


# ---------------------------------------------------------------------------
# Document upsert + chunking + embedding
# ---------------------------------------------------------------------------


def _stable_doc_id(slug: str, rel_path: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{slug}::{rel_path}")


async def _upsert_document(
    db,
    *,
    doc_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    rel_path: str,
    doc_type: str,
    file_size_bytes: int,
    content_hash: str,
    metadata: dict,
) -> Document:
    """Idempotent: returns the existing record if present, else inserts."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    doc = Document(
        id=doc_id,
        project_id=project_id,
        type=doc_type,
        title=title,
        original_filename=Path(rel_path).name,
        s3_key=f"public_demo/{rel_path}",
        file_size_bytes=file_size_bytes,
        content_hash=content_hash,
        processing_status="pending",
        data_source="public_demo",
        metadata_=metadata,
    )
    db.add(doc)
    await db.flush()
    return doc


async def _ingest_one(
    *,
    org: Organization,
    project: Project,
    entry: dict,
    file_path: Path,
    dry_run: bool,
) -> dict:
    """Returns a stats dict {success, chunks, embeddings, error}."""
    rel = entry["path"]
    category = entry.get("category", "")
    title = entry.get("name") or Path(rel).stem
    doc_type = DOC_TYPE_MAP.get(category, "reference")

    # Parse based on file type
    suffix = file_path.suffix.lower()
    page_batches: list[list[ParsedPage]]
    if suffix == ".pdf":
        try:
            page_batches = _parse_pdf_streaming(file_path)
        except Exception as exc:
            return {"success": False, "chunks": 0, "embeddings": 0, "error": f"pdf parse: {exc}"}
    elif suffix == ".xml":
        page_batches = [_read_text_as_pages(file_path)]
    elif suffix == ".json":
        page_batches = [_read_json_as_pages(file_path)]
    else:
        return {"success": False, "chunks": 0, "embeddings": 0, "error": f"unsupported {suffix}"}

    total_chunks = 0
    total_embeddings = 0
    file_size = file_path.stat().st_size
    content_hash = entry.get("sha256") or _hash_file(file_path)

    for batch_idx, pages in enumerate(page_batches):
        if not pages:
            continue
        # When a single file maps to multiple Documents (>500 pages), give
        # each batch its own stable id and title-suffix.
        suffix_id = f"::part-{batch_idx + 1}" if len(page_batches) > 1 else ""
        doc_id = _stable_doc_id(org.slug, rel + suffix_id)
        batch_title = (
            f"{title} (part {batch_idx + 1}/{len(page_batches)})"
            if len(page_batches) > 1
            else title
        )

        if dry_run:
            chunks = chunk_document_smart(pages)
            print(
                f"  [dry] {org.slug} <- {batch_title}: {len(pages)} pages -> {len(chunks)} chunks"
            )
            total_chunks += len(chunks)
            continue

        async with async_session() as db:
            try:
                doc = await _upsert_document(
                    db,
                    doc_id=doc_id,
                    project_id=project.id,
                    title=batch_title,
                    rel_path=rel + suffix_id,
                    doc_type=doc_type,
                    file_size_bytes=file_size,
                    content_hash=content_hash,
                    metadata={
                        "source_url": entry.get("source_url"),
                        "license": entry.get("license"),
                        "category": category,
                        "data_source": "public_demo",
                    },
                )

                # Skip if already chunked
                existing_chunks = await db.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc.id).limit(1)
                )
                if existing_chunks.scalar_one_or_none() is not None:
                    await db.commit()
                    continue

                chunks = chunk_document_smart(pages)
                if not chunks:
                    doc.processing_status = "complete"
                    doc.metadata_ = {**(doc.metadata_ or {}), "chunks": 0}
                    await db.commit()
                    continue

                db_chunks: list[DocumentChunk] = []
                for idx, ch in enumerate(chunks):
                    db_ch = DocumentChunk(
                        document_id=doc.id,
                        chunk_index=idx,
                        content=ch.content,
                        chunk_type=ch.chunk_type,
                        page_number=ch.page_number,
                        section_hierarchy=ch.section_hierarchy,
                        csi_section=ch.csi_section,
                        token_count=ch.token_count,
                    )
                    db.add(db_ch)
                    db_chunks.append(db_ch)
                await db.flush()

                vectors = await embed_chunks([c.content for c in db_chunks])
                emb_count = await store_chunk_embeddings(db, db_chunks, vectors)

                doc.processing_status = "complete"
                doc.page_count = len(pages)
                await db.commit()

                total_chunks += len(chunks)
                total_embeddings += emb_count
            except Exception as exc:
                await db.rollback()
                return {
                    "success": False,
                    "chunks": total_chunks,
                    "embeddings": total_embeddings,
                    "error": f"ingest: {type(exc).__name__}: {exc}",
                }

    return {
        "success": True,
        "chunks": total_chunks,
        "embeddings": total_embeddings,
        "error": None,
    }


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def ingest_all(*, only_tenant: str | None, only_category: str | None, dry_run: bool) -> None:
    pairs = await _list_demo_projects()
    if not pairs:
        print("No demo tenants. Run seed_demo_tenants.py first.")
        return

    if only_tenant:
        pairs = [(o, p) for o, p in pairs if o.slug == only_tenant]
        if not pairs:
            print(f"Tenant {only_tenant!r} not found.")
            return

    manifest = _load_manifest()
    if not manifest:
        return
    print(f"Found {len(manifest)} manifest entries; ingesting into {len(pairs)} tenants.\n")

    overall = {"docs": 0, "chunks": 0, "embeddings": 0, "skipped": 0, "failed": 0}
    fail_log: list[str] = []

    for org, project in pairs:
        affinity = TENANT_AFFINITY.get(org.slug, [])
        print(f"=== {org.slug} (affinity: {','.join(affinity) or 'all'}) ===")
        for entry in manifest:
            cat = entry.get("category", "")
            if only_category and cat != only_category:
                continue
            if affinity and cat not in affinity:
                overall["skipped"] += 1
                continue
            rel = entry["path"]
            file_path = DATA_ROOT / rel
            if not file_path.exists():
                continue
            if file_path.is_dir():
                continue
            if file_path.suffix.lower() not in {".pdf", ".xml", ".json"}:
                continue

            print(f"  -> {Path(rel).name} ({cat})")
            result = await _ingest_one(
                org=org,
                project=project,
                entry=entry,
                file_path=file_path,
                dry_run=dry_run,
            )
            if result["success"]:
                overall["docs"] += 1
                overall["chunks"] += result["chunks"]
                overall["embeddings"] += result["embeddings"]
                if result["chunks"]:
                    print(f"    chunks={result['chunks']} embeddings={result['embeddings']}")
            else:
                overall["failed"] += 1
                msg = f"{org.slug} | {Path(rel).name}: {result['error']}"
                fail_log.append(msg)
                print(f"    FAIL: {result['error']}")

    print("\n=== SUMMARY ===")
    print(f"  documents ingested: {overall['docs']}")
    print(f"  chunks:             {overall['chunks']}")
    print(f"  embeddings:         {overall['embeddings']}")
    print(f"  skipped (affinity): {overall['skipped']}")
    print(f"  failed:             {overall['failed']}")
    if fail_log:
        print("\nFailures:")
        for m in fail_log[:30]:
            print(f"  - {m}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="Restrict to single tenant slug")
    parser.add_argument("--category", help="Restrict to single category")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(
        ingest_all(
            only_tenant=args.tenant,
            only_category=args.category,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
