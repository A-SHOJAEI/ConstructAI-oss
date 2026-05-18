async def test_register_user_success(client, test_org):
    """Register endpoint returns a generic 200 to prevent enumeration; the
    actual account is created in the background and a verification email is
    sent."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "SecurePassword123!",
            "full_name": "New User",
            "org_id": str(test_org.id),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "verification email" in data["detail"].lower()


async def test_register_duplicate_email(client, test_org, test_user):
    """Duplicate emails get the same generic 200 — no 409 — to avoid leaking
    whether an email is already registered."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": test_user.email,
            "password": "SecurePassword123!",
            "full_name": "Duplicate User",
            "org_id": str(test_org.id),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "verification email" in data["detail"].lower()


async def test_login_success(client, test_org):
    # First register
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "loginuser@example.com",
            "password": "SecurePassword123!",
            "full_name": "Login User",
            "org_id": str(test_org.id),
        },
    )
    # Then login
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "loginuser@example.com", "password": "SecurePassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password(client, test_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user.email, "password": "wrongpassword"},
    )
    assert response.status_code == 401


async def test_login_nonexistent_user(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "somepassword123"},
    )
    assert response.status_code == 401


async def test_refresh_token(client, test_org):
    """Login sets an httpOnly refresh cookie (XSS hardening); body tokens
    are empty by design. The /refresh endpoint reads the cookie, which
    httpx automatically forwards on the same client instance."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "refreshuser@example.com",
            "password": "SecurePassword123!",
            "full_name": "Refresh User",
            "org_id": str(test_org.id),
        },
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "refreshuser@example.com", "password": "SecurePassword123!"},
    )
    assert login_response.status_code == 200
    # The refresh_token cookie was set by login; httpx persists it.
    assert "refresh_token" in client.cookies, "login must set refresh_token cookie"

    # Send the refresh request with an empty body — the endpoint falls back
    # to the httpOnly cookie that httpx is now sending automatically.
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": ""})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data  # field present even if body value is ""


async def test_access_me_with_valid_token(client, auth_headers):
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "email" in data
    assert "full_name" in data


async def test_access_me_without_token(client):
    response = await client.get("/api/v1/auth/me")
    # /api/v1/auth/* is exempt from TenantContextMiddleware, so the request
    # reaches the route's Depends(get_current_user), which raises 401 when
    # no Bearer header or access_token cookie is present.
    assert response.status_code == 401
