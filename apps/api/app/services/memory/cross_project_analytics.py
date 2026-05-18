"""Cross-project analytics for organization-wide construction intelligence.

Provides real analytics across all projects within an organization,
including cost pattern detection, schedule accuracy analysis, RFI clustering,
cost trend tracking, and risk factor correlation.

SECURITY (C-10): All queries are scoped by org_id for tenant isolation.
Parameters are anonymized before caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import RFI
from app.models.cross_project import CrossProjectInsight
from app.models.estimating import CostEstimate, EstimateLineItem
from app.models.evm import EVMSnapshot
from app.models.field_management import RiskRegisterEntry
from app.models.pay_application import PayApplication, PayApplicationLineItem, ScheduleOfValues
from app.models.project import Project
from app.models.scheduling import ScheduleBaseline
from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# SECURITY (C-10): Anonymize parameters before caching
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_PROJECT_ID_KEYS = {"project_id", "project_name", "project_number", "client_name", "client_id"}

# Default cache TTL for insights (24 hours)
_DEFAULT_INSIGHT_TTL_HOURS = 24

# Maximum number of projects to include in a single analytics query
_MAX_PROJECTS_PER_QUERY = 500


def _anonymize_parameters(params: dict) -> dict:
    """Strip project-specific identifiers from parameters for caching."""
    if not params:
        return {}
    cleaned: dict = {}
    for key, value in params.items():
        if key.lower() in _PROJECT_ID_KEYS:
            continue
        if isinstance(value, str) and _UUID_RE.fullmatch(value):
            continue
        cleaned[key] = value
    return cleaned


def _compute_query_hash(insight_type: str, params: dict) -> str:
    """Compute a deterministic hash for cache lookup."""
    safe = _anonymize_parameters(params)
    payload = json.dumps({"type": insight_type, "params": safe}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CostPattern:
    """A detected cost pattern across projects."""

    csi_division: str
    description: str
    average_variance_pct: float
    project_count: int
    project_type: str | None = None
    confidence: float = 0.50
    low_confidence: bool = False  # SV-33: set when insufficient data


@dataclass
class ScheduleAccuracyReport:
    """Schedule accuracy analysis across projects."""

    total_projects: int
    average_duration_variance_pct: float
    on_time_rate: float
    by_project_type: dict = field(default_factory=dict)
    by_project_size: dict = field(default_factory=dict)
    common_delay_causes: list[dict] = field(default_factory=list)
    low_confidence: bool = False  # SV-33


@dataclass
class RFIPattern:
    """A common RFI pattern across projects."""

    subject_cluster: str
    occurrence_count: int
    average_resolution_days: float
    most_common_keywords: list[str] = field(default_factory=list)
    building_type: str | None = None
    low_confidence: bool = False  # SV-33


@dataclass
class CostTrendInsight:
    """Cost trend for a CSI division over time."""

    csi_division: str
    description: str
    trend_direction: str  # "increasing", "decreasing", "stable"
    average_annual_change_pct: float
    data_points: list[dict] = field(default_factory=list)
    project_count: int = 0


@dataclass
class RiskCorrelation:
    """Correlation between risk type and project outcomes."""

    risk_category: str
    occurrence_count: int
    avg_cost_impact_pct: float
    avg_schedule_impact_days: float
    projects_affected: int
    correlation_strength: str  # "weak", "moderate", "strong"


@dataclass
class CrossProjectAnswer:
    """Answer to a natural language cross-project question."""

    question: str
    answer: str
    confidence: float
    source_project_count: int
    supporting_data: dict = field(default_factory=dict)
    cached: bool = False


# ---------------------------------------------------------------------------
# Helper: get org projects
# ---------------------------------------------------------------------------


async def _get_org_project_ids(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_type: str | None = None,
) -> list[uuid.UUID]:
    """Get all project IDs for an org, optionally filtered by type."""
    stmt = select(Project.id).where(Project.org_id == org_id)
    if project_type:
        stmt = stmt.where(Project.type == project_type)
    stmt = stmt.limit(_MAX_PROJECTS_PER_QUERY)
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


async def _get_org_projects(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[Project]:
    """Get all projects for an org."""
    stmt = select(Project).where(Project.org_id == org_id).limit(_MAX_PROJECTS_PER_QUERY)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Cost pattern detection
# ---------------------------------------------------------------------------


async def detect_cost_patterns(
    db: AsyncSession,
    org_id: uuid.UUID,
    filters: dict | None = None,
    min_project_count: int = 3,
) -> list[CostPattern]:
    """Detect cost patterns by comparing estimates to actuals across projects.

    Joins estimate line items against pay application actuals by CSI division
    across all org projects. Groups by project type and identifies systematic
    over/under-estimation patterns.

    Args:
        db: Database session.
        org_id: Organization ID for tenant scoping.
        filters: Optional filters (project_type, csi_division, min_projects).

    Returns:
        List of CostPattern sorted by absolute variance.
    """
    filters = filters or {}
    project_type_filter = filters.get("project_type")
    csi_filter = filters.get("csi_division")
    min_projects = filters.get("min_projects", 2)

    project_ids = await _get_org_project_ids(db, org_id, project_type_filter)
    if not project_ids:
        return []

    # SV-33: Check for statistical significance
    _low_confidence_cost = len(project_ids) < min_project_count

    # Get all projects for type lookup
    projects = await _get_org_projects(db, org_id)
    project_type_map = {p.id: p.type for p in projects}

    # Query estimate line items grouped by CSI division and project
    estimate_stmt = (
        select(
            EstimateLineItem.csi_code,
            CostEstimate.project_id,
            func.sum(EstimateLineItem.quantity * EstimateLineItem.unit_cost).label(
                "estimated_total"
            ),
        )
        .join(CostEstimate, EstimateLineItem.estimate_id == CostEstimate.id)
        .where(
            CostEstimate.project_id.in_(project_ids),
            EstimateLineItem.csi_code.isnot(None),
        )
        .group_by(EstimateLineItem.csi_code, CostEstimate.project_id)
    )
    if csi_filter:
        estimate_stmt = estimate_stmt.where(EstimateLineItem.csi_code.startswith(csi_filter))

    estimate_result = await db.execute(estimate_stmt)
    estimate_rows = estimate_result.all()

    # Query SOV/pay app actuals grouped by CSI code and project
    actual_stmt = (
        select(
            ScheduleOfValues.csi_code,
            ScheduleOfValues.project_id,
            func.sum(ScheduleOfValues.scheduled_value).label("actual_total"),
        )
        .where(
            ScheduleOfValues.project_id.in_(project_ids),
            ScheduleOfValues.csi_code.isnot(None),
        )
        .group_by(ScheduleOfValues.csi_code, ScheduleOfValues.project_id)
    )
    if csi_filter:
        actual_stmt = actual_stmt.where(ScheduleOfValues.csi_code.startswith(csi_filter))

    actual_result = await db.execute(actual_stmt)
    actual_rows = actual_result.all()

    # Build lookup: (csi_code, project_id) -> estimated
    estimates: dict[tuple[str, uuid.UUID], float] = {}
    for row in estimate_rows:
        csi = _normalize_csi(row.csi_code)
        if csi:
            estimates[(csi, row.project_id)] = float(row.estimated_total or 0)

    # Build lookup: (csi_code, project_id) -> actual (from SOV)
    actuals: dict[tuple[str, uuid.UUID], float] = {}
    for row in actual_rows:
        csi = _normalize_csi(row.csi_code)
        if csi:
            actuals[(csi, row.project_id)] = float(row.actual_total or 0)

    # SV-32: Secondary comparison path — if PayApplication records exist,
    # use actual billed amounts (sum of work_completed_this_period) instead
    # of SOV scheduled values for more accurate variance analysis.
    # ``csi_code`` lives on ScheduleOfValues, not PayApplicationLineItem,
    # so we join through sov_id.
    try:
        payapp_stmt = (
            select(
                ScheduleOfValues.csi_code,
                PayApplication.project_id,
                func.sum(PayApplicationLineItem.work_completed_this_period).label("billed_total"),
            )
            .join(PayApplication, PayApplicationLineItem.pay_application_id == PayApplication.id)
            .join(ScheduleOfValues, PayApplicationLineItem.sov_id == ScheduleOfValues.id)
            .where(
                PayApplication.project_id.in_(project_ids),
                ScheduleOfValues.csi_code.isnot(None),
            )
            .group_by(ScheduleOfValues.csi_code, PayApplication.project_id)
        )
        if csi_filter:
            payapp_stmt = payapp_stmt.where(ScheduleOfValues.csi_code.startswith(csi_filter))

        payapp_result = await db.execute(payapp_stmt)
        payapp_rows = payapp_result.all()

        if payapp_rows:
            # Override SOV-based actuals with pay app billed amounts where available
            for row in payapp_rows:
                csi = _normalize_csi(row.csi_code)
                if csi and row.billed_total:
                    actuals[(csi, row.project_id)] = float(row.billed_total)
    except Exception as exc:
        logger.debug("PayApplication comparison path failed (using SOV): %s", exc)

    # Compute variance by CSI division
    # Group by CSI division (first 2 digits)
    division_variances: dict[str, list[dict]] = defaultdict(list)

    all_keys = set(estimates.keys()) | set(actuals.keys())
    for csi, pid in all_keys:
        est = estimates.get((csi, pid), 0)
        act = actuals.get((csi, pid), 0)
        if est > 0:
            variance_pct = ((act - est) / est) * 100
            division = csi[:2] if len(csi) >= 2 else csi
            division_variances[division].append(
                {
                    "variance_pct": variance_pct,
                    "project_id": pid,
                    "project_type": project_type_map.get(pid),
                }
            )

    # Build patterns from aggregated data
    patterns: list[CostPattern] = []
    for division, entries in division_variances.items():
        unique_projects = {e["project_id"] for e in entries}
        if len(unique_projects) < min_projects:
            continue

        avg_variance = sum(e["variance_pct"] for e in entries) / len(entries)

        # Determine most common project type
        type_counter = Counter(e["project_type"] for e in entries if e["project_type"])
        common_type = type_counter.most_common(1)[0][0] if type_counter else None

        direction = "over" if avg_variance > 0 else "under"
        desc = (
            f"Division {division} costs averaged {abs(avg_variance):.1f}% "
            f"{direction} estimate across {len(unique_projects)} projects"
        )

        confidence = min(0.95, 0.40 + 0.05 * len(unique_projects))

        patterns.append(
            CostPattern(
                csi_division=division,
                description=desc,
                average_variance_pct=round(avg_variance, 2),
                project_count=len(unique_projects),
                project_type=common_type,
                confidence=round(confidence, 2),
                low_confidence=_low_confidence_cost,
            )
        )

    # Sort by absolute variance (most significant first)
    patterns.sort(key=lambda p: abs(p.average_variance_pct), reverse=True)
    return patterns


def _normalize_csi(code: str | None) -> str | None:
    """Normalize CSI code to standard format (strip spaces, first 2-6 chars)."""
    if not code:
        return None
    return code.strip().replace(" ", "")[:6]


# ---------------------------------------------------------------------------
# Schedule accuracy analysis
# ---------------------------------------------------------------------------


async def analyze_schedule_accuracy(
    db: AsyncSession,
    org_id: uuid.UUID,
    min_project_count: int = 3,
) -> ScheduleAccuracyReport:
    """Analyze schedule accuracy across all org projects.

    Compares original baseline duration to actual duration for completed
    projects. Groups by project type and size.

    Args:
        db: Database session.
        org_id: Organization ID.

    Returns:
        ScheduleAccuracyReport with aggregate statistics.
    """
    projects = await _get_org_projects(db, org_id)
    if not projects:
        return ScheduleAccuracyReport(
            total_projects=0,
            average_duration_variance_pct=0.0,
            on_time_rate=0.0,
        )

    project_ids = [p.id for p in projects]

    # Get baselines with total duration
    baseline_stmt = (
        select(
            ScheduleBaseline.project_id,
            func.min(ScheduleBaseline.total_duration_days).label("baseline_duration"),
        )
        .where(
            ScheduleBaseline.project_id.in_(project_ids),
            ScheduleBaseline.total_duration_days.isnot(None),
            ScheduleBaseline.version == 1,  # Original baseline
        )
        .group_by(ScheduleBaseline.project_id)
    )
    baseline_result = await db.execute(baseline_stmt)
    baselines = {row.project_id: row.baseline_duration for row in baseline_result.all()}

    # Calculate actual durations from project dates
    project_durations: dict[uuid.UUID, dict] = {}
    for project in projects:
        if project.start_date and project.end_date:
            actual_days = (project.end_date - project.start_date).days
            baseline_days = baselines.get(project.id)

            if baseline_days and baseline_days > 0 and actual_days > 0:
                variance_pct = ((actual_days - baseline_days) / baseline_days) * 100
                project_durations[project.id] = {
                    "baseline_days": baseline_days,
                    "actual_days": actual_days,
                    "variance_pct": variance_pct,
                    "on_time": actual_days <= baseline_days,
                    "project_type": project.type,
                    "contract_value": float(project.contract_value)
                    if project.contract_value
                    else None,
                }

    if not project_durations:
        return ScheduleAccuracyReport(
            total_projects=len(projects),
            average_duration_variance_pct=0.0,
            on_time_rate=0.0,
        )

    # Aggregate
    total_with_data = len(project_durations)
    avg_variance = sum(d["variance_pct"] for d in project_durations.values()) / total_with_data
    on_time_count = sum(1 for d in project_durations.values() if d["on_time"])
    on_time_rate = on_time_count / total_with_data

    # Group by project type
    by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "variances": [], "on_time": 0})
    for data in project_durations.values():
        ptype = data["project_type"] or "unknown"
        by_type[ptype]["count"] += 1
        by_type[ptype]["variances"].append(data["variance_pct"])
        if data["on_time"]:
            by_type[ptype]["on_time"] += 1

    by_type_result = {}
    for ptype, info in by_type.items():
        by_type_result[ptype] = {
            "count": info["count"],
            "average_variance_pct": round(sum(info["variances"]) / len(info["variances"]), 2),
            "on_time_rate": round(info["on_time"] / info["count"], 2),
        }

    # Group by project size (contract value buckets)
    by_size: dict[str, dict] = defaultdict(lambda: {"count": 0, "variances": [], "on_time": 0})
    for data in project_durations.values():
        value = data.get("contract_value")
        if value is None:
            bucket = "unknown"
        elif value < 1_000_000:
            bucket = "small_under_1M"
        elif value < 10_000_000:
            bucket = "medium_1M_10M"
        elif value < 50_000_000:
            bucket = "large_10M_50M"
        else:
            bucket = "mega_over_50M"
        by_size[bucket]["count"] += 1
        by_size[bucket]["variances"].append(data["variance_pct"])
        if data["on_time"]:
            by_size[bucket]["on_time"] += 1

    by_size_result = {}
    for bucket, info in by_size.items():
        by_size_result[bucket] = {
            "count": info["count"],
            "average_variance_pct": round(sum(info["variances"]) / len(info["variances"]), 2),
            "on_time_rate": round(info["on_time"] / info["count"], 2),
        }

    # SV-33: Flag low confidence when insufficient projects
    _low_conf = total_with_data < min_project_count

    return ScheduleAccuracyReport(
        total_projects=total_with_data,
        average_duration_variance_pct=round(avg_variance, 2),
        on_time_rate=round(on_time_rate, 2),
        by_project_type=by_type_result,
        by_project_size=by_size_result,
        low_confidence=_low_conf,
    )


# ---------------------------------------------------------------------------
# RFI pattern detection
# ---------------------------------------------------------------------------


async def find_rfi_patterns(
    db: AsyncSession,
    org_id: uuid.UUID,
    building_type: str | None = None,
    min_project_count: int = 3,
) -> list[RFIPattern]:
    """Cluster RFIs by subject keywords across projects.

    Identifies the most common RFI subjects by building/project type.

    Uses SQL aggregation for counting and average resolution time where
    possible, then clusters by subject keywords in Python.

    Args:
        db: Database session.
        org_id: Organization ID.
        building_type: Optional filter by project type.

    Returns:
        List of RFIPattern sorted by occurrence count.
    """
    project_ids = await _get_org_project_ids(db, org_id, building_type)
    if not project_ids:
        return []

    # SV-33: Track low confidence
    _low_conf_rfi = len(project_ids) < min_project_count

    # Fetch only the columns needed for clustering (not full ORM objects)
    rfi_stmt = (
        select(
            RFI.subject,
            RFI.created_at,
            RFI.responded_at,
            RFI.date_answered,
            RFI.project_id,
        )
        .where(RFI.project_id.in_(project_ids))
        .limit(10_000)
    )
    result = await db.execute(rfi_stmt)
    rfis = result.all()

    if not rfis:
        return []

    # Extract keywords from subjects and cluster
    keyword_clusters: dict[str, list[dict]] = defaultdict(list)
    _STOP_WORDS = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "rfi",
        "request",
        "information",
        "re",
        "regarding",
        "about",
        "question",
    }

    for rfi in rfis:
        subject = (rfi.subject or "").lower()
        # Extract significant words
        words = re.findall(r"[a-z]+", subject)
        keywords = [w for w in words if len(w) > 2 and w not in _STOP_WORDS]

        # Use top 2 keywords as cluster key
        if len(keywords) >= 2:
            cluster_key = " ".join(sorted(keywords[:3]))
        elif keywords:
            cluster_key = keywords[0]
        else:
            cluster_key = "general"

        # Calculate resolution time
        resolution_days = None
        answered = rfi.date_answered or rfi.responded_at
        if answered and rfi.created_at:
            delta = answered - rfi.created_at
            resolution_days = delta.total_seconds() / 86400

        keyword_clusters[cluster_key].append(
            {
                "keywords": keywords[:5],
                "resolution_days": resolution_days,
                "project_id": rfi.project_id,
            }
        )

    # Build patterns from clusters
    patterns: list[RFIPattern] = []
    for cluster_key, entries in keyword_clusters.items():
        if len(entries) < 2:
            continue

        resolution_days_list = [
            e["resolution_days"] for e in entries if e["resolution_days"] is not None
        ]
        avg_resolution = (
            sum(resolution_days_list) / len(resolution_days_list) if resolution_days_list else 0.0
        )

        # Most common keywords across all entries in this cluster
        all_kw: Counter[str] = Counter()
        for e in entries:
            all_kw.update(e["keywords"])
        top_keywords = [kw for kw, _ in all_kw.most_common(5)]

        patterns.append(
            RFIPattern(
                subject_cluster=cluster_key,
                occurrence_count=len(entries),
                average_resolution_days=round(avg_resolution, 1),
                most_common_keywords=top_keywords,
                building_type=building_type,
                low_confidence=_low_conf_rfi,
            )
        )

    patterns.sort(key=lambda p: p.occurrence_count, reverse=True)

    # SV-31: Optional embedding-based clustering for more accurate grouping.
    # If the embeddings service is available, embed RFI subjects and cluster
    # by cosine similarity. Falls back to keyword-based results above if
    # embeddings are unavailable.
    try:
        from app.services.rag.embeddings import generate_embeddings

        # Collect unique subjects for embedding
        subjects = [(rfi.subject or "") for rfi in rfis if rfi.subject]
        if len(subjects) >= 5:
            embeddings = await generate_embeddings(subjects)
            if embeddings and len(embeddings) == len(subjects):
                import numpy as _np

                emb_array = _np.array(embeddings)
                # Normalize
                norms = _np.linalg.norm(emb_array, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                emb_norm = emb_array / norms

                # Simple greedy clustering by cosine similarity threshold
                _SIM_THRESHOLD = 0.80
                assigned = [False] * len(subjects)
                embedding_clusters: list[list[int]] = []

                for i in range(len(subjects)):
                    if assigned[i]:
                        continue
                    cluster = [i]
                    assigned[i] = True
                    for j in range(i + 1, len(subjects)):
                        if assigned[j]:
                            continue
                        sim = float(_np.dot(emb_norm[i], emb_norm[j]))
                        if sim >= _SIM_THRESHOLD:
                            cluster.append(j)
                            assigned[j] = True
                    embedding_clusters.append(cluster)

                # Rebuild patterns from embedding clusters (only if meaningful)
                if len(embedding_clusters) < len(subjects):
                    _STOP_WORDS_EMB = _STOP_WORDS  # reuse from above
                    emb_patterns: list[RFIPattern] = []
                    for cluster_indices in embedding_clusters:
                        if len(cluster_indices) < 2:
                            continue
                        cluster_subjects = [subjects[i] for i in cluster_indices]
                        representative = cluster_subjects[0][:80]

                        cluster_rfis = [rfis[i] for i in cluster_indices]
                        res_days = []
                        for rfi_row in cluster_rfis:
                            answered = rfi_row.date_answered or rfi_row.responded_at
                            if answered and rfi_row.created_at:
                                delta = answered - rfi_row.created_at
                                res_days.append(delta.total_seconds() / 86400)

                        avg_resolution = sum(res_days) / len(res_days) if res_days else 0.0

                        all_words: Counter[str] = Counter()
                        for subj in cluster_subjects:
                            words = re.findall(r"[a-z]+", subj.lower())
                            kws = [w for w in words if len(w) > 2 and w not in _STOP_WORDS_EMB]
                            all_words.update(kws)

                        emb_patterns.append(
                            RFIPattern(
                                subject_cluster=representative,
                                occurrence_count=len(cluster_indices),
                                average_resolution_days=round(avg_resolution, 1),
                                most_common_keywords=[kw for kw, _ in all_words.most_common(5)],
                                building_type=building_type,
                            )
                        )

                    if emb_patterns:
                        emb_patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
                        patterns = emb_patterns[:50]
                        logger.info(
                            "Used embedding-based RFI clustering: %d clusters from %d RFIs",
                            len(patterns),
                            len(subjects),
                        )
    except ImportError:
        logger.debug("Embeddings not available for RFI clustering; using keyword-based")
    except Exception as exc:
        logger.debug("Embedding-based RFI clustering failed (using keyword fallback): %s", exc)

    return patterns[:50]  # Cap at top 50 patterns


# ---------------------------------------------------------------------------
# Cost trend analysis
# ---------------------------------------------------------------------------


async def analyze_cost_trends(
    db: AsyncSession,
    org_id: uuid.UUID,
    csi_division: str | None = None,
    min_project_count: int = 3,
) -> list[CostTrendInsight]:
    """Track actual cost per unit by CSI code across projects over time.

    Identifies inflationary trends and seasonal patterns.

    Args:
        db: Database session.
        org_id: Organization ID.
        csi_division: Optional CSI division filter (e.g., "03" for concrete).

    Returns:
        List of CostTrendInsight sorted by annual change magnitude.
    """
    project_ids = await _get_org_project_ids(db, org_id)
    if not project_ids:
        return []

    # Query estimate line items with dates
    stmt = (
        select(
            EstimateLineItem.csi_code,
            EstimateLineItem.unit_cost,
            EstimateLineItem.unit,
            CostEstimate.created_at,
            CostEstimate.project_id,
        )
        .join(CostEstimate, EstimateLineItem.estimate_id == CostEstimate.id)
        .where(
            CostEstimate.project_id.in_(project_ids),
            EstimateLineItem.csi_code.isnot(None),
            EstimateLineItem.unit_cost.isnot(None),
        )
        .order_by(CostEstimate.created_at)
    )
    if csi_division:
        stmt = stmt.where(EstimateLineItem.csi_code.startswith(csi_division))

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return []

    # Group by CSI division
    division_data: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        csi = _normalize_csi(row.csi_code)
        if not csi:
            continue
        division = csi[:2] if len(csi) >= 2 else csi
        division_data[division].append(
            {
                "unit_cost": float(row.unit_cost),
                "date": row.created_at,
                "project_id": row.project_id,
            }
        )

    # Analyze trends per division
    insights: list[CostTrendInsight] = []
    for division, entries in division_data.items():
        if len(entries) < 3:
            continue

        entries.sort(key=lambda e: e["date"])
        unique_projects = {e["project_id"] for e in entries}

        # Calculate average cost by year
        yearly_costs: dict[int, list[float]] = defaultdict(list)
        for e in entries:
            year = e["date"].year
            yearly_costs[year].append(e["unit_cost"])

        yearly_averages = {
            year: sum(costs) / len(costs) for year, costs in sorted(yearly_costs.items())
        }

        if len(yearly_averages) < 2:
            continue

        years = sorted(yearly_averages.keys())
        first_avg = yearly_averages[years[0]]
        last_avg = yearly_averages[years[-1]]
        span_years = years[-1] - years[0]

        if first_avg <= 0 or span_years <= 0:
            continue

        total_change_pct = ((last_avg - first_avg) / first_avg) * 100
        annual_change_pct = total_change_pct / span_years

        if abs(annual_change_pct) < 1.0:
            direction = "stable"
        elif annual_change_pct > 0:
            direction = "increasing"
        else:
            direction = "decreasing"

        desc = (
            f"Division {division} costs {direction} at "
            f"{abs(annual_change_pct):.1f}%/year over {span_years} years"
        )

        data_points = [
            {"year": year, "average_unit_cost": round(avg, 2)}
            for year, avg in sorted(yearly_averages.items())
        ]

        insights.append(
            CostTrendInsight(
                csi_division=division,
                description=desc,
                trend_direction=direction,
                average_annual_change_pct=round(annual_change_pct, 2),
                data_points=data_points,
                project_count=len(unique_projects),
            )
        )

    insights.sort(key=lambda i: abs(i.average_annual_change_pct), reverse=True)
    return insights


# ---------------------------------------------------------------------------
# Risk factor correlation
# ---------------------------------------------------------------------------


async def correlate_risk_factors(
    db: AsyncSession,
    org_id: uuid.UUID,
    min_project_count: int = 3,
) -> list[RiskCorrelation]:
    """Correlate risk register entries against schedule/cost variances.

    Identifies which risk categories most often led to actual delays
    or cost overruns across the organization.

    Args:
        db: Database session.
        org_id: Organization ID.

    Returns:
        List of RiskCorrelation sorted by correlation strength.
    """
    project_ids = await _get_org_project_ids(db, org_id)
    if not project_ids:
        return []

    # Fetch risk register entries
    risk_stmt = (
        select(RiskRegisterEntry).where(RiskRegisterEntry.project_id.in_(project_ids)).limit(5_000)
    )
    result = await db.execute(risk_stmt)
    risks = result.scalars().all()

    if not risks:
        return []

    # Fetch EVM snapshots for cost variance data
    evm_stmt = (
        select(
            EVMSnapshot.project_id,
            func.avg(EVMSnapshot.cpi).label("avg_cpi"),
            func.avg(EVMSnapshot.spi).label("avg_spi"),
        )
        .where(EVMSnapshot.project_id.in_(project_ids))
        .group_by(EVMSnapshot.project_id)
    )
    evm_result = await db.execute(evm_stmt)
    evm_data = {
        row.project_id: {"avg_cpi": float(row.avg_cpi or 1.0), "avg_spi": float(row.avg_spi or 1.0)}
        for row in evm_result.all()
    }

    # Fetch schedule baselines for duration variance
    projects = await _get_org_projects(db, org_id)
    project_schedule: dict[uuid.UUID, dict] = {}
    for p in projects:
        if p.start_date and p.end_date:
            actual_days = (p.end_date - p.start_date).days
            project_schedule[p.id] = {"actual_days": actual_days}

    # Group risks by category
    category_risks: dict[str, list[dict]] = defaultdict(list)
    for risk in risks:
        category = (risk.category or "general").lower()
        pid = risk.project_id

        cost_impact_pct = 0.0
        schedule_impact_days = 0.0

        # Cost impact from EVM
        evm = evm_data.get(pid)
        if evm and evm["avg_cpi"] != 0:
            cost_impact_pct = (1.0 - evm["avg_cpi"]) * 100  # CPI < 1 = over budget

        # Schedule impact from SPI
        if evm and evm["avg_spi"] != 0:
            sched = project_schedule.get(pid)
            if sched:
                # SPI < 1 means behind schedule
                if evm["avg_spi"] < 1.0:
                    schedule_impact_days = sched["actual_days"] * (1.0 - evm["avg_spi"])

        category_risks[category].append(
            {
                "project_id": pid,
                "cost_impact_pct": cost_impact_pct,
                "schedule_impact_days": schedule_impact_days,
                "status": risk.status,
            }
        )

    # Build correlations
    correlations: list[RiskCorrelation] = []
    for category, entries in category_risks.items():
        if not entries:
            continue

        unique_projects = {e["project_id"] for e in entries}
        avg_cost = sum(e["cost_impact_pct"] for e in entries) / len(entries)
        avg_sched = sum(e["schedule_impact_days"] for e in entries) / len(entries)

        # Determine correlation strength
        triggered = [e for e in entries if e["status"] in ("triggered", "occurred", "closed")]
        trigger_rate = len(triggered) / len(entries) if entries else 0

        if trigger_rate > 0.5 and (abs(avg_cost) > 10 or avg_sched > 30):
            strength = "strong"
        elif trigger_rate > 0.3 and (abs(avg_cost) > 5 or avg_sched > 14):
            strength = "moderate"
        else:
            strength = "weak"

        correlations.append(
            RiskCorrelation(
                risk_category=category,
                occurrence_count=len(entries),
                avg_cost_impact_pct=round(avg_cost, 2),
                avg_schedule_impact_days=round(avg_sched, 1),
                projects_affected=len(unique_projects),
                correlation_strength=strength,
            )
        )

    # Sort by strength then occurrence count
    strength_order = {"strong": 0, "moderate": 1, "weak": 2}
    correlations.sort(
        key=lambda c: (strength_order.get(c.correlation_strength, 3), -c.occurrence_count)
    )
    return correlations


# ---------------------------------------------------------------------------
# Natural language query
# ---------------------------------------------------------------------------


async def query_cross_project(
    db: AsyncSession,
    org_id: uuid.UUID,
    question: str,
    llm_gateway: Any = None,
) -> CrossProjectAnswer:
    """Answer a natural language question using cross-project data.

    Uses LLM with aggregated org data as context. Caches results in
    CrossProjectInsight with TTL.

    Args:
        db: Database session.
        org_id: Organization ID.
        question: Natural language question about org performance.
        llm_gateway: Optional LLMGateway instance.

    Returns:
        CrossProjectAnswer with answer text and supporting data.
    """
    # SECURITY: Sanitize question
    sanitized_question = sanitize_for_prompt(question, max_length=1000)

    # Check cache
    query_hash = _compute_query_hash("nl_query", {"question": sanitized_question})
    cache_stmt = (
        select(CrossProjectInsight)
        .where(
            CrossProjectInsight.org_id == org_id,
            CrossProjectInsight.query_hash == query_hash,
            CrossProjectInsight.expires_at > datetime.now(UTC),
        )
        .order_by(CrossProjectInsight.created_at.desc())
        .limit(1)
    )
    cache_result = await db.execute(cache_stmt)
    cached = cache_result.scalar_one_or_none()
    if cached:
        return CrossProjectAnswer(
            question=sanitized_question,
            answer=cached.result.get("answer", ""),
            confidence=float(cached.confidence),
            source_project_count=cached.source_project_count,
            supporting_data=cached.result.get("supporting_data", {}),
            cached=True,
        )

    # Gather aggregate data for context
    projects = await _get_org_projects(db, org_id)
    project_count = len(projects)

    if project_count == 0:
        return CrossProjectAnswer(
            question=sanitized_question,
            answer="No projects found for this organization.",
            confidence=0.0,
            source_project_count=0,
        )

    # Build context summary
    context_parts = [f"Organization has {project_count} projects."]

    # Project type distribution
    type_counts = Counter(p.type for p in projects if p.type)
    if type_counts:
        context_parts.append(
            "Project types: " + ", ".join(f"{t}: {c}" for t, c in type_counts.most_common(10))
        )

    # Contract value statistics
    values = [float(p.contract_value) for p in projects if p.contract_value]
    if values:
        avg_val = sum(values) / len(values)
        total_val = sum(values)
        context_parts.append(
            f"Total contract value: ${total_val:,.0f}. "
            f"Average: ${avg_val:,.0f}. Range: ${min(values):,.0f} - ${max(values):,.0f}."
        )

    # Status distribution
    status_counts = Counter(p.status for p in projects)
    context_parts.append(
        "Status distribution: " + ", ".join(f"{s}: {c}" for s, c in status_counts.most_common())
    )

    # Get recent EVM data for cost performance
    project_ids = [p.id for p in projects]
    evm_stmt = select(
        func.avg(EVMSnapshot.cpi).label("avg_cpi"),
        func.avg(EVMSnapshot.spi).label("avg_spi"),
    ).where(EVMSnapshot.project_id.in_(project_ids))
    evm_result = await db.execute(evm_stmt)
    evm_row = evm_result.one_or_none()
    if evm_row and evm_row.avg_cpi:
        context_parts.append(
            f"Average CPI: {float(evm_row.avg_cpi):.3f}, Average SPI: {float(evm_row.avg_spi or 1.0):.3f}."
        )

    # RFI stats
    rfi_count_stmt = select(func.count(RFI.id)).where(RFI.project_id.in_(project_ids))
    rfi_result = await db.execute(rfi_count_stmt)
    rfi_count = rfi_result.scalar() or 0
    context_parts.append(f"Total RFIs across projects: {rfi_count}.")

    context = "\n".join(context_parts)

    # LLM query
    if llm_gateway is None:
        from app.services.reliability.llm_gateway import get_llm_gateway

        llm_gateway = await get_llm_gateway()

    prompt = (
        "You are a construction project portfolio analyst. "
        "Answer the following question using ONLY the data provided. "
        "Be specific and cite numbers where available. "
        "If the data is insufficient, say so.\n\n"
        f"Portfolio Data:\n{context}\n\n"
        f"Question: {sanitized_question}"
    )

    messages = [
        {"role": "system", "content": "You are a construction portfolio analyst."},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm_gateway.complete(
            messages=messages,
            agent_name="cross_project_analytics",
            org_id=str(org_id),
            temperature=0.2,
            max_tokens=1024,
        )
        answer_text = result.get("content", "Unable to generate an answer.")
        confidence = 0.70
    except Exception as exc:
        logger.error("LLM query failed: %s", exc)
        answer_text = "AI analysis is temporarily unavailable. Please try again later."
        confidence = 0.0

    # Cache the result
    insight = CrossProjectInsight(
        org_id=org_id,
        insight_type="nl_query",
        query_hash=query_hash,
        parameters=_anonymize_parameters({"question": sanitized_question}),
        result={"answer": answer_text, "supporting_data": {"project_count": project_count}},
        source_project_count=project_count,
        confidence=Decimal(str(confidence)),
        expires_at=datetime.now(UTC) + timedelta(hours=_DEFAULT_INSIGHT_TTL_HOURS),
    )
    db.add(insight)
    await db.flush()

    return CrossProjectAnswer(
        question=sanitized_question,
        answer=answer_text,
        confidence=confidence,
        source_project_count=project_count,
        supporting_data={"project_count": project_count},
    )


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


async def get_cached_insights(
    db: AsyncSession,
    org_id: uuid.UUID,
    insight_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Get cached insights for an organization.

    Args:
        db: Database session.
        org_id: Organization ID.
        insight_type: Optional filter by type.
        limit: Maximum results.

    Returns:
        List of cached insight dicts.
    """
    stmt = (
        select(CrossProjectInsight)
        .where(CrossProjectInsight.org_id == org_id)
        .order_by(CrossProjectInsight.created_at.desc())
        .limit(min(limit, 200))
    )

    if insight_type:
        stmt = stmt.where(CrossProjectInsight.insight_type == insight_type)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "id": str(row.id),
            "insight_type": row.insight_type,
            "parameters": row.parameters,
            "result": row.result,
            "source_project_count": row.source_project_count,
            "confidence": float(row.confidence),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "is_expired": (row.expires_at < datetime.now(UTC) if row.expires_at else False),
        }
        for row in rows
    ]
