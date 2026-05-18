"""Procore data sync service.

Pulls data from Procore and persists it to ConstructAI's database.
Each sync function:
  1. Fetches data from Procore via ProcoreAPI
  2. Maps to ConstructAI models via procore_mapper
  3. Upserts records (insert or update based on procore_id)
  4. Returns counts for the SyncLog

All Procore-sourced records are flagged with data_source='procore'.
CRITICAL: Data with data_source='procore' MUST be excluded from ML
training pipelines per Procore API terms of service.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import RFI
from app.models.document import Document
from app.models.evm import ChangeOrder
from app.models.procore_connection import ProcoreConnection
from app.models.productivity import DailyLog
from app.models.project import Project
from app.models.sync_log import SyncLog
from app.services.integrations.procore_api import ProcoreAPI
from app.services.integrations.procore_mapper import (
    map_procore_budget_to_evm,
    map_procore_change_order,
    map_procore_daily_log,
    map_procore_document,
    map_procore_project,
    map_procore_rfi,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-entity sync functions
# ---------------------------------------------------------------------------


async def sync_projects(
    api: ProcoreAPI,
    db: AsyncSession,
    org_id: uuid.UUID,
    company_id: int,
) -> dict[str, Any]:
    """Sync projects from Procore to ConstructAI.

    Uses upsert logic: if a project with the same procore_id already
    exists for this org, update it; otherwise create a new one.
    """
    procore_projects = await api.list_projects_v1_1(company_id)
    synced = 0
    errors: list[dict] = []

    for pp in procore_projects:
        try:
            mapped = map_procore_project(pp, org_id)

            result = await db.execute(
                select(Project).where(
                    Project.org_id == org_id,
                    Project.procore_id == pp.id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                for key, value in mapped.items():
                    if key not in ("org_id",):
                        setattr(existing, key, value)
                existing.updated_at = datetime.now(UTC)
            else:
                project = Project(**mapped)
                db.add(project)

            synced += 1
        except Exception as exc:
            logger.error("Failed to sync project %d: %s", pp.id, exc)
            errors.append({"entity": "project", "procore_id": pp.id, "error": str(exc)})

    await db.flush()
    return {"synced": synced, "errors": errors}


async def sync_rfis(
    api: ProcoreAPI,
    db: AsyncSession,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
) -> dict[str, Any]:
    """Sync RFIs for a single project."""
    procore_rfis = await api.list_rfis(procore_project_id, company_id)
    synced = 0
    errors: list[dict] = []

    for pr in procore_rfis:
        try:
            mapped = map_procore_rfi(pr, project_id)

            result = await db.execute(
                select(RFI).where(
                    RFI.project_id == project_id,
                    RFI.procore_id == pr.id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                for key, value in mapped.items():
                    if key not in ("project_id",):
                        setattr(existing, key, value)
                existing.updated_at = datetime.now(UTC)
            else:
                rfi = RFI(**mapped)
                db.add(rfi)

            synced += 1
        except Exception as exc:
            logger.error("Failed to sync RFI %d: %s", pr.id, exc)
            errors.append({"entity": "rfi", "procore_id": pr.id, "error": str(exc)})

    await db.flush()
    return {"synced": synced, "errors": errors}


async def sync_documents(
    api: ProcoreAPI,
    db: AsyncSession,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
    kafka_producer: Any = None,
) -> dict[str, Any]:
    """Sync documents for a project.

    Downloads files from Procore and uploads to MinIO.
    Publishes 'constructai.document.ingested' Kafka events for each new doc.
    """
    from app.utils.s3 import upload_file

    procore_docs = await api.list_documents(procore_project_id, company_id)
    synced = 0
    errors: list[dict] = []

    for pd in procore_docs:
        try:
            # Check for existing document
            result = await db.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.procore_id == pd.id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Already synced — update metadata only, skip re-download
                mapped = map_procore_document(pd, project_id)
                for key in ("title", "type", "file_size_bytes", "metadata_"):
                    setattr(existing, key, mapped[key])
                existing.updated_at = datetime.now(UTC)
                synced += 1
                continue

            # Download file from Procore
            try:
                file_bytes, content_type = await api.download_document(
                    procore_project_id,
                    pd.id,
                    company_id,
                )
            except Exception as dl_exc:
                logger.warning("Could not download document %d: %s", pd.id, dl_exc)
                errors.append(
                    {
                        "entity": "document",
                        "procore_id": pd.id,
                        "error": f"download failed: {dl_exc}",
                    }
                )
                continue

            # Upload to MinIO (sync boto3 op — run in executor)
            # SECURITY: Sanitize filename to prevent path traversal attacks.
            # Strip ../, ..\, and path separators from the filename component.
            raw_filename = pd.filename or pd.name
            import os as _os

            safe_filename = (
                _os.path.basename(raw_filename).replace("..", "").replace("\\", "").replace("/", "")
            )
            if not safe_filename:
                safe_filename = f"document_{pd.id}"
            s3_key = f"procore/{project_id}/{pd.id}/{safe_filename}"
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                partial(upload_file, s3_key, file_bytes, content_type),
            )

            # Create Document record
            mapped = map_procore_document(pd, project_id)
            mapped["s3_key"] = s3_key
            mapped["file_size_bytes"] = len(file_bytes)
            mapped["content_hash"] = hashlib.sha256(file_bytes).hexdigest()

            doc = Document(**mapped)
            db.add(doc)
            await db.flush()
            await db.refresh(doc)

            # Publish Kafka event for RAG pipeline
            if kafka_producer and kafka_producer.available:
                await kafka_producer.publish(
                    event_type="constructai.document.ingested",
                    data={
                        "document_id": str(doc.id),
                        "project_id": str(project_id),
                        "s3_key": s3_key,
                        "title": doc.title,
                        "data_source": "procore",
                    },
                    source="/procore-sync",
                )

            synced += 1
        except Exception as exc:
            logger.error("Failed to sync document %d: %s", pd.id, exc)
            errors.append({"entity": "document", "procore_id": pd.id, "error": str(exc)})

    return {"synced": synced, "errors": errors}


async def sync_change_orders(
    api: ProcoreAPI,
    db: AsyncSession,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
) -> dict[str, Any]:
    """Sync change order packages for a project."""
    procore_cos = await api.list_change_orders(procore_project_id, company_id)
    synced = 0
    errors: list[dict] = []

    for pco in procore_cos:
        try:
            mapped = map_procore_change_order(pco, project_id)

            result = await db.execute(
                select(ChangeOrder).where(
                    ChangeOrder.project_id == project_id,
                    ChangeOrder.procore_id == pco.id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                for key, value in mapped.items():
                    if key not in ("project_id",):
                        setattr(existing, key, value)
                existing.updated_at = datetime.now(UTC)
            else:
                co = ChangeOrder(**mapped)
                db.add(co)

            synced += 1
        except Exception as exc:
            logger.error("Failed to sync change order %d: %s", pco.id, exc)
            errors.append({"entity": "change_order", "procore_id": pco.id, "error": str(exc)})

    await db.flush()
    return {"synced": synced, "errors": errors}


async def sync_budget(
    api: ProcoreAPI,
    db: AsyncSession,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
) -> dict[str, Any]:
    """Sync budget line items for a project.

    Maps original_budget -> planned_value for EVM calculations.
    Stores budget details in the project's metadata_.
    """
    budget_items = await api.get_budget(procore_project_id, company_id)
    evm_data = map_procore_budget_to_evm(budget_items)

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project:
        meta = dict(project.metadata_ or {})
        meta["procore_budget"] = {
            "planned_value": str(evm_data["planned_value"]),
            "original_budget": str(evm_data["original_budget"]),
            "line_item_count": len(budget_items),
            "synced_at": datetime.now(UTC).isoformat(),
            "line_items": [
                {
                    "procore_id": li.id,
                    "cost_code": li.cost_code,
                    "description": li.description,
                    "original_budget": str(li.original_budget_amount or 0),
                    "approved_cos": str(li.approved_change_orders or 0),
                    "revised_budget": str(li.revised_budget or 0),
                }
                for li in budget_items
            ],
        }
        project.metadata_ = meta
        project.contract_value = evm_data["planned_value"]
        await db.flush()

    return {"synced": len(budget_items), "errors": []}


async def sync_daily_logs(
    api: ProcoreAPI,
    db: AsyncSession,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
) -> dict[str, Any]:
    """Sync daily log entries for a project."""
    procore_logs = await api.list_daily_logs(procore_project_id, company_id)
    synced = 0
    errors: list[dict] = []

    for pdl in procore_logs:
        try:
            mapped = map_procore_daily_log(pdl, project_id)

            result = await db.execute(
                select(DailyLog).where(
                    DailyLog.project_id == project_id,
                    DailyLog.procore_id == pdl.id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                for key, value in mapped.items():
                    if key not in ("project_id",):
                        setattr(existing, key, value)
                existing.updated_at = datetime.now(UTC)
            else:
                daily_log = DailyLog(**mapped)
                db.add(daily_log)

            synced += 1
        except Exception as exc:
            logger.error("Failed to sync daily log %d: %s", pdl.id, exc)
            errors.append({"entity": "daily_log", "procore_id": pdl.id, "error": str(exc)})

    await db.flush()
    return {"synced": synced, "errors": errors}


# ---------------------------------------------------------------------------
# Per-project orchestrator
# ---------------------------------------------------------------------------


async def sync_project_data(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    procore_project_id: int,
    company_id: int,
    kafka_producer: Any = None,
) -> dict[str, Any]:
    """Sync all entity types for a single project.

    Runs entity syncs sequentially (AsyncSession is not concurrent-safe).
    Returns a summary dict with counts per entity type.
    """
    api = ProcoreAPI(org_id=org_id, db=db)
    results: dict[str, int] = {}
    all_errors: list[dict] = []

    entity_syncs = [
        ("rfis", sync_rfis(api, db, project_id, procore_project_id, company_id)),
        (
            "documents",
            sync_documents(
                api,
                db,
                project_id,
                procore_project_id,
                company_id,
                kafka_producer,
            ),
        ),
        (
            "change_orders",
            sync_change_orders(
                api,
                db,
                project_id,
                procore_project_id,
                company_id,
            ),
        ),
        ("budget", sync_budget(api, db, project_id, procore_project_id, company_id)),
        (
            "daily_logs",
            sync_daily_logs(
                api,
                db,
                project_id,
                procore_project_id,
                company_id,
            ),
        ),
    ]

    for entity_name, coro in entity_syncs:
        try:
            result = await coro
            results[entity_name] = result["synced"]
            all_errors.extend(result.get("errors", []))
        except Exception as exc:
            logger.error(
                "Failed to sync %s for project %s: %s",
                entity_name,
                project_id,
                exc,
            )
            results[entity_name] = 0
            all_errors.append(
                {
                    "entity": entity_name,
                    "project_id": str(project_id),
                    "error": str(exc),
                }
            )

    return {"entities_synced": results, "errors": all_errors}


# ---------------------------------------------------------------------------
# Full sync orchestrator
# ---------------------------------------------------------------------------


async def sync_all(
    db: AsyncSession,
    org_id: uuid.UUID,
    triggered_by: uuid.UUID | None = None,
    kafka_producer: Any = None,
) -> SyncLog:
    """Full sync: all projects and their entities for an organization.

    Creates a SyncLog entry, syncs projects first, then iterates over
    each synced project to sync its child entities.

    Handles partial failures gracefully — individual project failures
    do not abort the full sync.
    """
    sync_log = SyncLog(
        org_id=org_id,
        sync_type="full",
        status="running",
        triggered_by=triggered_by,
    )
    db.add(sync_log)
    await db.flush()
    await db.refresh(sync_log)

    try:
        # Get Procore connection
        conn_result = await db.execute(
            select(ProcoreConnection).where(ProcoreConnection.organization_id == org_id)
        )
        conn = conn_result.scalar_one_or_none()
        if conn is None or not conn.procore_company_id:
            sync_log.status = "failed"
            sync_log.completed_at = datetime.now(UTC)
            sync_log.errors = [{"error": "No Procore connection found"}]
            await db.flush()
            return sync_log

        company_id = int(conn.procore_company_id)
        api = ProcoreAPI(org_id=org_id, db=db)

        # Step 1: Sync projects
        project_result = await sync_projects(api, db, org_id, company_id)
        total_entities: dict[str, int] = {"projects": project_result["synced"]}
        all_errors: list[dict] = list(project_result.get("errors", []))

        # Step 2: Get all procore-synced projects for this org
        projects_result = await db.execute(
            select(Project).where(
                Project.org_id == org_id,
                Project.data_source == "procore",
                Project.procore_id.isnot(None),
            )
        )
        projects = projects_result.scalars().all()

        # Step 3: Sync entities for each project
        for project in projects:
            try:
                if project.procore_id is None:
                    logger.warning("Skipping project %s: no procore_id", project.id)
                    continue
                result = await sync_project_data(
                    db=db,
                    org_id=org_id,
                    project_id=project.id,
                    procore_project_id=project.procore_id,
                    company_id=company_id,
                    kafka_producer=kafka_producer,
                )
                for entity, count in result["entities_synced"].items():
                    total_entities[entity] = total_entities.get(entity, 0) + count
                all_errors.extend(result.get("errors", []))
            except Exception as exc:
                logger.error("Failed to sync project %s: %s", project.id, exc)
                all_errors.append(
                    {
                        "entity": "project_sync",
                        "project_id": str(project.id),
                        "error": str(exc),
                    }
                )

        # Update sync log
        sync_log.status = "completed" if not all_errors else "partial"
        sync_log.completed_at = datetime.now(UTC)
        sync_log.entities_synced = total_entities
        sync_log.errors = all_errors[:100]  # Cap error list size

        # Update connection's last_sync_at
        conn.last_sync_at = datetime.now(UTC)
        # SECURITY: Only mark as "synced" for fully completed syncs;
        # partial syncs (with errors) get "partial_sync" to accurately
        # reflect sync state.
        conn.sync_status = "synced" if sync_log.status == "completed" else "partial_sync"

        await db.flush()
        return sync_log

    except Exception as exc:
        logger.error("Full sync failed for org %s: %s", org_id, exc)
        sync_log.status = "failed"
        sync_log.completed_at = datetime.now(UTC)
        sync_log.errors = [{"error": str(exc)}]
        await db.flush()
        return sync_log
