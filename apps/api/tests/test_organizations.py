import uuid

import pytest_asyncio

from app.models.user import User
from app.utils.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def admin_user(db_session, test_org):
    """Create a platform_admin user for org endpoint tests."""
    user = User(
        email=f"orgadmin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("TestPassword123!"),
        full_name="Org Admin",
        org_id=test_org.id,
        role="platform_admin",
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_headers(admin_user):
    """Auth headers for a platform_admin user."""
    token = create_access_token(data={"sub": str(admin_user.id), "org_id": str(admin_user.org_id)})
    return {"Authorization": f"Bearer {token}"}


async def test_create_organization_requires_auth(client):
    """Un-authed POST is rejected by CSRFMiddleware with 403 before the
    auth dependency runs."""
    response = await client.post(
        "/api/v1/organizations/",
        json={"name": "New Org", "slug": f"new-org-{uuid.uuid4().hex[:8]}", "type": "gc"},
    )
    assert response.status_code == 403


async def test_create_organization_requires_admin(client, auth_headers):
    """Non-admin users should be denied (403)."""
    response = await client.post(
        "/api/v1/organizations/",
        json={"name": "New Org", "slug": f"new-org-{uuid.uuid4().hex[:8]}", "type": "gc"},
        headers=auth_headers,
    )
    assert response.status_code == 403


async def test_create_organization(client, admin_headers):
    response = await client.post(
        "/api/v1/organizations/",
        json={"name": "New Org", "slug": f"new-org-{uuid.uuid4().hex[:8]}", "type": "gc"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Org"
    assert data["type"] == "gc"
    assert "id" in data


async def test_get_organization(client, auth_headers, test_org):
    response = await client.get(
        f"/api/v1/organizations/{test_org.id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == test_org.name
    assert data["slug"] == test_org.slug


async def test_list_organizations(client, auth_headers, test_org):
    """Non-admin user sees only their own org."""
    response = await client.get(
        "/api/v1/organizations/",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert "meta" in data
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 1  # Only user's own org
    assert isinstance(data["meta"]["has_more"], bool)


async def test_list_organizations_pagination(client, admin_headers):
    """Platform admin can list all orgs with pagination."""
    # Create 3 organizations
    for i in range(3):
        await client.post(
            "/api/v1/organizations/",
            json={
                "name": f"Pagination Org {i}",
                "slug": f"page-org-{i}-{uuid.uuid4().hex[:8]}",
                "type": "gc",
            },
            headers=admin_headers,
        )

    # Get first page with limit=2
    response = await client.get(
        "/api/v1/organizations/?limit=2",
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["meta"]["has_more"] is True
    assert data["meta"]["cursor"] is not None

    # Get second page using cursor
    cursor = data["meta"]["cursor"]
    response = await client.get(
        f"/api/v1/organizations/?cursor={cursor}&limit=2",
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) >= 1
    assert data["meta"]["has_more"] is False
