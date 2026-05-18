"""Ask ConstructAI — natural language query engine for construction project data.

Pipeline:
  1. Sanitize user question via sanitize_for_prompt()
  2. Classify intent via LLM (what domain is the user asking about?)
  3. Route to data gatherers to fetch relevant context from the database
  4. Generate a response with citations using LLM
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reliability.llm_gateway import LLMGateway, get_llm_gateway
from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory response cache with TTL
# ---------------------------------------------------------------------------

_ASK_CACHE_TTL_SECONDS = 300  # 5 minutes


class _AskCache:
    """Simple in-memory cache with TTL for ask() responses.

    Cache key = SHA-256 of (project_id, question).
    Entries expire after ``_ASK_CACHE_TTL_SECONDS``.
    """

    def __init__(self, ttl: int = _ASK_CACHE_TTL_SECONDS, maxlen: int = 500):
        self._ttl = ttl
        self._maxlen = maxlen
        self._store: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, result)
        self._lock = asyncio.Lock()

    @staticmethod
    def _make_key(project_id: uuid.UUID, question: str) -> str:
        raw = f"{project_id}:{question.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get(self, project_id: uuid.UUID, question: str) -> Any | None:
        async with self._lock:
            key = self._make_key(project_id, question)
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, result = entry
            if time.monotonic() - ts > self._ttl:
                # Expired
                del self._store[key]
                return None
            return result

    async def put(self, project_id: uuid.UUID, question: str, result: Any) -> None:
        async with self._lock:
            key = self._make_key(project_id, question)
            # Evict oldest entries if at capacity
            if len(self._store) >= self._maxlen and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (time.monotonic(), result)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


_ask_cache = _AskCache()

# ---------------------------------------------------------------------------
# Agent name used for LLM gateway usage tracking
# ---------------------------------------------------------------------------

_AGENT_NAME = "ask_constructai"

# ---------------------------------------------------------------------------
# SV-02: In-memory conversation history for follow-up context
# ---------------------------------------------------------------------------

_MAX_MESSAGES_PER_CONVERSATION = 10
_MAX_CONVERSATIONS = 1000

# conversation_id -> list of {question, answer, timestamp}
_conversation_history: OrderedDict[str, list[dict]] = OrderedDict()


def _record_conversation_turn(conversation_id: str, question: str, answer: str) -> None:
    """Record a Q&A turn in the conversation history."""
    if not conversation_id:
        return

    # Evict oldest conversation if at capacity
    if (
        conversation_id not in _conversation_history
        and len(_conversation_history) >= _MAX_CONVERSATIONS
    ):
        _conversation_history.popitem(last=False)

    if conversation_id not in _conversation_history:
        _conversation_history[conversation_id] = []

    _conversation_history[conversation_id].append(
        {
            "question": question,
            "answer": answer[:500],  # cap stored answer length
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    # Cap messages per conversation
    if len(_conversation_history[conversation_id]) > _MAX_MESSAGES_PER_CONVERSATION:
        _conversation_history[conversation_id] = _conversation_history[conversation_id][
            -_MAX_MESSAGES_PER_CONVERSATION:
        ]

    # Move to end (most recently used)
    _conversation_history.move_to_end(conversation_id)


def _get_conversation_context(conversation_id: str | None) -> str:
    """Return the last 3 Q&A pairs as context text for the LLM prompt."""
    if not conversation_id or conversation_id not in _conversation_history:
        return ""

    recent = _conversation_history[conversation_id][-3:]
    if not recent:
        return ""

    lines = ["Previous conversation context:"]
    for turn in recent:
        lines.append(f"User: {turn['question']}")
        lines.append(f"Assistant: {turn['answer']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IntentClassification:
    """Result of intent classification for a user question."""

    primary_intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class ContextChunk:
    """A single piece of context gathered from the database."""

    source: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 1.0


@dataclass
class Citation:
    """A reference to a specific data source in the generated answer."""

    source: str
    page: int | None = None
    section: str | None = None
    excerpt: str = ""


@dataclass
class AskResult:
    """Complete result returned by the Ask pipeline."""

    answer: str
    intent: str
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0
    data_sources: list[str] = field(default_factory=list)
    follow_up_suggestions: list[str] = field(default_factory=list)
    processing_time_ms: int = 0


# ---------------------------------------------------------------------------
# Intent definitions
# ---------------------------------------------------------------------------

ALL_INTENTS = [
    "schedule",
    "cost",
    "safety",
    "rfi",
    "quality",
    "evm",
    "change_order",
    "pay_application",
    "document",
    "weather",
    "general",
]

_INTENT_CLASSIFICATION_PROMPT = """\
You are a construction project assistant. Classify the user's question into exactly one intent category.

Categories:
- schedule: questions about activities, milestones, critical path, project timeline, delays, float
- cost: questions about cost estimates, line items, budgets, unit costs, pricing
- safety: questions about safety incidents, alerts, risk scores, PPE, OSHA
- rfi: questions about RFIs (Requests for Information), open/closed status, responses
- quality: questions about inspections, defect reports, quality scores, NCRs
- evm: questions about earned value, SPI, CPI, EAC, ETC, planned vs actual cost/schedule
- change_order: questions about change orders, PCOs, CORs, cost/schedule impact of changes
- pay_application: questions about pay applications, schedule of values, billing, retainage, G702/G703
- document: questions about project documents, specs, drawings, submittals
- weather: questions about weather conditions, forecasts, weather impact on construction
- general: anything that does not clearly fit another category

Also extract any entities mentioned:
- status: e.g. "open", "closed", "critical", "overdue"
- date_range: e.g. "last week", "this month"
- activity_name: specific activity names mentioned
- item_type: specific types (e.g. "concrete", "steel")
- aggregation: "count", "total", "sum", "average", "how many"

Respond with JSON only, no other text:
{"intent": "<category>", "entities": {<extracted_entities>}, "confidence": <0.0-1.0>}

User question: <user_query>{question}</user_query>
"""

_AGGREGATION_PATTERNS = [
    (r"\bhow\s+many\b", "count"),
    (r"\btotal\b", "sum"),
    (r"\bcount\b", "count"),
    (r"\bsum\b", "sum"),
    (r"\baverage\b", "average"),
    (r"\bmean\b", "average"),
    (r"\boverall\b", "sum"),
]


# ---------------------------------------------------------------------------
# AskService
# ---------------------------------------------------------------------------


class AskService:
    """Core 'Ask ConstructAI' natural language query service.

    Orchestrates the full pipeline: sanitize -> classify -> gather -> respond.
    """

    def __init__(self, llm_gateway: LLMGateway | None = None) -> None:
        self._llm: LLMGateway | None = llm_gateway

    async def _get_llm(self) -> LLMGateway:
        if self._llm is None:
            self._llm = await get_llm_gateway()
        return self._llm

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        project_id: uuid.UUID,
        org_id: str,
        db: AsyncSession,
        conversation_id: str | None = None,
    ) -> AskResult:
        """Main entry point. Returns a fully formed AskResult."""
        start_ns = time.monotonic_ns()

        # Check cache first (keyed on project_id + question)
        # Skip cache when continuing a conversation (follow-ups need fresh context)
        if not conversation_id:
            cached = await _ask_cache.get(project_id, question)
            if cached is not None:
                logger.debug("Ask cache hit for project %s", project_id)
                cached.processing_time_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
                return cached

        # 1. Sanitize
        sanitized = sanitize_for_prompt(question)

        # 2. Classify intent
        classification = await self._classify_intent(sanitized, org_id)

        # 3. Gather context from the appropriate data domain
        context_chunks = await self._gather_context(classification, project_id, sanitized, db)

        # 4. Generate response (with conversation context if available)
        conv_context = _get_conversation_context(conversation_id)
        result = await self._generate_response(
            sanitized,
            context_chunks,
            classification,
            org_id,
            conversation_context=conv_context,
        )

        result.processing_time_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)

        # Cache the result (TTL = 5 minutes) — only for non-conversation queries
        if not conversation_id:
            await _ask_cache.put(project_id, question, result)

        # Record this turn in conversation history
        if conversation_id:
            _record_conversation_turn(conversation_id, question, result.answer)

        return result

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------

    async def _classify_intent(self, question: str, org_id: str) -> IntentClassification:
        """Use LLM to classify the question into a domain intent."""
        prompt = _INTENT_CLASSIFICATION_PROMPT.replace("{question}", question)

        try:
            llm = await self._get_llm()
            response = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                agent_name=_AGENT_NAME,
                org_id=org_id,
                task_class="classification",
                temperature=0.0,
                max_tokens=256,
            )
            raw = response.get("content", "").strip()

            # Extract JSON from the response (handle markdown fences)
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                intent = parsed.get("intent", "general")
                if intent not in ALL_INTENTS:
                    intent = "general"
                return IntentClassification(
                    primary_intent=intent,
                    entities=parsed.get("entities", {}),
                    confidence=float(parsed.get("confidence", 0.5)),
                )
        except Exception:
            logger.warning(
                "Intent classification failed for question, defaulting to general",
                exc_info=True,
            )

        # Check for aggregation keywords as a heuristic fallback
        entities: dict[str, Any] = {}
        for pattern, agg_type in _AGGREGATION_PATTERNS:
            if re.search(pattern, question, re.IGNORECASE):
                entities["aggregation"] = agg_type
                break

        return IntentClassification(
            primary_intent="general",
            entities=entities,
            confidence=0.3,
        )

    # ------------------------------------------------------------------
    # Context gathering dispatcher
    # ------------------------------------------------------------------

    async def _gather_context(
        self,
        classification: IntentClassification,
        project_id: uuid.UUID,
        question: str,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Route to the appropriate data gatherer based on intent."""
        intent = classification.primary_intent
        entities = classification.entities

        gatherer_map: dict[str, Any] = {
            "schedule": self._gather_schedule_data,
            "cost": self._gather_cost_data,
            "safety": self._gather_safety_data,
            "rfi": self._gather_rfi_data,
            "quality": self._gather_quality_data,
            "evm": self._gather_evm_data,
            "change_order": self._gather_change_order_data,
            "pay_application": self._gather_pay_app_data,
            "document": self._gather_document_data,
            "weather": self._gather_weather_data,
        }

        chunks: list[ContextChunk] = []

        gatherer = gatherer_map.get(intent)
        if gatherer:
            if intent in ("document", "weather"):
                chunks = await gatherer(project_id, entities, question, db)
            else:
                chunks = await gatherer(project_id, entities, db)

        # Also check for aggregation patterns regardless of intent
        if entities.get("aggregation") or any(
            re.search(p, question, re.IGNORECASE) for p, _ in _AGGREGATION_PATTERNS
        ):
            agg_chunks = await self._gather_aggregation_data(project_id, entities, question, db)
            chunks.extend(agg_chunks)

        # For general intent or if no data found, try document search
        if not chunks and intent == "general":
            chunks = await self._gather_document_data(project_id, entities, question, db)

        return chunks

    # ------------------------------------------------------------------
    # Domain-specific data gatherers
    # ------------------------------------------------------------------

    async def _gather_schedule_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query schedule activities and optionally run CPM."""
        from app.models.scheduling import ScheduleActivity

        chunks: list[ContextChunk] = []

        # Build filters
        stmt = select(ScheduleActivity).where(ScheduleActivity.project_id == project_id)

        status = entities.get("status")
        if status:
            stmt = stmt.where(ScheduleActivity.status == status)

        # If asking about critical path, filter for critical activities
        wants_critical = any(
            kw in str(entities) for kw in ("critical", "critical_path", "critical path")
        )
        if wants_critical:
            stmt = stmt.where(ScheduleActivity.is_critical.is_(True))

        stmt = stmt.order_by(ScheduleActivity.start_date.asc().nullslast()).limit(50)
        result = await db.execute(stmt)
        activities = result.scalars().all()

        if activities:
            lines = []
            for act in activities:
                line = (
                    f"Activity: {act.name} (Code: {act.activity_code})"
                    f" | Duration: {act.duration_days}d"
                    f" | Start: {act.start_date}"
                    f" | Finish: {act.finish_date}"
                    f" | Status: {act.status}"
                    f" | Float: {act.total_float}d"
                    f" | Critical: {act.is_critical}"
                    f" | Progress: {act.pct_complete}%"
                )
                lines.append(line)

            chunks.append(
                ContextChunk(
                    source="Schedule Activities",
                    content="\n".join(lines),
                    metadata={
                        "activity_count": len(activities),
                        "critical_count": sum(1 for a in activities if a.is_critical),
                    },
                    relevance_score=0.95,
                )
            )

        # Optionally run CPM if asking about critical path
        if wants_critical and activities:
            try:
                from app.services.scheduling.cpm_engine import calculate_cpm

                cpm_input = []
                for act in activities:
                    cpm_input.append(
                        {
                            "id": str(act.id),
                            "name": act.name,
                            "duration_days": act.duration_days,
                            "predecessors": act.predecessors or [],
                        }
                    )

                cpm_result = await calculate_cpm(cpm_input)
                cpm_summary = (
                    f"CPM Analysis: Project duration = {cpm_result['project_duration']} days, "
                    f"Critical path length = {cpm_result['critical_path_length']} activities"
                )
                chunks.append(
                    ContextChunk(
                        source="CPM Analysis",
                        content=cpm_summary,
                        metadata={"project_duration": cpm_result["project_duration"]},
                        relevance_score=0.98,
                    )
                )
            except Exception:
                logger.warning("CPM calculation failed", exc_info=True)

        return chunks

    async def _gather_cost_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query cost estimates and line items."""
        from app.models.estimating import CostEstimate, EstimateLineItem

        chunks: list[ContextChunk] = []

        # Get estimates
        stmt = (
            select(CostEstimate)
            .where(CostEstimate.project_id == project_id)
            .order_by(CostEstimate.created_at.desc())
            .limit(10)
        )
        result = await db.execute(stmt)
        estimates = result.scalars().all()

        if estimates:
            lines = []
            for est in estimates:
                total_str = f"${est.total_cost:,.2f}" if est.total_cost else "N/A"
                line = (
                    f"Estimate: {est.name} ({est.estimate_type})"
                    f" | Status: {est.status}"
                    f" | Total: {total_str}"
                )
                if est.contingency_pct:
                    line += f" | Contingency: {est.contingency_pct}%"
                if est.monte_carlo_p50:
                    line += f" | P50: ${est.monte_carlo_p50:,.2f}"
                if est.monte_carlo_p80:
                    line += f" | P80: ${est.monte_carlo_p80:,.2f}"
                lines.append(line)

            # Get top line items from the most recent estimate
            latest = estimates[0]
            li_stmt = (
                select(EstimateLineItem)
                .where(EstimateLineItem.estimate_id == latest.id)
                .order_by(EstimateLineItem.total_cost.desc())
                .limit(20)
            )
            li_result = await db.execute(li_stmt)
            line_items = li_result.scalars().all()

            if line_items:
                lines.append(f"\nTop line items from '{latest.name}':")
                for li in line_items:
                    lines.append(
                        f"  - {li.description} ({li.csi_code or 'N/A'})"
                        f" | Qty: {li.quantity} {li.unit}"
                        f" | Unit Cost: ${li.unit_cost:,.2f}"
                        f" | Total: ${li.total_cost:,.2f}"
                    )

            total_all = sum((e.total_cost or Decimal(0)) for e in estimates)
            chunks.append(
                ContextChunk(
                    source="Cost Estimates",
                    content="\n".join(lines),
                    metadata={
                        "estimate_count": len(estimates),
                        "total_across_estimates": str(total_all),
                    },
                    relevance_score=0.95,
                )
            )

        return chunks

    async def _gather_safety_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query safety alerts and daily risk scores."""
        from app.models.osha import DailyRiskScore
        from app.models.safety_incident import SafetyAlert

        chunks: list[ContextChunk] = []

        # Recent safety alerts
        stmt = (
            select(SafetyAlert)
            .where(SafetyAlert.project_id == project_id)
            .order_by(SafetyAlert.created_at.desc())
            .limit(20)
        )
        result = await db.execute(stmt)
        alerts = result.scalars().all()

        if alerts:
            lines = []
            for alert in alerts:
                line = (
                    f"Alert [{alert.priority}]: {alert.alert_type}"
                    f" — {alert.description}"
                    f" | Confidence: {alert.confidence}"
                    f" | Acknowledged: {alert.is_acknowledged}"
                    f" | Date: {alert.created_at.date() if alert.created_at else 'N/A'}"
                )
                lines.append(line)

            unacknowledged = sum(1 for a in alerts if not a.is_acknowledged)
            chunks.append(
                ContextChunk(
                    source="Safety Alerts",
                    content="\n".join(lines),
                    metadata={
                        "total_alerts": len(alerts),
                        "unacknowledged_count": unacknowledged,
                    },
                    relevance_score=0.95,
                )
            )

        # Latest daily risk score
        risk_stmt = (
            select(DailyRiskScore)
            .where(DailyRiskScore.project_id == project_id)
            .order_by(DailyRiskScore.score_date.desc())
            .limit(5)
        )
        risk_result = await db.execute(risk_stmt)
        risk_scores = risk_result.scalars().all()

        if risk_scores:
            lines = []
            for rs in risk_scores:
                lines.append(
                    f"Risk Score ({rs.score_date}): {rs.overall_score}/100"
                    f" | Top Risks: {json.dumps(rs.top_risks[:3])}"
                )
            chunks.append(
                ContextChunk(
                    source="Daily Risk Scores",
                    content="\n".join(lines),
                    metadata={"latest_score": risk_scores[0].overall_score},
                    relevance_score=0.90,
                )
            )

        return chunks

    async def _gather_rfi_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query RFIs with status filtering and summary statistics."""
        from app.models.communication import RFI

        chunks: list[ContextChunk] = []

        stmt = select(RFI).where(RFI.project_id == project_id)

        status = entities.get("status")
        if status and status in ("open", "closed", "answered", "overdue"):
            stmt = stmt.where(RFI.status == status)

        stmt = stmt.order_by(RFI.created_at.desc()).limit(30)
        result = await db.execute(stmt)
        rfis = result.scalars().all()

        if rfis:
            lines = []
            for rfi in rfis:
                line = (
                    f"RFI {rfi.rfi_number}: {rfi.subject}"
                    f" | Status: {rfi.status}"
                    f" | Priority: {rfi.priority}"
                    f" | Due: {rfi.due_date or 'N/A'}"
                )
                if rfi.cost_impact:
                    line += f" | Cost Impact: ${rfi.cost_impact_amount or 'Yes'}"
                if rfi.schedule_impact:
                    line += f" | Schedule Impact: {rfi.schedule_impact_days or 'Yes'}d"
                lines.append(line)

            # Compute status counts
            all_rfis_stmt = (
                select(RFI.status, func.count(RFI.id))
                .where(RFI.project_id == project_id)
                .group_by(RFI.status)
            )
            count_result = await db.execute(all_rfis_stmt)
            status_counts = {row[0]: row[1] for row in count_result.all()}

            # Count overdue
            today = date.today()
            overdue_stmt = select(func.count(RFI.id)).where(
                RFI.project_id == project_id,
                RFI.status == "open",
                RFI.due_date < today,
            )
            overdue_result = await db.execute(overdue_stmt)
            overdue_count = overdue_result.scalar() or 0

            summary = f"\nRFI Summary: {json.dumps(status_counts)} | Overdue: {overdue_count}"
            lines.append(summary)

            chunks.append(
                ContextChunk(
                    source="RFIs",
                    content="\n".join(lines),
                    metadata={
                        "status_counts": status_counts,
                        "overdue_count": overdue_count,
                    },
                    relevance_score=0.95,
                )
            )

        return chunks

    async def _gather_quality_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query inspections and defect reports."""
        from app.models.quality import DefectReport, Inspection

        chunks: list[ContextChunk] = []

        # Recent inspections
        insp_stmt = (
            select(Inspection)
            .where(Inspection.project_id == project_id)
            .order_by(Inspection.created_at.desc())
            .limit(15)
        )
        insp_result = await db.execute(insp_stmt)
        inspections = insp_result.scalars().all()

        if inspections:
            lines = []
            for insp in inspections:
                line = (
                    f"Inspection [{insp.inspection_type}]: {insp.status}"
                    f" | Score: {insp.score or 'N/A'}"
                    f" | Location: {insp.location or 'N/A'}"
                    f" | Date: {insp.completed_at.date() if insp.completed_at else insp.scheduled_at}"
                )
                lines.append(line)

            chunks.append(
                ContextChunk(
                    source="Inspections",
                    content="\n".join(lines),
                    metadata={"inspection_count": len(inspections)},
                    relevance_score=0.90,
                )
            )

        # Defect reports
        defect_stmt = (
            select(DefectReport)
            .where(DefectReport.project_id == project_id)
            .order_by(DefectReport.created_at.desc())
            .limit(15)
        )
        defect_result = await db.execute(defect_stmt)
        defects = defect_result.scalars().all()

        if defects:
            lines = []
            for d in defects:
                line = (
                    f"Defect [{d.severity}]: {d.defect_type}"
                    f" — {d.description[:100]}"
                    f" | Status: {d.status}"
                    f" | Location: {d.location or 'N/A'}"
                )
                lines.append(line)

            open_count = sum(1 for d in defects if d.status == "open")
            chunks.append(
                ContextChunk(
                    source="Defect Reports",
                    content="\n".join(lines),
                    metadata={
                        "defect_count": len(defects),
                        "open_defects": open_count,
                    },
                    relevance_score=0.90,
                )
            )

        return chunks

    async def _gather_evm_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Get the latest EVM snapshot and compute metrics."""
        from app.models.evm import EVMSnapshot

        chunks: list[ContextChunk] = []

        stmt = (
            select(EVMSnapshot)
            .where(EVMSnapshot.project_id == project_id)
            .order_by(EVMSnapshot.snapshot_date.desc())
            .limit(5)
        )
        result = await db.execute(stmt)
        snapshots = result.scalars().all()

        if snapshots:
            lines = []
            for snap in snapshots:
                line = (
                    f"EVM Snapshot ({snap.snapshot_date}):"
                    f" BAC=${snap.bac:,.2f}"
                    f" | PV=${snap.pv:,.2f}"
                    f" | EV=${snap.ev:,.2f}"
                    f" | AC=${snap.ac:,.2f}"
                    f" | SPI={snap.spi}"
                    f" | CPI={snap.cpi}"
                    f" | EAC=${snap.eac:,.2f}"
                    f" | SV=${snap.sv:,.2f}"
                    f" | CV=${snap.cv:,.2f}"
                    f" | Complete: {snap.percent_complete}%"
                )
                lines.append(line)

            latest = snapshots[0]

            # Compute derived metrics using evm_engine
            try:
                from app.services.controls.evm_engine import calculate_evm_metrics

                metrics = calculate_evm_metrics(latest.bac, latest.pv, latest.ev, latest.ac)
                interpretation_parts = []
                spi_val = metrics.get("spi")
                cpi_val = metrics.get("cpi")
                if spi_val is not None:
                    if spi_val < Decimal("0.95"):
                        interpretation_parts.append(f"Schedule is BEHIND (SPI={spi_val})")
                    elif spi_val > Decimal("1.05"):
                        interpretation_parts.append(f"Schedule is AHEAD (SPI={spi_val})")
                    else:
                        interpretation_parts.append(f"Schedule is ON TRACK (SPI={spi_val})")

                if cpi_val is not None:
                    if cpi_val < Decimal("0.95"):
                        interpretation_parts.append(f"Cost is OVER BUDGET (CPI={cpi_val})")
                    elif cpi_val > Decimal("1.05"):
                        interpretation_parts.append(f"Cost is UNDER BUDGET (CPI={cpi_val})")
                    else:
                        interpretation_parts.append(f"Cost is ON BUDGET (CPI={cpi_val})")

                eac_val = metrics.get("eac")
                if eac_val is not None:
                    interpretation_parts.append(f"Estimated final cost (EAC): ${eac_val:,.2f}")
                vac_val = metrics.get("vac")
                if vac_val is not None:
                    interpretation_parts.append(f"Variance at Completion (VAC): ${vac_val:,.2f}")

                if interpretation_parts:
                    lines.append("\nEVM Interpretation: " + " | ".join(interpretation_parts))
            except Exception:
                logger.warning("EVM metric computation failed", exc_info=True)

            chunks.append(
                ContextChunk(
                    source="EVM Snapshots",
                    content="\n".join(lines),
                    metadata={
                        "snapshot_count": len(snapshots),
                        "latest_spi": str(latest.spi),
                        "latest_cpi": str(latest.cpi),
                    },
                    relevance_score=0.98,
                )
            )

        return chunks

    async def _gather_change_order_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query PCOs, CORs, and COs."""
        from app.models.change_order_lifecycle import (
            ChangeOrderRequest,
            PotentialChangeOrder,
        )
        from app.models.evm import ChangeOrder

        chunks: list[ContextChunk] = []

        # Change Orders
        co_stmt = (
            select(ChangeOrder)
            .where(ChangeOrder.project_id == project_id)
            .order_by(ChangeOrder.submitted_at.desc())
            .limit(20)
        )
        co_result = await db.execute(co_stmt)
        change_orders = co_result.scalars().all()

        # PCOs
        pco_stmt = (
            select(PotentialChangeOrder)
            .where(PotentialChangeOrder.project_id == project_id)
            .order_by(PotentialChangeOrder.created_at.desc())
            .limit(20)
        )
        pco_result = await db.execute(pco_stmt)
        pcos = pco_result.scalars().all()

        # CORs
        cor_stmt = (
            select(ChangeOrderRequest)
            .where(ChangeOrderRequest.project_id == project_id)
            .order_by(ChangeOrderRequest.created_at.desc())
            .limit(20)
        )
        cor_result = await db.execute(cor_stmt)
        cors = cor_result.scalars().all()

        lines = []

        if pcos:
            lines.append(f"Potential Change Orders (PCOs): {len(pcos)}")
            total_pco_cost = sum((p.total_cost or Decimal(0)) for p in pcos)
            lines.append(f"  Total PCO Value: ${total_pco_cost:,.2f}")
            for pco in pcos[:10]:
                lines.append(
                    f"  PCO-{pco.pco_number}: {pco.title}"
                    f" | Status: {pco.status}"
                    f" | Cost: ${pco.total_cost:,.2f}"
                    f" | Schedule: {pco.schedule_impact_days}d"
                )

        if cors:
            lines.append(f"\nChange Order Requests (CORs): {len(cors)}")
            total_cor_cost = sum((c.total_cost or Decimal(0)) for c in cors)
            lines.append(f"  Total COR Value: ${total_cor_cost:,.2f}")
            for cor in cors[:10]:
                lines.append(
                    f"  COR-{cor.cor_number}: {cor.title}"
                    f" | Status: {cor.status}"
                    f" | Cost: ${cor.total_cost:,.2f}"
                )

        if change_orders:
            lines.append(f"\nApproved Change Orders (COs): {len(change_orders)}")
            total_co_value = sum((co.cost_impact or Decimal(0)) for co in change_orders)
            lines.append(f"  Total CO Value: ${total_co_value:,.2f}")
            for co in change_orders[:10]:
                lines.append(
                    f"  {co.co_number}: {co.title}"
                    f" | Status: {co.status}"
                    f" | Cost Impact: ${co.cost_impact:,.2f}"
                    f" | Schedule Impact: {co.schedule_impact_days}d"
                )

        if lines:
            chunks.append(
                ContextChunk(
                    source="Change Orders",
                    content="\n".join(lines),
                    metadata={
                        "pco_count": len(pcos),
                        "cor_count": len(cors),
                        "co_count": len(change_orders),
                    },
                    relevance_score=0.95,
                )
            )

        return chunks

    async def _gather_pay_app_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Query pay applications and schedule of values."""
        from app.models.pay_application import (
            PayApplication,
            ScheduleOfValues,
        )

        chunks: list[ContextChunk] = []

        # Pay applications
        pa_stmt = (
            select(PayApplication)
            .where(PayApplication.project_id == project_id)
            .order_by(PayApplication.application_number.desc())
            .limit(10)
        )
        pa_result = await db.execute(pa_stmt)
        pay_apps = pa_result.scalars().all()

        if pay_apps:
            lines = []
            for pa in pay_apps:
                lines.append(
                    f"Pay App #{pa.application_number} (Period to {pa.period_to}):"
                    f" Status: {pa.status}"
                    f" | Contract Sum: ${pa.contract_sum_to_date:,.2f}"
                    f" | Completed/Stored: ${pa.total_completed_and_stored:,.2f}"
                    f" | Retainage: ${pa.total_retainage:,.2f}"
                    f" | Current Due: ${pa.current_payment_due:,.2f}"
                    f" | Balance: ${pa.balance_to_finish_including_retainage:,.2f}"
                )

            chunks.append(
                ContextChunk(
                    source="Pay Applications",
                    content="\n".join(lines),
                    metadata={"pay_app_count": len(pay_apps)},
                    relevance_score=0.95,
                )
            )

        # Schedule of values summary
        sov_stmt = (
            select(ScheduleOfValues)
            .where(ScheduleOfValues.project_id == project_id)
            .order_by(ScheduleOfValues.sort_order)
            .limit(50)
        )
        sov_result = await db.execute(sov_stmt)
        sov_items = sov_result.scalars().all()

        if sov_items:
            total_scheduled = sum(s.scheduled_value for s in sov_items)
            lines = [
                f"Schedule of Values: {len(sov_items)} items,"
                f" Total Scheduled Value: ${total_scheduled:,.2f}"
            ]
            for sov in sov_items[:15]:
                lines.append(
                    f"  {sov.item_number}: {sov.description}"
                    f" | ${sov.scheduled_value:,.2f}"
                    f" | CSI: {sov.csi_code or 'N/A'}"
                )
            if len(sov_items) > 15:
                lines.append(f"  ... and {len(sov_items) - 15} more items")

            chunks.append(
                ContextChunk(
                    source="Schedule of Values",
                    content="\n".join(lines),
                    metadata={"sov_item_count": len(sov_items)},
                    relevance_score=0.85,
                )
            )

        return chunks

    async def _gather_document_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        question: str,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Search project documents using hybrid RAG retrieval (vector + BM25)
        with cross-encoder reranking. Falls back to keyword-only on embed
        failure so non-trivial queries always have some retrieval signal.
        """
        chunks: list[ContextChunk] = []

        try:
            from app.services.rag.embeddings import embed_query
            from app.services.rag.reranker import rerank
            from app.services.rag.retrieval import bm25_search, hybrid_search

            results: list[dict]
            try:
                query_emb = await embed_query(question)
                results = await hybrid_search(db, question, query_emb, project_id, limit=20)
            except Exception as embed_exc:
                logger.warning(
                    "embed_query failed; falling back to bm25",
                    exc_info=embed_exc,
                )
                results = await bm25_search(db, question, project_id, limit=20)

            if results:
                try:
                    results = await rerank(question, results, top_n=10)
                except Exception:
                    logger.warning("rerank failed; passing through fused results")
                    results = results[:10]

            for doc_result in results:
                content = doc_result.get("content", "")
                if not content:
                    continue
                chunks.append(
                    ContextChunk(
                        source=doc_result.get("document_title", "Document"),
                        content=content[:1000],
                        metadata={
                            "document_id": doc_result.get("document_id"),
                            "page_number": doc_result.get("page_number"),
                            "section": doc_result.get("section_hierarchy"),
                            "csi_section": doc_result.get("csi_section"),
                            "rerank_score": doc_result.get("rerank_score"),
                        },
                        relevance_score=float(
                            doc_result.get("rerank_score") or doc_result.get("score", 0.5)
                        ),
                    )
                )
        except Exception:
            logger.warning("Document search failed", exc_info=True)

        return chunks

    async def _gather_weather_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        question: str,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Get project location and fetch weather forecast + impact."""
        from app.models.project import Project

        chunks: list[ContextChunk] = []

        # Get project address for location
        project = await db.get(Project, project_id)
        if not project or not project.address:
            chunks.append(
                ContextChunk(
                    source="Weather",
                    content="No project address configured. Set the project address to enable weather forecasts.",
                    metadata={},
                    relevance_score=0.5,
                )
            )
            return chunks

        try:
            from datetime import timedelta as _td

            from app.services.scheduling.weather_service import get_weather_impact

            _today = date.today()
            impact = await get_weather_impact(
                project.address, start_date=_today, end_date=_today + _td(days=7)
            )
            if impact:
                lines = [f"Weather forecast for: {project.address}"]
                # get_weather_impact returns a list of WeatherImpact objects
                if isinstance(impact, list):
                    for wi in impact:
                        lines.append(
                            f"  {wi.activity}: Allowed={wi.allowed}"
                            f" | Risk: {wi.risk_level.value}"
                            f" | Reasons: {', '.join(wi.reasons)}"
                        )
                else:
                    lines.append(str(impact))

                chunks.append(
                    ContextChunk(
                        source="Weather Forecast",
                        content="\n".join(lines),
                        metadata={"location": project.address},
                        relevance_score=0.90,
                    )
                )
        except Exception:
            logger.warning(
                "Weather data fetch failed for project %s",
                project_id,
                exc_info=True,
            )
            chunks.append(
                ContextChunk(
                    source="Weather",
                    content="Weather data is temporarily unavailable.",
                    metadata={},
                    relevance_score=0.3,
                )
            )

        return chunks

    async def _gather_aggregation_data(
        self,
        project_id: uuid.UUID,
        entities: dict,
        question: str,
        db: AsyncSession,
    ) -> list[ContextChunk]:
        """Handle aggregation queries: counts, sums, totals.

        SV-03: Uses SQL-level GROUP BY and func.count() for aggregation
        instead of fetching rows and counting in Python.
        """
        from app.models.change_order_lifecycle import PotentialChangeOrder
        from app.models.communication import RFI
        from app.models.evm import ChangeOrder
        from app.models.quality import DefectReport, Inspection
        from app.models.safety_incident import SafetyAlert
        from app.models.scheduling import ScheduleActivity

        chunks: list[ContextChunk] = []
        q_lower = question.lower()
        lines: list[str] = []

        # RFI counts — single GROUP BY query instead of 4 separate queries
        if "rfi" in q_lower:
            rfi_agg_stmt = (
                select(
                    RFI.status,
                    func.count(RFI.id).label("cnt"),
                )
                .where(RFI.project_id == project_id)
                .group_by(RFI.status)
            )
            rfi_rows = (await db.execute(rfi_agg_stmt)).all()
            status_counts: dict[str, int] = {row.status: row.cnt for row in rfi_rows}
            total = sum(status_counts.values())
            open_count = status_counts.get("open", 0)
            closed_count = status_counts.get("closed", 0)

            # Overdue needs a separate filtered count (date predicate)
            today = date.today()
            overdue_stmt = select(func.count(RFI.id)).where(
                RFI.project_id == project_id,
                RFI.status == "open",
                RFI.due_date < today,
            )
            overdue_count = (await db.execute(overdue_stmt)).scalar() or 0

            lines.append(
                f"RFI Counts: Total={total}, Open={open_count},"
                f" Closed={closed_count}, Overdue={overdue_count}"
            )

        # Change order totals — combined aggregate queries
        if "change order" in q_lower or "co " in q_lower or "pco" in q_lower:
            co_agg_stmt = select(
                func.count(ChangeOrder.id).label("cnt"),
                func.coalesce(func.sum(ChangeOrder.cost_impact), 0).label("total_impact"),
            ).where(ChangeOrder.project_id == project_id)
            co_row = (await db.execute(co_agg_stmt)).one()
            co_count = co_row.cnt
            co_sum = co_row.total_impact or Decimal(0)

            pco_agg_stmt = select(
                func.count(PotentialChangeOrder.id).label("cnt"),
                func.coalesce(func.sum(PotentialChangeOrder.total_cost), 0).label("total_cost"),
            ).where(PotentialChangeOrder.project_id == project_id)
            pco_row = (await db.execute(pco_agg_stmt)).one()
            pco_count = pco_row.cnt
            pco_sum = pco_row.total_cost or Decimal(0)

            lines.append(
                f"Change Orders: {co_count} COs (${co_sum:,.2f} total impact),"
                f" {pco_count} PCOs (${pco_sum:,.2f} total)"
            )

        # Safety alert counts — single GROUP BY on is_acknowledged
        if "safety" in q_lower or "alert" in q_lower or "incident" in q_lower:
            safety_agg_stmt = (
                select(
                    SafetyAlert.is_acknowledged,
                    func.count(SafetyAlert.id).label("cnt"),
                )
                .where(SafetyAlert.project_id == project_id)
                .group_by(SafetyAlert.is_acknowledged)
            )
            safety_rows = (await db.execute(safety_agg_stmt)).all()
            ack_counts: dict[bool, int] = {}
            for row in safety_rows:
                ack_counts[bool(row.is_acknowledged)] = row.cnt
            alert_count = sum(ack_counts.values())
            unack_count = ack_counts.get(False, 0)

            lines.append(f"Safety Alerts: Total={alert_count}, Unacknowledged={unack_count}")

        # Inspection / defect counts — GROUP BY status for defects
        if "inspection" in q_lower or "quality" in q_lower:
            insp_count_stmt = select(func.count(Inspection.id)).where(
                Inspection.project_id == project_id
            )
            insp_count = (await db.execute(insp_count_stmt)).scalar() or 0

            defect_agg_stmt = (
                select(
                    DefectReport.status,
                    func.count(DefectReport.id).label("cnt"),
                )
                .where(DefectReport.project_id == project_id)
                .group_by(DefectReport.status)
            )
            defect_rows = (await db.execute(defect_agg_stmt)).all()
            defect_status: dict[str, int] = {row.status: row.cnt for row in defect_rows}
            defect_count = sum(defect_status.values())
            open_defect_count = defect_status.get("open", 0)

            lines.append(
                f"Quality: Inspections={insp_count},"
                f" Defects={defect_count} (Open: {open_defect_count})"
            )

        # Schedule activity counts — single GROUP BY on status + is_critical
        if "activit" in q_lower or "schedule" in q_lower or "task" in q_lower:
            act_agg_stmt = (
                select(
                    ScheduleActivity.status,
                    ScheduleActivity.is_critical,
                    func.count(ScheduleActivity.id).label("cnt"),
                )
                .where(ScheduleActivity.project_id == project_id)
                .group_by(ScheduleActivity.status, ScheduleActivity.is_critical)
            )
            act_rows = (await db.execute(act_agg_stmt)).all()
            act_count = 0
            critical_count = 0
            completed_count = 0
            for act_row in act_rows:
                act_count += act_row.cnt
                if act_row.is_critical:
                    critical_count += act_row.cnt
                if act_row.status == "completed":
                    completed_count += act_row.cnt

            lines.append(
                f"Schedule Activities: Total={act_count},"
                f" Critical={critical_count},"
                f" Completed={completed_count}"
            )

        if lines:
            chunks.append(
                ContextChunk(
                    source="Aggregation Query",
                    content="\n".join(lines),
                    metadata={"aggregation_type": entities.get("aggregation", "count")},
                    relevance_score=0.99,
                )
            )

        return chunks

    # ------------------------------------------------------------------
    # Response generation
    # ------------------------------------------------------------------

    _RESPONSE_SYSTEM_PROMPT = """\
You are ConstructAI, an expert construction project management assistant.
Answer the user's question based ONLY on the provided context data.

Rules:
1. Answer factually from the provided context. If the data does not contain the answer, say so clearly.
2. Cite your sources using the format [Source: <name>, p. <page>] or [Source: <name>] when referencing data.
3. If you are uncertain about something, indicate your level of certainty.
4. Suggest 2-3 follow-up questions the user might find useful.
5. Use construction industry terminology where appropriate.
6. Format numbers with commas for readability (e.g., $1,234,567.89).
7. For schedule data, mention float and critical path status when relevant.
8. For cost data, mention variances and trends when available.
9. Be concise but thorough. Prefer bullet points for lists.
10. End your response with a "Follow-up Questions:" section listing 2-3 related questions.

Context data from the project:
<retrieved_document>
{context}
</retrieved_document>
"""

    async def _generate_response(
        self,
        question: str,
        context_chunks: list[ContextChunk],
        classification: IntentClassification,
        org_id: str,
        conversation_context: str = "",
    ) -> AskResult:
        """Build LLM prompt from context and generate the final answer."""

        # Build context block
        if context_chunks:
            context_parts = []
            for i, chunk in enumerate(context_chunks, 1):
                header = f"[Source {i}: {chunk.source}]"
                if chunk.metadata.get("page_number"):
                    header += f" (p. {chunk.metadata['page_number']})"
                context_parts.append(f"{header}\n{chunk.content}")
            context_text = "\n\n---\n\n".join(context_parts)
        else:
            context_text = (
                "No data found for this query in the project database. "
                "The project may not have the relevant data loaded yet."
            )

        # SV-02: Include conversation history in the system prompt
        if conversation_context:
            context_text = conversation_context + "\n\n---\n\n" + context_text

        system_prompt = self._RESPONSE_SYSTEM_PROMPT.replace("{context}", context_text)

        try:
            llm = await self._get_llm()
            response = await llm.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                agent_name=_AGENT_NAME,
                org_id=org_id,
                temperature=0.2,
                max_tokens=2048,
            )
            answer_text = response.get("content", "").strip()
        except Exception:
            logger.error("Response generation failed", exc_info=True)
            answer_text = (
                "I apologize, but I was unable to generate a response at this time. "
                "The AI service may be temporarily unavailable. Please try again shortly."
            )

        # Extract citations from the response
        citations = self._extract_citations(answer_text)

        # Extract follow-up suggestions from the response
        follow_ups = self._parse_follow_ups(answer_text)
        if not follow_ups:
            follow_ups = self._suggest_follow_ups(classification.primary_intent, context_chunks)

        # Determine confidence based on data availability and intent confidence
        data_confidence = min(1.0, len(context_chunks) * 0.25) if context_chunks else 0.1
        confidence = (classification.confidence + data_confidence) / 2

        # Collect unique data sources
        data_sources = list(dict.fromkeys(chunk.source for chunk in context_chunks))

        return AskResult(
            answer=answer_text,
            intent=classification.primary_intent,
            citations=citations,
            confidence=round(confidence, 2),
            data_sources=data_sources,
            follow_up_suggestions=follow_ups[:3],
        )

    # ------------------------------------------------------------------
    # Citation extraction
    # ------------------------------------------------------------------

    _CITATION_RE = re.compile(
        r"\[Source:\s*([^\],]+?)(?:,\s*p\.\s*(\d+))?" r"(?:,\s*(?:section|sec\.?)\s*([^\]]+))?\]",
        re.IGNORECASE,
    )

    def _extract_citations(self, response_text: str) -> list[Citation]:
        """Extract [Source: X, p. Y] citation patterns from LLM response."""
        citations: list[Citation] = []
        seen: set[str] = set()

        for match in self._CITATION_RE.finditer(response_text):
            source = match.group(1).strip()
            page = int(match.group(2)) if match.group(2) else None
            section = match.group(3).strip() if match.group(3) else None

            # Deduplicate by source+page
            key = f"{source}:{page}:{section}"
            if key in seen:
                continue
            seen.add(key)

            # Extract surrounding text as excerpt
            start = max(0, match.start() - 100)
            end = min(len(response_text), match.end() + 100)
            excerpt = response_text[start:end].strip()

            citations.append(
                Citation(
                    source=source,
                    page=page,
                    section=section,
                    excerpt=excerpt,
                )
            )

        return citations

    # ------------------------------------------------------------------
    # Follow-up suggestions
    # ------------------------------------------------------------------

    _FOLLOW_UP_RE = re.compile(
        r"(?:Follow-up Questions?|Related Questions?|You (?:might|may) also ask):\s*\n"
        r"((?:\s*[-*\d.]+\s*.+\n?)+)",
        re.IGNORECASE,
    )

    def _parse_follow_ups(self, response_text: str) -> list[str]:
        """Parse follow-up questions from the LLM response text."""
        match = self._FOLLOW_UP_RE.search(response_text)
        if not match:
            return []

        block = match.group(1)
        questions: list[str] = []
        for line in block.strip().split("\n"):
            cleaned = re.sub(r"^\s*[-*\d.]+\s*", "", line).strip()
            if cleaned and len(cleaned) > 10:
                questions.append(cleaned)

        return questions[:3]

    def _suggest_follow_ups(
        self,
        intent: str,
        context_chunks: list[ContextChunk],
    ) -> list[str]:
        """Generate follow-up suggestions based on intent and available data."""
        suggestions_by_intent: dict[str, list[str]] = {
            "schedule": [
                "What activities are on the critical path?",
                "Which activities have negative float?",
                "What is the projected completion date?",
            ],
            "cost": [
                "What are the top 5 cost items by value?",
                "How does the estimate compare to the budget?",
                "What is the Monte Carlo P80 estimate?",
            ],
            "safety": [
                "What are the unacknowledged safety alerts?",
                "What is today's safety risk score?",
                "What are the most common alert types?",
            ],
            "rfi": [
                "How many RFIs are currently overdue?",
                "What is the average RFI response time?",
                "Which RFIs have cost or schedule impact?",
            ],
            "quality": [
                "What open defects need attention?",
                "What is the average inspection score?",
                "Are there any critical severity defects?",
            ],
            "evm": [
                "Is the project on schedule and budget?",
                "What is the Estimate at Completion?",
                "How has the CPI trended over the last 5 periods?",
            ],
            "change_order": [
                "What is the total value of pending change orders?",
                "Which change orders have schedule impact?",
                "How many PCOs are awaiting review?",
            ],
            "pay_application": [
                "What is the current retainage balance?",
                "What is the total billed to date?",
                "Which SOV items have the most remaining balance?",
            ],
            "document": [
                "What specifications are relevant to this scope?",
                "Are there any drawing cross-references?",
                "What submittals are pending review?",
            ],
            "weather": [
                "Can we pour concrete tomorrow?",
                "What is the weather impact on crane operations?",
                "What are the heat illness risk conditions?",
            ],
            "general": [
                "What is the overall project status?",
                "What items need immediate attention?",
                "Give me a project health summary.",
            ],
        }

        return suggestions_by_intent.get(
            intent,
            suggestions_by_intent["general"],
        )


# ---------------------------------------------------------------------------
# Project suggestions helper
# ---------------------------------------------------------------------------


async def get_project_suggestions(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> list[str]:
    """Return starter questions based on what data exists for a project."""
    from app.models.communication import RFI
    from app.models.evm import EVMSnapshot
    from app.models.quality import Inspection
    from app.models.scheduling import ScheduleActivity

    suggestions: list[str] = []

    # Check for schedule data
    sched_count = (
        await db.execute(
            select(func.count(ScheduleActivity.id)).where(ScheduleActivity.project_id == project_id)
        )
    ).scalar() or 0
    if sched_count > 0:
        suggestions.append("What activities are on the critical path?")
        suggestions.append(f"Give me a schedule summary ({sched_count} activities loaded)")

    # Check for EVM data
    evm_count = (
        await db.execute(
            select(func.count(EVMSnapshot.id)).where(EVMSnapshot.project_id == project_id)
        )
    ).scalar() or 0
    if evm_count > 0:
        suggestions.append("What is the current SPI and CPI?")
        suggestions.append("Is the project on budget and on schedule?")

    # Check for RFIs
    rfi_count = (
        await db.execute(select(func.count(RFI.id)).where(RFI.project_id == project_id))
    ).scalar() or 0
    if rfi_count > 0:
        suggestions.append(f"How many RFIs are open? ({rfi_count} total)")

    # Check for inspections
    insp_count = (
        await db.execute(
            select(func.count(Inspection.id)).where(Inspection.project_id == project_id)
        )
    ).scalar() or 0
    if insp_count > 0:
        suggestions.append("What is the latest inspection status?")

    if not suggestions:
        suggestions = [
            "What data is available for this project?",
            "Tell me about the project status.",
            "What documents have been uploaded?",
        ]

    return suggestions[:6]
