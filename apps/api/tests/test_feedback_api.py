"""API tests for user feedback endpoints.

The /api/v1/feedback/* endpoints are stubs (return 501 Not Implemented).
Auth tests still pin 401/403 today; the "_success" tests assert the 501
contract until the feedback persistence layer ships.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestFeedbackAPI:
    # ── Submit feedback ───────────────────────────────────────────────

    async def test_submit_feedback_requires_auth(self, client):
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "document_agent",
                "rating": 1,
            },
        )
        # Un-authed POST is rejected by CSRFMiddleware (no Bearer, no CSRF
        # token); un-authed GET is rejected by TenantContextMiddleware
        # (/api/v1/feedback is not exempt). Both yield 403 before the
        # auth dependency runs.
        assert response.status_code == 403

    async def test_submit_feedback_success_positive(self, client, auth_headers):
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "document_agent",
                "rating": 1,
                "feedback_text": "Great analysis!",
            },
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_submit_feedback_success_negative(self, client, auth_headers):
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "scheduling_agent",
                "rating": -1,
                "feedback_text": "Missed critical path dependency.",
            },
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_submit_feedback_invalid_rating(self, client, auth_headers):
        """Rating of 0 should be rejected (must be 1 or -1)."""
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "safety_agent",
                "rating": 0,
            },
            headers=auth_headers,
        )
        # Pydantic 422 still wins over the stub's 501 (validation runs first)
        assert response.status_code == 422

    async def test_submit_feedback_invalid_rating_value(self, client, auth_headers):
        """Rating of 5 should be rejected (must be 1 or -1)."""
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "safety_agent",
                "rating": 5,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_submit_feedback_minimal_fields(self, client, auth_headers):
        """Only agent_name and rating are required."""
        response = await client.post(
            "/api/v1/feedback/",
            json={
                "agent_name": "estimating_agent",
                "rating": 1,
            },
            headers=auth_headers,
        )
        assert response.status_code == 501

    # ── List feedback ─────────────────────────────────────────────────

    async def test_list_feedback_requires_auth(self, client):
        response = await client.get("/api/v1/feedback/")
        # Un-authed POST is rejected by CSRFMiddleware (no Bearer, no CSRF
        # token); un-authed GET is rejected by TenantContextMiddleware
        # (/api/v1/feedback is not exempt). Both yield 403 before the
        # auth dependency runs.
        assert response.status_code == 403

    async def test_list_feedback_success(self, client, auth_headers):
        response = await client.get(
            "/api/v1/feedback/",
            headers=auth_headers,
        )
        assert response.status_code == 501

    async def test_list_feedback_filter_by_agent(self, client, auth_headers):
        response = await client.get(
            "/api/v1/feedback/",
            params={"agent_name": "document_agent"},
            headers=auth_headers,
        )
        assert response.status_code == 501

    # ── Feedback summary ──────────────────────────────────────────────

    async def test_feedback_summary_requires_auth(self, client):
        response = await client.get("/api/v1/feedback/summary")
        # Un-authed POST is rejected by CSRFMiddleware (no Bearer, no CSRF
        # token); un-authed GET is rejected by TenantContextMiddleware
        # (/api/v1/feedback is not exempt). Both yield 403 before the
        # auth dependency runs.
        assert response.status_code == 403

    async def test_feedback_summary_success(self, client, auth_headers):
        response = await client.get(
            "/api/v1/feedback/summary",
            headers=auth_headers,
        )
        assert response.status_code == 501
