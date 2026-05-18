import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const API_BASE = "http://localhost:8000";

/**
 * Build a structurally-valid JWT whose `exp` is in the future so the
 * Next.js middleware accepts it (it checks format + expiry, not signature).
 */
function makeFakeJwt(extraMinutes = 30): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const payload = btoa(
    JSON.stringify({
      sub: "test-user-id",
      exp: Math.floor(Date.now() / 1000) + extraMinutes * 60,
    }),
  )
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const sig = btoa("fake-signature")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `${header}.${payload}.${sig}`;
}

const TEST_USER = {
  id: "user-1",
  email: "test@test.com",
  full_name: "Test User",
  role: "project_manager",
  org_id: "org-1",
};

const TEST_PROJECTS = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      name: "Highway Bridge Project",
      status: "active",
      project_number: "PRJ-2026-001",
      contract_value: 15000000,
      start_date: "2026-01-15",
      end_date: "2027-06-30",
    },
    {
      id: "00000000-0000-0000-0000-000000000002",
      name: "Office Tower",
      status: "active",
      project_number: "PRJ-2026-002",
      contract_value: 45000000,
      start_date: "2026-03-01",
      end_date: "2028-12-31",
    },
    {
      id: "00000000-0000-0000-0000-000000000003",
      name: "School Renovation",
      status: "planning",
      project_number: "PRJ-2026-003",
      contract_value: 3200000,
      start_date: null,
      end_date: null,
    },
  ],
  total: 3,
};

// ---------------------------------------------------------------------------
// Common setup: authenticate and set up core API mocks
// ---------------------------------------------------------------------------

test.describe("Project Workflow", () => {
  test.beforeEach(async ({ page }) => {
    // Set auth cookie so middleware allows access
    await page.context().addCookies([
      { name: "access_token", value: makeFakeJwt(), domain: "localhost", path: "/" },
    ]);

    // Mock /auth/me
    await page.route(`${API_BASE}/api/v1/auth/me`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEST_USER),
      });
    });

    // Mock /auth/refresh (may be called by AuthProvider)
    await page.route(`${API_BASE}/api/v1/auth/refresh`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ message: "refreshed" }),
      });
    });

    // Mock projects list (GET)
    await page.route(`${API_BASE}/api/v1/projects/`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(TEST_PROJECTS),
        });
      } else {
        // POST for creating projects
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: "00000000-0000-0000-0000-000000000099",
            name: "New Test Project",
            status: "planning",
          }),
        });
      }
    });

    // Mock notification preferences (settings page loads this)
    await page.route(`${API_BASE}/api/v1/users/me/notification-preferences`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          email_notifications: true,
          safety_alerts: true,
          schedule_changes: false,
          daily_digest: false,
        }),
      });
    });
  });

  // -------------------------------------------------------------------------
  // 1. Project listing
  // -------------------------------------------------------------------------
  test("can view projects list", async ({ page }) => {
    await page.goto("/projects");

    // Wait for projects to load and render
    await expect(page.getByText("Highway Bridge Project")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Office Tower")).toBeVisible();
    await expect(page.getByText("School Renovation")).toBeVisible();

    // Verify the page heading
    await expect(page.getByRole("heading", { level: 1, name: /projects/i })).toBeVisible();

    // Verify the New Project button is present
    await expect(page.getByText(/New Project/i)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // 2. Project selection
  // -------------------------------------------------------------------------
  test("can select a project and navigate to dashboard", async ({ page }) => {
    // Mock the dashboard route that the project selection navigates to
    await page.route(`${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0, data: [] }),
      });
    });

    await page.goto("/projects");

    // Wait for projects to render
    await expect(page.getByText("Highway Bridge Project")).toBeVisible({ timeout: 10000 });

    // Click on a project to select it
    await page.getByText("Highway Bridge Project").click();

    // The project store should be updated — verify via localStorage
    const storedData = await page.evaluate(() => {
      const raw = localStorage.getItem("constructai-project");
      return raw ? JSON.parse(raw) : null;
    });
    expect(storedData).toBeTruthy();
    expect(storedData.state.selectedProjectId).toBe("00000000-0000-0000-0000-000000000001");
  });

  // -------------------------------------------------------------------------
  // 3. Navigation between dashboard pages with project context
  // -------------------------------------------------------------------------
  test("navigation between dashboard pages retains project context", async ({ page }) => {
    // Set project in localStorage before navigating (simulating a previously selected project)
    await page.goto("/projects");

    await page.evaluate(() => {
      const state = {
        state: {
          selectedProjectId: "00000000-0000-0000-0000-000000000001",
          selectedProject: {
            id: "00000000-0000-0000-0000-000000000001",
            name: "Highway Bridge Project",
            status: "active",
          },
        },
        version: 0,
      };
      localStorage.setItem("constructai-project", JSON.stringify(state));
    });

    // Mock document-related API
    await page.route(`${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/documents/*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });

    // Mock schedule API
    await page.route(`${API_BASE}/api/v1/scheduling/**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });

    // Navigate to documents
    await page.goto("/documents");
    await expect(page.getByRole("heading", { level: 1, name: /documents/i })).toBeVisible({ timeout: 10000 });

    // Navigate to schedule
    await page.goto("/schedule");
    await expect(page.getByRole("heading", { level: 1, name: /schedule/i })).toBeVisible({ timeout: 10000 });

    // Verify project is still in localStorage
    const storedData = await page.evaluate(() => {
      const raw = localStorage.getItem("constructai-project");
      return raw ? JSON.parse(raw) : null;
    });
    expect(storedData.state.selectedProjectId).toBe("00000000-0000-0000-0000-000000000001");
  });

  // -------------------------------------------------------------------------
  // 4. Document search (mock the API)
  // -------------------------------------------------------------------------
  test("documents page loads with project context and shows upload area", async ({ page }) => {
    // Set project in store
    await page.goto("/projects");
    await page.evaluate(() => {
      const state = {
        state: {
          selectedProjectId: "00000000-0000-0000-0000-000000000001",
          selectedProject: {
            id: "00000000-0000-0000-0000-000000000001",
            name: "Highway Bridge Project",
            status: "active",
          },
        },
        version: 0,
      };
      localStorage.setItem("constructai-project", JSON.stringify(state));
    });

    // Mock documents list with some documents
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/documents/`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [
              {
                id: "doc-1",
                name: "Structural_Plans_Rev2.pdf",
                type: "pdf",
                status: "complete",
                discipline: "Structural",
                created_at: "2026-03-15T10:00:00Z",
              },
              {
                id: "doc-2",
                name: "MEP_Specs.pdf",
                type: "pdf",
                status: "processing",
                discipline: "Mechanical",
                created_at: "2026-03-20T14:00:00Z",
              },
            ],
            total: 2,
          }),
        });
      },
    );

    await page.goto("/documents");

    // Verify heading
    await expect(page.getByRole("heading", { level: 1, name: /documents/i })).toBeVisible({ timeout: 10000 });

    // Verify documents are displayed
    await expect(page.getByText("Structural_Plans_Rev2.pdf")).toBeVisible();
    await expect(page.getByText("MEP_Specs.pdf")).toBeVisible();

    // Verify upload area text
    await expect(page.getByText(/drag and drop/i)).toBeVisible();

    // Verify filter buttons
    await expect(page.getByRole("button", { name: "All" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Processing" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Complete" })).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // 5. Safety alerts page loads
  // -------------------------------------------------------------------------
  test("safety page loads and shows monitoring heading", async ({ page }) => {
    // Set project in store
    await page.goto("/projects");
    await page.evaluate(() => {
      const state = {
        state: {
          selectedProjectId: "00000000-0000-0000-0000-000000000001",
          selectedProject: {
            id: "00000000-0000-0000-0000-000000000001",
            name: "Highway Bridge Project",
            status: "active",
          },
        },
        version: 0,
      };
      localStorage.setItem("constructai-project", JSON.stringify(state));
    });

    // Mock safety stats
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/safety/stats`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            total: 12,
            by_priority: { P1_critical: 2, P2_high: 5, P3_medium: 3, P4_low: 2 },
            acknowledged_count: 8,
            false_positive_count: 1,
          }),
        });
      },
    );

    // Mock cameras
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/safety/cameras*`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        });
      },
    );

    // Mock safety alerts
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/safety/alerts*`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: [
              {
                id: "alert-1",
                type: "no_hard_hat",
                priority: "P1_critical",
                status: "new",
                created_at: "2026-03-25T14:00:00Z",
                message: "Worker without hard hat detected",
              },
            ],
            meta: { cursor: null, has_more: false },
          }),
        });
      },
    );

    // Mock the WebSocket connection (will fail but page should still load)
    // Mock predictive risk API
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/safety/predictive-risk*`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ risk_score: 0.35, risk_level: "low", categories: [] }),
        });
      },
    );

    await page.goto("/safety");

    // Verify the main heading
    await expect(page.getByRole("heading", { level: 1, name: /safety monitoring/i })).toBeVisible({
      timeout: 10000,
    });

    // Verify sub-section headings
    await expect(page.getByText(/camera feeds/i)).toBeVisible();
    await expect(page.getByText(/alert timeline/i)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // 6. RFIs page loads with project context
  // -------------------------------------------------------------------------
  test("rfis page loads and shows RFI heading", async ({ page }) => {
    // Set project in store
    await page.goto("/projects");
    await page.evaluate(() => {
      const state = {
        state: {
          selectedProjectId: "00000000-0000-0000-0000-000000000001",
          selectedProject: {
            id: "00000000-0000-0000-0000-000000000001",
            name: "Highway Bridge Project",
            status: "active",
          },
        },
        version: 0,
      };
      localStorage.setItem("constructai-project", JSON.stringify(state));
    });

    // Mock RFI stats
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/rfis/stats`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            total: 15,
            open: 5,
            pending_review: 3,
            answered: 4,
            closed: 3,
            overdue: 2,
            avg_response_days: 4.5,
            unnecessary_count: 1,
          }),
        });
      },
    );

    // Mock RFI list
    await page.route(
      `${API_BASE}/api/v1/projects/00000000-0000-0000-0000-000000000001/rfis?*`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: [
              {
                id: "rfi-1",
                project_id: "00000000-0000-0000-0000-000000000001",
                rfi_number: "RFI-001",
                subject: "Foundation rebar spacing clarification",
                question: "What is the required spacing?",
                status: "open",
                priority: "high",
                assigned_to: null,
                ball_in_court: null,
                due_date: "2026-04-01",
                is_overdue: false,
                days_open: 5,
                ai_status: null,
                created_at: "2026-03-20T10:00:00Z",
              },
            ],
            meta: { cursor: null, has_more: false },
          }),
        });
      },
    );

    await page.goto("/rfis");

    // Verify heading
    await expect(page.getByRole("heading", { level: 1, name: /rfis/i })).toBeVisible({
      timeout: 10000,
    });

    // Verify RFI table data is rendered
    await expect(page.getByText("RFI-001")).toBeVisible();
    await expect(page.getByText("Foundation rebar spacing clarification")).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // 7. Settings page loads and shows profile
  // -------------------------------------------------------------------------
  test("settings page loads and shows user profile", async ({ page }) => {
    await page.goto("/settings");

    // Verify heading
    await expect(page.getByRole("heading", { level: 1, name: /settings/i })).toBeVisible({
      timeout: 10000,
    });

    // Verify profile section
    await expect(page.getByText("Profile")).toBeVisible();
    await expect(page.getByText("Test User")).toBeVisible();
    await expect(page.getByText("test@test.com")).toBeVisible();

    // Verify notifications section
    await expect(page.getByText("Notifications")).toBeVisible();
    await expect(page.getByText("Email Notifications")).toBeVisible();
    await expect(page.getByText("Safety Alerts")).toBeVisible();
  });
});
