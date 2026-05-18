"""API tests for evaluation metrics endpoints.

The /api/v1/evaluation/* endpoints are stubs (return 501 Not Implemented)
pending the production telemetry pipeline. We assert the 501 contract for
now and keep the auth tests in place so the 401/403 wiring remains pinned.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestEvaluationAPI:
    # ── List agent metrics ────────────────────────────────────────────

    async def test_list_agents_requires_auth(self, client):
        response = await client.get("/api/v1/evaluation/agents")
        # /api/v1/evaluation/* is not exempt from TenantContext, so an
        # un-authed GET is rejected with 403 before the route runs. POSTs
        # are rejected by CSRFMiddleware (no Bearer, no CSRF token) → 403.
        assert response.status_code == 403

    async def test_list_agents_success(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/agents",
            headers=auth_headers,
        )
        # Endpoint is a stub; revisit once telemetry pipeline is live.
        assert response.status_code == 501

    # ── Agent history ─────────────────────────────────────────────────

    async def test_agent_history_requires_auth(self, client):
        response = await client.get(
            "/api/v1/evaluation/agents/document_agent/history",
        )
        # /api/v1/evaluation/* is not exempt from TenantContext, so an
        # un-authed GET is rejected with 403 before the route runs. POSTs
        # are rejected by CSRFMiddleware (no Bearer, no CSRF token) → 403.
        assert response.status_code == 403

    async def test_agent_history_success(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/agents/document_agent/history",
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_agent_history_with_limit(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/agents/safety_agent/history",
            params={"limit": 7},
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_agent_history_limit_exceeds_max(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/agents/safety_agent/history",
            params={"limit": 999},
            headers=auth_headers,
        )
        # FastAPI returns 422 when Query constraint (le=365) is violated
        # before the route body executes — so 422 wins over 501.
        assert response.status_code == 422

    # ── Trigger evaluation run ────────────────────────────────────────

    async def test_trigger_evaluation_requires_auth(self, client):
        response = await client.post(
            "/api/v1/evaluation/run",
            json={},
        )
        # /api/v1/evaluation/* is not exempt from TenantContext, so an
        # un-authed GET is rejected with 403 before the route runs. POSTs
        # are rejected by CSRFMiddleware (no Bearer, no CSRF token) → 403.
        assert response.status_code == 403

    async def test_trigger_evaluation_success(self, client, auth_headers):
        response = await client.post(
            "/api/v1/evaluation/run",
            json={},
            headers=auth_headers,
        )
        # /run is implemented (returns 202 with a synthetic evaluation_id);
        # the GET endpoints are still 501 stubs.
        assert response.status_code == 202
        data = response.json()
        assert "evaluation_id" in data
        assert data["status"] == "started"
        assert isinstance(data["agents_queued"], list)
        assert data["agents_queued"]

    async def test_trigger_evaluation_specific_agents(self, client, auth_headers):
        agents = ["document_agent", "safety_agent"]
        response = await client.post(
            "/api/v1/evaluation/run",
            json={"agent_names": agents},
            headers=auth_headers,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["agents_queued"] == agents

    # ── LLM usage stats ──────────────────────────────────────────────

    async def test_llm_usage_requires_auth(self, client):
        response = await client.get("/api/v1/evaluation/llm-usage")
        # /api/v1/evaluation/* is not exempt from TenantContext, so an
        # un-authed GET is rejected with 403 before the route runs. POSTs
        # are rejected by CSRFMiddleware (no Bearer, no CSRF token) → 403.
        assert response.status_code == 403

    async def test_llm_usage_success(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/llm-usage",
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_llm_usage_filter_by_agent(self, client, auth_headers):
        response = await client.get(
            "/api/v1/evaluation/llm-usage",
            params={"agent_name": "document_agent"},
            headers=auth_headers,
        )
        assert response.status_code == 501
