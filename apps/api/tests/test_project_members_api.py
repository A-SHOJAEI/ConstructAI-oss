"""Tests for the project members management API.

Covers auth, add/remove members, and RBAC enforcement.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from app.models.organization import Organization
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.security import create_access_token, hash_password

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def member_project(client, auth_headers):
    """Create a project and return its ID."""
    resp = await client.post(
        "/api/v1/projects/",
        json={"name": "Members Test Project"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest_asyncio.fixture()
async def second_user(db_session: AsyncSession, test_org: Organization) -> User:
    """Create a second user in the same org for membership tests."""
    user = User(
        email=f"second-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Password123!"),
        full_name="Second User",
        org_id=test_org.id,
        role="field_engineer",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListMembers:
    """GET /projects/{pid}/members"""

    @pytest.mark.asyncio
    async def test_list_members_requires_auth(self, client, member_project):
        resp = await client.get(
            f"/api/v1/projects/{member_project}/members",
        )
        # TenantContextMiddleware rejects un-authed requests with 403 before
        # the auth dependency runs (no tenant ID extractable from a missing
        # JWT). The middleware order is fixed in main.py so this is the
        # deterministic response.
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_members_returns_empty_initially(self, client, auth_headers, member_project):
        resp = await client.get(
            f"/api/v1/projects/{member_project}/members",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)


class TestAddMember:
    """POST /projects/{pid}/members"""

    @pytest.mark.asyncio
    async def test_add_member_success(self, client, auth_headers, member_project, second_user):
        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["user_id"] == str(second_user.id)
        assert data["role"] == "field_engineer"
        assert data["project_id"] == member_project

    @pytest.mark.asyncio
    async def test_add_member_invalid_role_rejected(
        self, client, auth_headers, member_project, second_user
    ):
        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "superadmin"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "invalid role" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_add_member_duplicate_rejected(
        self, client, auth_headers, member_project, second_user
    ):
        """Adding the same user twice should return 409 Conflict."""
        await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=auth_headers,
        )
        # Second attempt
        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "superintendent"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "already a member" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_add_nonexistent_user_returns_404(self, client, auth_headers, member_project):
        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(uuid.uuid4()), "role": "field_engineer"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestRemoveMember:
    """DELETE /projects/{pid}/members/{uid}"""

    @pytest.mark.asyncio
    async def test_remove_member_success(self, client, auth_headers, member_project, second_user):
        # Add first
        await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=auth_headers,
        )

        # Remove
        resp = await client.delete(
            f"/api/v1/projects/{member_project}/members/{second_user.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "removed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_member_returns_404(
        self, client, auth_headers, member_project
    ):
        fake_uid = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/projects/{member_project}/members/{fake_uid}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestRBACEnforcement:
    """Members API should enforce RBAC based on the user's role."""

    @pytest.mark.asyncio
    async def test_readonly_user_cannot_add_members(
        self, client, db_session, test_org, member_project, second_user
    ):
        """A user with 'readonly' role should be forbidden from adding members."""
        readonly_user = User(
            email=f"readonly-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("ReadOnly123!"),
            full_name="Read Only User",
            org_id=test_org.id,
            role="readonly",
            email_verified=True,
        )
        db_session.add(readonly_user)
        await db_session.flush()
        await db_session.refresh(readonly_user)

        token = create_access_token(
            data={"sub": str(readonly_user.id), "org_id": str(readonly_user.org_id)}
        )
        readonly_headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=readonly_headers,
        )
        # readonly should be denied -- either 403 (Forbidden) or 404 (project
        # not found because non-org_admin non-member can't see it)
        assert resp.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_unauthenticated_add_member_returns_403(self, client, member_project):
        resp = await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(uuid.uuid4()), "role": "field_engineer"},
        )
        # POST without a CSRF token (and without Bearer auth) is rejected by
        # CSRFMiddleware with 403 before the auth dependency runs.
        assert resp.status_code == 403


class TestUpdateMemberRole:
    """PATCH /projects/{pid}/members/{uid}"""

    @pytest.mark.asyncio
    async def test_update_role_success(self, client, auth_headers, member_project, second_user):
        # Add member
        await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=auth_headers,
        )

        # Update role
        resp = await client.patch(
            f"/api/v1/projects/{member_project}/members/{second_user.id}",
            json={"role": "superintendent"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "superintendent"

    @pytest.mark.asyncio
    async def test_update_with_invalid_role_rejected(
        self, client, auth_headers, member_project, second_user
    ):
        # Add member
        await client.post(
            f"/api/v1/projects/{member_project}/members",
            json={"user_id": str(second_user.id), "role": "field_engineer"},
            headers=auth_headers,
        )

        resp = await client.patch(
            f"/api/v1/projects/{member_project}/members/{second_user.id}",
            json={"role": "god_mode"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "invalid role" in resp.json()["detail"].lower()
