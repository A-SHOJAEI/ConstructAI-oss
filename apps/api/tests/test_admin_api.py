"""API tests for admin and tenant management endpoints."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.models.user import User
from app.utils.security import create_access_token, hash_password


@pytest.mark.asyncio
class TestAdminAPI:
    @pytest_asyncio.fixture
    async def admin_user(self, db_session, test_org):
        """Create a platform_admin user for admin endpoint tests."""
        user = User(
            email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("TestPassword123!"),
            full_name="Platform Admin",
            org_id=test_org.id,
            role="platform_admin",
            email_verified=True,
            mfa_enabled=True,  # admin endpoints require MFA
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def admin_headers(self, admin_user):
        """Auth headers for a platform_admin user."""
        token = create_access_token(
            data={"sub": str(admin_user.id), "org_id": str(admin_user.org_id)}
        )
        return {"Authorization": f"Bearer {token}"}

    # ── Tenant endpoints ──────────────────────────────────────────────

    async def test_create_tenant_requires_auth(self, client):
        response = await client.post(
            "/api/v1/admin/tenants",
            json={
                "org_name": "Acme Corp",
                "admin_email": "admin@acme.com",
            },
        )
        # Un-authed admin requests are rejected by middleware before the
        # auth dependency runs:
        #   - POST → CSRFMiddleware (no Bearer, no CSRF token) → 403
        #   - GET  → TenantContextMiddleware (no JWT, /admin not exempt) → 403
        assert response.status_code == 403

    @pytest_asyncio.fixture
    async def nonadmin_user(self, db_session, test_org):
        """Create a non-admin user for permission denial tests."""
        user = User(
            email=f"nonadmin-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("TestPassword123!"),
            full_name="Non Admin",
            org_id=test_org.id,
            role="field_engineer",
            email_verified=True,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)
        return user

    @pytest_asyncio.fixture
    async def nonadmin_headers(self, nonadmin_user):
        token = create_access_token(
            data={"sub": str(nonadmin_user.id), "org_id": str(nonadmin_user.org_id)}
        )
        return {"Authorization": f"Bearer {token}"}

    async def test_create_tenant_requires_admin_role(self, client, nonadmin_headers):
        """Non-admin users should be denied (403)."""
        response = await client.post(
            "/api/v1/admin/tenants",
            json={
                "org_name": "Acme Corp",
                "admin_email": "admin@acme.com",
            },
            headers=nonadmin_headers,
        )
        assert response.status_code == 403

    async def test_create_tenant_success(self, client, admin_headers):
        response = await client.post(
            "/api/v1/admin/tenants",
            json={
                "org_name": "Acme Corp",
                "admin_email": "admin@acme.com",
            },
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert "meta" in body
        assert body["meta"]["stub"] is True
        data = body["data"]
        assert "id" in data
        assert "org_id" in data
        assert data["billing_plan"] == "startup"
        assert "created_at" in data
        assert response.headers.get("X-ConstructAI-Stub") == "true"

    async def test_create_tenant_custom_billing_plan(self, client, admin_headers):
        response = await client.post(
            "/api/v1/admin/tenants",
            json={
                "org_name": "Big Builder Inc",
                "billing_plan": "enterprise",
                "admin_email": "admin@bigbuilder.com",
            },
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        data = body["data"]
        assert data["billing_plan"] == "enterprise"

    async def test_create_tenant_missing_fields(self, client, admin_headers):
        response = await client.post(
            "/api/v1/admin/tenants",
            json={},
            headers=admin_headers,
        )
        assert response.status_code == 422

    async def test_list_tenants_requires_auth(self, client):
        response = await client.get("/api/v1/admin/tenants")
        # Un-authed admin requests are rejected by middleware before the
        # auth dependency runs:
        #   - POST → CSRFMiddleware (no Bearer, no CSRF token) → 403
        #   - GET  → TenantContextMiddleware (no JWT, /admin not exempt) → 403
        assert response.status_code == 403

    async def test_list_tenants_success(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/tenants",
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body
        assert body["meta"]["stub"] is True
        assert "items" in body["data"]
        assert isinstance(body["data"]["items"], list)
        assert response.headers.get("X-ConstructAI-Stub") == "true"

    # ── Feature flag endpoints ────────────────────────────────────────

    async def test_create_feature_flag_requires_auth(self, client):
        response = await client.post(
            "/api/v1/admin/feature-flags",
            json={"name": "new_dashboard"},
        )
        # Un-authed admin requests are rejected by middleware before the
        # auth dependency runs:
        #   - POST → CSRFMiddleware (no Bearer, no CSRF token) → 403
        #   - GET  → TenantContextMiddleware (no JWT, /admin not exempt) → 403
        assert response.status_code == 403

    async def test_create_feature_flag_success(self, client, admin_headers):
        response = await client.post(
            "/api/v1/admin/feature-flags",
            json={"name": "new_dashboard"},
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert "meta" in body
        assert body["meta"]["stub"] is True
        data = body["data"]
        assert data["name"] == "new_dashboard"
        assert data["enabled"] is False
        assert data["rollout_percentage"] == 0
        assert data["description"] is None
        assert "id" in data
        assert "created_at" in data
        assert response.headers.get("X-ConstructAI-Stub") == "true"

    async def test_create_feature_flag_with_rollout(self, client, admin_headers):
        response = await client.post(
            "/api/v1/admin/feature-flags",
            json={
                "name": "beta_reports",
                "description": "Beta reporting feature",
                "enabled": True,
                "rollout_percentage": 50,
            },
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        data = body["data"]
        assert data["name"] == "beta_reports"
        assert data["description"] == "Beta reporting feature"
        assert data["enabled"] is True
        assert data["rollout_percentage"] == 50

    async def test_list_feature_flags_requires_auth(self, client):
        response = await client.get("/api/v1/admin/feature-flags")
        # Un-authed admin requests are rejected by middleware before the
        # auth dependency runs:
        #   - POST → CSRFMiddleware (no Bearer, no CSRF token) → 403
        #   - GET  → TenantContextMiddleware (no JWT, /admin not exempt) → 403
        assert response.status_code == 403

    async def test_list_feature_flags_success(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/feature-flags",
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body
        assert body["meta"]["stub"] is True
        assert "items" in body["data"]
        assert isinstance(body["data"]["items"], list)
        assert response.headers.get("X-ConstructAI-Stub") == "true"
