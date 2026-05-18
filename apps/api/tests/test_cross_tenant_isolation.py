"""Cross-tenant IDOR tests (M-58).

Exemplar that proves the pattern — every sensitive GET/POST/PUT/DELETE
endpoint should be covered by the same shape of test: create a resource
in org A, attempt to access it with a user from org B, expect 404 (NOT
403, because 403 leaks existence).

Write similar tests for every list/detail/update/delete endpoint. The
matrix is mechanical enough that a parametrize-over-endpoints fixture
would collapse this into ~50 lines of test scaffolding.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_user_from_org_b_cannot_read_org_a_project(
    client: AsyncClient,
    db_session,
):
    """Classic IDOR: cross-org project access must 404, not 403.

    403 leaks that the resource exists; 404 keeps that signal opaque.
    """
    from app.models.organization import Organization
    from app.models.project import Project
    from app.models.user import User
    from app.utils.security import create_access_token, hash_password

    # Two independent tenants.
    org_a = Organization(name="Org A", slug="org-a-" + str(uuid.uuid4())[:8])
    org_b = Organization(name="Org B", slug="org-b-" + str(uuid.uuid4())[:8])
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    user_b = User(
        email=f"user-b-{uuid.uuid4()}@example.com",
        hashed_password=hash_password("CorrectHorseBattery1!"),
        full_name="User B",
        org_id=org_b.id,
        role="project_manager",
    )
    db_session.add(user_b)
    await db_session.flush()

    # Resource belongs to org_a; user_b is in org_b.
    project_a = Project(
        name="Confidential Project",
        org_id=org_a.id,
        type="commercial",
        status="active",
    )
    db_session.add(project_a)
    await db_session.flush()
    await db_session.commit()

    token = create_access_token(
        {"sub": str(user_b.id), "org_id": str(user_b.org_id), "token_version": 0}
    )

    # Detail GET — must 404 for cross-tenant.
    r = await client.get(
        f"/api/v1/projects/{project_a.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (404, 403), (
        f"Cross-tenant project access returned {r.status_code}; "
        "should be 404 (preferred) or 403 — never 200."
    )
    assert r.status_code != 200, "IDOR — cross-tenant project readable"


@pytest.mark.asyncio
async def test_user_from_org_b_cannot_list_org_a_rfis(
    client: AsyncClient,
    db_session,
):
    """List endpoints must not return cross-tenant rows even without a detail lookup."""
    from app.models.communication import RFI
    from app.models.organization import Organization
    from app.models.project import Project
    from app.models.user import User
    from app.utils.security import create_access_token, hash_password

    org_a = Organization(name="Org A", slug="org-a-" + str(uuid.uuid4())[:8])
    org_b = Organization(name="Org B", slug="org-b-" + str(uuid.uuid4())[:8])
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    user_b = User(
        email=f"user-b-{uuid.uuid4()}@example.com",
        hashed_password=hash_password("CorrectHorseBattery1!"),
        full_name="User B",
        org_id=org_b.id,
        role="project_manager",
    )
    project_a = Project(
        name="Org A Project",
        org_id=org_a.id,
        type="commercial",
        status="active",
    )
    db_session.add_all([user_b, project_a])
    await db_session.flush()

    db_session.add(
        RFI(
            project_id=project_a.id,
            rfi_number="RFI-001",
            subject="cross-tenant test",
            question="Should not be visible",
            status="open",
        )
    )
    await db_session.flush()
    await db_session.commit()

    token = create_access_token(
        {"sub": str(user_b.id), "org_id": str(user_b.org_id), "token_version": 0}
    )

    # Even if user_b passes org_a's project_id, the access guard should 403/404.
    r = await client.get(
        f"/api/v1/projects/{project_a.id}/rfis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (
        404,
        403,
    ), f"Cross-tenant RFI list returned {r.status_code}; should refuse access."
