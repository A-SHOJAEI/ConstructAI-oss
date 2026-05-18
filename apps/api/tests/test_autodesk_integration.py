"""Tests for Autodesk Construction Cloud integration endpoints.

Covers HMAC webhook signature verification, stub behaviour, and auth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# HMAC webhook verification
# ---------------------------------------------------------------------------


class TestAutodeskWebhookSignature:
    """POST /integrations/autodesk/webhooks — HMAC-SHA256 signature checks."""

    def _sign(self, body: bytes, secret: str) -> str:
        """Compute the expected HMAC-SHA256 hex digest."""
        return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    @pytest.mark.asyncio
    async def test_valid_hmac_signature_accepted(self, client):
        """A correctly signed payload should be accepted (200)."""
        secret = "test-webhook-secret-1234"
        payload = json.dumps({"hook": {"event": "dm.version.added"}, "payload": {}}).encode()
        sig = self._sign(payload, secret)

        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.AUTODESK_WEBHOOK_SECRET = secret
            mock_settings.ENVIRONMENT = "development"

            resp = await client.post(
                "/api/v1/integrations/autodesk/webhooks",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Autodesk-Signature": sig,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "received"
        assert data["event"] == "dm.version.added"

    @pytest.mark.asyncio
    async def test_invalid_hmac_signature_rejected(self, client):
        """A payload with a wrong signature should be rejected (401)."""
        secret = "correct-secret"
        payload = json.dumps({"hook": {"event": "test"}, "payload": {}}).encode()
        wrong_sig = "deadbeef1234567890"

        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.AUTODESK_WEBHOOK_SECRET = secret
            mock_settings.ENVIRONMENT = "development"

            resp = await client.post(
                "/api/v1/integrations/autodesk/webhooks",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Autodesk-Signature": wrong_sig,
                },
            )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_signature_header_rejected(self, client):
        """When X-Autodesk-Signature is absent, the request gets an empty
        string which should fail HMAC comparison."""
        secret = "some-secret"
        payload = json.dumps({"hook": {"event": "test"}}).encode()

        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.AUTODESK_WEBHOOK_SECRET = secret
            mock_settings.ENVIRONMENT = "development"

            resp = await client.post(
                "/api/v1/integrations/autodesk/webhooks",
                content=payload,
                headers={"Content-Type": "application/json"},
                # NO X-Autodesk-Signature header
            )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_webhook_secret_rejects(self, client):
        """When AUTODESK_WEBHOOK_SECRET is empty, all webhooks are rejected."""
        payload = json.dumps({"hook": {"event": "test"}}).encode()

        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.AUTODESK_WEBHOOK_SECRET = ""
            mock_settings.ENVIRONMENT = "development"

            resp = await client.post(
                "/api/v1/integrations/autodesk/webhooks",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Autodesk-Signature": "anything",
                },
            )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Stub endpoints return 501 in production
# ---------------------------------------------------------------------------


class TestAutodeskStubEndpoints:
    """Stub endpoints should return 501 in production/staging."""

    @pytest.mark.asyncio
    async def test_list_projects_returns_501_in_production(self, client, auth_headers):
        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.ENVIRONMENT = "production"

            resp = await client.get(
                "/api/v1/integrations/autodesk/projects?account_id=hub123",
                headers=auth_headers,
            )

        assert resp.status_code == 501
        assert "not yet implemented" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_sync_returns_501_in_staging(self, client, auth_headers):
        """Create a project first, then try to sync in staging mode."""
        # Create a project
        create_resp = await client.post(
            "/api/v1/projects/",
            json={"name": "Autodesk Sync Test"},
            headers=auth_headers,
        )
        project_id = create_resp.json()["id"]

        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.ENVIRONMENT = "staging"

            resp = await client.post(
                f"/api/v1/integrations/autodesk/sync/{project_id}?autodesk_project_id=adsk-123",
                headers=auth_headers,
            )

        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_list_projects_returns_data_in_development(self, client, auth_headers):
        """In development, the stub endpoint returns an empty project list (not 501)."""
        with patch("app.api.v1.integrations.autodesk.settings") as mock_settings:
            mock_settings.ENVIRONMENT = "development"

            resp = await client.get(
                "/api/v1/integrations/autodesk/projects?account_id=hub123",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
