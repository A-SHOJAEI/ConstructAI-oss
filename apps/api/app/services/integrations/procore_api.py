"""Procore REST API wrapper with auto-refresh, rate limiting, and retries.

All API calls go through ProcoreAPI which:
  - Auto-refreshes OAuth tokens before each call if expired
  - Adds the required Procore-Company-Id header
  - Implements sliding-window rate limiting (3600 requests/hour)
  - Retries on 429 (rate limited) with Retry-After header
  - Retries once on 401 (refreshes token and retries)
  - Logs all API calls with timing
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.integrations.procore_oauth import (
    ProcoreOAuthError,
    get_valid_access_token,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiter: 3600 requests/hour per org
# ---------------------------------------------------------------------------

_RATE_LIMIT = 3600
_RATE_WINDOW = 3600.0  # 1 hour in seconds
_rate_timestamps: dict[str, list[float]] = {}
_rate_lock = asyncio.Lock()


async def _check_rate_limit_redis(org_id: str) -> bool | None:
    """Check Procore API rate limit via Redis for cross-process consistency.

    Returns True if under limit, False if at/over limit, or None if Redis
    is unavailable (caller should fall back to in-memory).
    """
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is None:
            return None
        key = f"procore_rate_limit:{org_id}"
        now = time.time()
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - _RATE_WINDOW)  # Remove entries older than 1 hour
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, int(_RATE_WINDOW))
        results = await pipe.execute()
        count = results[1]
        return count < _RATE_LIMIT
    except Exception:
        logger.debug("Redis rate limit check failed; falling back to in-memory", exc_info=True)
        return None


async def _rate_limit_wait(org_id: str) -> None:
    """Block until the org's request rate is under the limit.

    Attempts Redis-backed rate limiting for cross-process consistency.
    Falls back to in-memory when Redis is unavailable.
    """
    # Try Redis first for cross-process rate limiting
    redis_result = await _check_rate_limit_redis(org_id)
    if redis_result is True:
        return  # Under limit per Redis
    if redis_result is False:
        logger.warning(
            "Procore rate limit reached for org %s (Redis); waiting 1s before retry",
            org_id,
        )
        await asyncio.sleep(1.0)
        return

    # Fall back to in-memory rate limiting
    wait_time = 0.0

    # Phase 1: Calculate wait time under the lock, then release
    async with _rate_lock:
        now = time.monotonic()
        timestamps = _rate_timestamps.setdefault(org_id, [])

        # Prune timestamps outside the window
        cutoff = now - _RATE_WINDOW
        _rate_timestamps[org_id] = [t for t in timestamps if t > cutoff]
        timestamps = _rate_timestamps[org_id]

        # Clean up empty org entries to prevent unbounded growth
        if not timestamps:
            del _rate_timestamps[org_id]
            timestamps = []

        if len(timestamps) >= _RATE_LIMIT:
            oldest = timestamps[0]
            wait_time = oldest + _RATE_WINDOW - now

    # Phase 2: Sleep outside the lock
    if wait_time > 0:
        logger.warning(
            "Procore rate limit reached for org %s; waiting %.1fs",
            org_id,
            wait_time,
        )
        await asyncio.sleep(wait_time)

    # Phase 3: Reacquire lock to record the timestamp
    async with _rate_lock:
        _rate_timestamps.setdefault(org_id, []).append(time.monotonic())


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class ProcoreCompany(BaseModel):
    id: int
    name: str
    is_active: bool = True


class ProcoreProject(BaseModel):
    id: int
    name: str
    project_number: str | None = None
    status: str | None = None
    address: str | None = None
    city: str | None = None
    state_code: str | None = None
    start_date: str | None = None
    completion_date: str | None = None


class ProcoreRFI(BaseModel):
    id: int
    number: int | None = None
    subject: str
    status: str | None = None
    priority: str | None = None
    assignee: dict | None = None
    due_date: str | None = None
    created_at: str | None = None


class ProcoreSubmittal(BaseModel):
    id: int
    number: str | None = None
    title: str
    status: str | None = None
    spec_section: str | None = None
    submitted_date: str | None = None


class ProcoreChangeOrder(BaseModel):
    id: int
    number: int | None = None
    title: str
    status: str | None = None
    grand_total: float | None = None
    created_at: str | None = None


class ProcoreBudgetLineItem(BaseModel):
    id: int
    cost_code: str | None = None
    description: str | None = None
    original_budget_amount: float | None = None
    approved_change_orders: float | None = None
    revised_budget: float | None = None


class ProcoreDocument(BaseModel):
    id: int
    name: str
    filename: str | None = None
    description: str | None = None
    document_type: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    download_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ProcoreDailyLog(BaseModel):
    id: int
    log_date: str | None = None
    weather: dict | None = None
    notes: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# ProcoreAPI class
# ---------------------------------------------------------------------------


class ProcoreAPI:
    """Authenticated Procore API client for a single organization.

    Usage::

        api = ProcoreAPI(org_id=org_id, db=db_session)
        companies = await api.list_companies()
        projects = await api.list_projects(company_id=12345)
    """

    def __init__(self, org_id: uuid.UUID, db: AsyncSession) -> None:
        self._org_id = org_id
        self._db = db
        self._access_token: str | None = None
        self._company_id: int | None = None
        self._client: httpx.AsyncClient | None = (
            None if httpx is None else httpx.AsyncClient(timeout=30.0)
        )

    async def close(self) -> None:
        """Close the shared HTTP client. Call when done with this instance."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> ProcoreAPI:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _ensure_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        token, conn = await get_valid_access_token(self._org_id, self._db)
        self._access_token = token
        if conn.procore_company_id:
            self._company_id = int(conn.procore_company_id)
        return token

    def _headers(self, token: str, company_id: int | None = None) -> dict[str, str]:
        """Build request headers."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        cid = company_id or self._company_id
        if cid:
            headers["Procore-Company-Id"] = str(cid)
        return headers

    # Maximum number of retries for 429 rate-limit responses
    _MAX_429_RETRIES = 5

    async def _request(
        self,
        method: str,
        path: str,
        *,
        company_id: int | None = None,
        params: dict | None = None,
        json_body: dict | None = None,
        _retry_on_401: bool = True,
        _retry_count: int = 0,
    ) -> Any:
        """Execute an authenticated Procore API request.

        Handles rate limiting, token refresh on 401, and retry on 429.
        """
        if httpx is None:
            raise ProcoreOAuthError("httpx is required for Procore API calls")

        await _rate_limit_wait(str(self._org_id))
        token = await self._ensure_token()

        api_url = settings.PROCORE_API_URL.rstrip("/")
        url = f"{api_url}{path}"

        start = time.monotonic()

        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.request(
                method,
                url,
                headers=self._headers(token, company_id),
                params=params,
                json=json_body,
            )
        finally:
            # Only close if we created a one-off client (no shared client)
            if self._client is None:
                await client.aclose()

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Procore API %s %s → %d (%.0fms)",
            method,
            path,
            resp.status_code,
            elapsed_ms,
        )

        # Handle 401 — token may have been revoked server-side
        if resp.status_code == 401 and _retry_on_401:
            logger.warning("Procore 401 — attempting token refresh and retry")
            self._access_token = None
            return await self._request(
                method,
                path,
                company_id=company_id,
                params=params,
                json_body=json_body,
                _retry_on_401=False,  # only retry once
                _retry_count=_retry_count,
            )

        # Handle 429 — rate limited by Procore
        if resp.status_code == 429:
            if _retry_count >= self._MAX_429_RETRIES:
                raise ProcoreOAuthError(
                    f"Procore API rate limit exceeded after {self._MAX_429_RETRIES} retries "
                    f"for {method} {path}"
                )
            try:
                retry_after = min(int(resp.headers.get("Retry-After", "60")), 300)
            except (ValueError, TypeError):
                retry_after = 60
            logger.warning(
                "Procore 429 rate limited; retrying after %ds (attempt %d/%d)",
                retry_after,
                _retry_count + 1,
                self._MAX_429_RETRIES,
            )
            await asyncio.sleep(retry_after)
            return await self._request(
                method,
                path,
                company_id=company_id,
                params=params,
                json_body=json_body,
                _retry_on_401=_retry_on_401,
                _retry_count=_retry_count + 1,
            )

        resp.raise_for_status()
        return resp.json()

    async def _request_v1_1(
        self,
        method: str,
        path: str,
        *,
        company_id: int | None = None,
        params: dict | None = None,
        json_body: dict | None = None,
        _retry_on_401: bool = True,
        _retry_count: int = 0,
    ) -> Any:
        """Execute a Procore API v1.1 request.

        Identical to _request() but uses /rest/v1.1 base path instead of
        the configured PROCORE_API_URL (which defaults to v1.0).
        """
        if httpx is None:
            raise ProcoreOAuthError("httpx is required for Procore API calls")

        await _rate_limit_wait(str(self._org_id))
        token = await self._ensure_token()

        base_url = settings.PROCORE_BASE_URL.rstrip("/")
        url = f"{base_url}/rest/v1.1{path}"

        start = time.monotonic()

        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.request(
                method,
                url,
                headers=self._headers(token, company_id),
                params=params,
                json=json_body,
            )
        finally:
            if self._client is None:
                await client.aclose()

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Procore API v1.1 %s %s → %d (%.0fms)",
            method,
            path,
            resp.status_code,
            elapsed_ms,
        )

        if resp.status_code == 401 and _retry_on_401:
            logger.warning("Procore 401 on v1.1 — attempting token refresh and retry")
            self._access_token = None
            return await self._request_v1_1(
                method,
                path,
                company_id=company_id,
                params=params,
                json_body=json_body,
                _retry_on_401=False,
                _retry_count=_retry_count,
            )

        if resp.status_code == 429:
            if _retry_count >= self._MAX_429_RETRIES:
                raise ProcoreOAuthError(
                    f"Procore API v1.1 rate limit exceeded after {self._MAX_429_RETRIES} retries "
                    f"for {method} {path}"
                )
            try:
                retry_after = min(int(resp.headers.get("Retry-After", "60")), 300)
            except (ValueError, TypeError):
                retry_after = 60
            logger.warning(
                "Procore 429 rate limited; retrying after %ds (attempt %d/%d)",
                retry_after,
                _retry_count + 1,
                self._MAX_429_RETRIES,
            )
            await asyncio.sleep(retry_after)
            return await self._request_v1_1(
                method,
                path,
                company_id=company_id,
                params=params,
                json_body=json_body,
                _retry_on_401=_retry_on_401,
                _retry_count=_retry_count + 1,
            )

        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    async def list_companies(self) -> list[ProcoreCompany]:
        """List Procore companies accessible to the authenticated user."""
        data = await self._request("GET", "/companies")
        return [ProcoreCompany(**c) for c in data]

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def list_projects(self, company_id: int) -> list[ProcoreProject]:
        """List projects for a Procore company."""
        data = await self._request(
            "GET",
            "/projects",
            company_id=company_id,
        )
        return [ProcoreProject(**p) for p in data]

    async def get_project(self, project_id: int, company_id: int) -> ProcoreProject:
        """Get a single project by ID."""
        data = await self._request(
            "GET",
            f"/projects/{project_id}",
            company_id=company_id,
        )
        return ProcoreProject(**data)

    # ------------------------------------------------------------------
    # RFIs
    # ------------------------------------------------------------------

    async def list_rfis(self, project_id: int, company_id: int) -> list[ProcoreRFI]:
        """List RFIs for a project."""
        data = await self._request(
            "GET",
            f"/projects/{project_id}/rfis",
            company_id=company_id,
        )
        return [ProcoreRFI(**r) for r in data]

    # ------------------------------------------------------------------
    # Submittals
    # ------------------------------------------------------------------

    async def list_submittals(self, project_id: int, company_id: int) -> list[ProcoreSubmittal]:
        """List submittals for a project."""
        data = await self._request(
            "GET",
            f"/projects/{project_id}/submittals",
            company_id=company_id,
        )
        return [ProcoreSubmittal(**s) for s in data]

    # ------------------------------------------------------------------
    # Change Orders
    # ------------------------------------------------------------------

    async def list_change_orders(
        self, project_id: int, company_id: int
    ) -> list[ProcoreChangeOrder]:
        """List prime change orders for a project."""
        data = await self._request(
            "GET",
            f"/projects/{project_id}/prime_contract/change_orders",
            company_id=company_id,
        )
        return [ProcoreChangeOrder(**co) for co in data]

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------

    async def get_budget(
        self,
        project_id: int,
        company_id: int,
        budget_view_id: int | None = None,
    ) -> list[ProcoreBudgetLineItem]:
        """Get budget line items for a project.

        Procore requires a budget_view_id. If not provided, we fetch
        the list of budget views and use the first one.
        """
        if budget_view_id is None:
            views = await self._request(
                "GET",
                f"/projects/{project_id}/budget/views",
                company_id=company_id,
            )
            if not views:
                return []
            budget_view_id = views[0]["id"]

        data = await self._request(
            "GET",
            f"/projects/{project_id}/budget/views/{budget_view_id}/detail_rows",
            company_id=company_id,
        )
        return [ProcoreBudgetLineItem(**row) for row in data]

    # ------------------------------------------------------------------
    # Cost data sync helper
    # ------------------------------------------------------------------

    async def sync_cost_data(
        self,
        project_id: int,
        company_id: int,
    ) -> dict:
        """Sync cost data from Procore for a project.

        Fetches budget, change orders, and RFIs in parallel and returns
        a summary dict.
        """
        budget_task = self.get_budget(project_id, company_id)
        co_task = self.list_change_orders(project_id, company_id)
        rfi_task = self.list_rfis(project_id, company_id)

        budget, change_orders, rfis = await asyncio.gather(
            budget_task,
            co_task,
            rfi_task,
            return_exceptions=True,
        )

        budget_count = len(budget) if isinstance(budget, list) else 0
        co_count = len(change_orders) if isinstance(change_orders, list) else 0
        rfi_count = len(rfis) if isinstance(rfis, list) else 0

        # Update last_sync_at on the connection
        from sqlalchemy import select

        from app.models.procore_connection import ProcoreConnection

        result = await self._db.execute(
            select(ProcoreConnection).where(ProcoreConnection.organization_id == self._org_id)
        )
        conn = result.scalar_one_or_none()
        if conn:
            conn.last_sync_at = datetime.now(UTC)
            # SECURITY: Only mark "synced" if all entity fetches succeeded;
            # if any returned exceptions, mark as "partial_sync".
            has_errors = (
                isinstance(budget, BaseException)
                or isinstance(change_orders, BaseException)
                or isinstance(rfis, BaseException)
            )
            conn.sync_status = "partial_sync" if has_errors else "synced"
            await self._db.flush()

        return {
            "project_id": project_id,
            "company_id": company_id,
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "status": "completed",
            "items_synced": {
                "budget_line_items": budget_count,
                "change_orders": co_count,
                "rfis": rfi_count,
            },
        }

    # ------------------------------------------------------------------
    # v1.1 Projects
    # ------------------------------------------------------------------

    async def list_projects_v1_1(self, company_id: int) -> list[ProcoreProject]:
        """List projects using v1.1 API (includes total_value)."""
        data = await self._request_v1_1(
            "GET",
            f"/companies/{company_id}/projects",
            company_id=company_id,
        )
        return [ProcoreProject(**p) for p in data]

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        project_id: int,
        company_id: int,
    ) -> list[ProcoreDocument]:
        """List documents for a project."""
        data = await self._request_v1_1(
            "GET",
            f"/projects/{project_id}/documents",
            company_id=company_id,
        )
        return [ProcoreDocument(**d) for d in data]

    async def download_document(
        self,
        project_id: int,
        document_id: int,
        company_id: int,
    ) -> tuple[bytes, str]:
        """Download a document file. Returns (file_bytes, content_type)."""
        if httpx is None:
            raise ProcoreOAuthError("httpx is required for Procore API calls")

        await _rate_limit_wait(str(self._org_id))
        token = await self._ensure_token()
        base_url = settings.PROCORE_BASE_URL.rstrip("/")
        url = f"{base_url}/rest/v1.1/projects/{project_id}/documents/{document_id}/download"

        # Downloads may need longer timeout and follow redirects;
        # use a dedicated client for this since the shared client has 30s timeout.
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as dl_client:
            resp = await dl_client.get(
                url,
                headers=self._headers(token, company_id),
            )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.content, content_type

    # ------------------------------------------------------------------
    # Daily Logs
    # ------------------------------------------------------------------

    async def list_daily_logs(
        self,
        project_id: int,
        company_id: int,
    ) -> list[ProcoreDailyLog]:
        """List daily log entries for a project."""
        data = await self._request_v1_1(
            "GET",
            f"/projects/{project_id}/daily_logs",
            company_id=company_id,
        )
        return [ProcoreDailyLog(**dl) for dl in data]

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def register_webhook(
        self,
        company_id: int,
        destination_url: str,
        resource_name: str,
    ) -> dict:
        """Register a webhook hook for a specific resource type.

        Uses POST /rest/v1.1/companies/{company_id}/webhooks/hooks
        per Procore's Webhooks API.
        """
        data = await self._request_v1_1(
            "POST",
            f"/companies/{company_id}/webhooks/hooks",
            company_id=company_id,
            json_body={
                "hook": {
                    "api_version": "v2",
                    "destination_url": destination_url,
                    "namespace": resource_name,
                },
            },
        )
        return data

    async def list_webhooks(self, company_id: int) -> list[dict]:
        """List registered webhook hooks for a company."""
        data = await self._request_v1_1(
            "GET",
            f"/companies/{company_id}/webhooks/hooks",
            company_id=company_id,
        )
        return data

    async def delete_webhook(self, company_id: int, hook_id: int) -> None:
        """Delete a webhook hook by ID."""
        await self._request_v1_1(
            "DELETE",
            f"/companies/{company_id}/webhooks/hooks/{hook_id}",
            company_id=company_id,
        )
