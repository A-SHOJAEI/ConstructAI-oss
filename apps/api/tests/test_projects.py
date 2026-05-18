async def test_create_project(client, auth_headers):
    response = await client.post(
        "/api/v1/projects/",
        json={"name": "Test Project", "project_number": "P-001"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Project"
    assert data["project_number"] == "P-001"
    assert data["status"] == "preconstruction"


async def test_list_projects(client, auth_headers):
    # Create a project first
    await client.post(
        "/api/v1/projects/",
        json={"name": "List Project"},
        headers=auth_headers,
    )

    response = await client.get("/api/v1/projects/", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert "meta" in data
    assert isinstance(data["data"], list)
    assert len(data["data"]) >= 1


async def test_list_projects_pagination(client, auth_headers):
    # Create 3 projects
    for i in range(3):
        await client.post(
            "/api/v1/projects/",
            json={"name": f"Page Project {i}"},
            headers=auth_headers,
        )

    # Get first page
    response = await client.get("/api/v1/projects/?limit=2", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["meta"]["has_more"] is True

    # Follow cursor
    cursor = data["meta"]["cursor"]
    response = await client.get(f"/api/v1/projects/?cursor={cursor}&limit=2", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) >= 1
    assert data["meta"]["has_more"] is False


async def test_get_project(client, auth_headers):
    # Create a project
    create_response = await client.post(
        "/api/v1/projects/",
        json={"name": "Get Project"},
        headers=auth_headers,
    )
    project_id = create_response.json()["id"]

    # Get it
    response = await client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Get Project"


async def test_update_project(client, auth_headers):
    # Create a project
    create_response = await client.post(
        "/api/v1/projects/",
        json={"name": "Update Project"},
        headers=auth_headers,
    )
    project_id = create_response.json()["id"]

    # Update it
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Updated Project Name"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Project Name"


async def test_create_project_unauthorized(client):
    response = await client.post(
        "/api/v1/projects/",
        json={"name": "Unauthorized Project"},
    )
    # Un-authed POST is rejected by CSRFMiddleware with 403 before the
    # auth dependency runs.
    assert response.status_code == 403
