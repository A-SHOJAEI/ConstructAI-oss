"""Integration load tests for ConstructAI platform.

Scenarios:
  1. 10 concurrent Procore project syncs
  2. 20 simultaneous weekly intelligence brief generations
  3. 50 concurrent RFI resolution requests
  4. 100 concurrent safety detection / risk assessment requests

Usage:
  # Run all scenarios (mixed workload)
  locust -f load_tests/load_test_integration.py --headless \
    -u 100 -r 10 --run-time 5m --host http://localhost:8000

  # Run a single scenario
  locust -f load_tests/load_test_integration.py ProcoreSyncUser --headless \
    -u 10 -r 5 --run-time 2m --host http://localhost:8000

  locust -f load_tests/load_test_integration.py IntelligenceBriefUser --headless \
    -u 20 -r 5 --run-time 3m --host http://localhost:8000

  locust -f load_tests/load_test_integration.py RFIResolutionUser --headless \
    -u 50 -r 10 --run-time 5m --host http://localhost:8000

  locust -f load_tests/load_test_integration.py SafetyDetectionUser --headless \
    -u 100 -r 20 --run-time 5m --host http://localhost:8000
"""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, tag, task

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Pre-generated project/org/RFI IDs for deterministic load testing.
# In a real run these would be seeded in the DB beforehand.
DEMO_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEMO_PROJECT_IDS = [f"00000000-0000-0000-0000-{str(i).zfill(12)}" for i in range(1, 21)]
DEMO_RFI_IDS = [str(uuid.uuid4()) for _ in range(50)]

LOAD_TEST_EMAIL = "loadtest@constructai.dev"
LOAD_TEST_PASSWORD = "LoadTest!Secure123"


class _AuthMixin:
    """Login on start and set Bearer header."""

    token: str = ""

    def _login(self):
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": LOAD_TEST_EMAIL, "password": LOAD_TEST_PASSWORD},
            name="/api/v1/auth/login",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("access_token", "")
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})


# ---------------------------------------------------------------------------
# Scenario 1: 10 concurrent Procore project syncs
# ---------------------------------------------------------------------------


class ProcoreSyncUser(_AuthMixin, HttpUser):
    """Simulates 10 users triggering Procore data syncs simultaneously.

    Tests:
      - POST /api/v1/integrations/procore/sync          (full org sync)
      - POST /api/v1/integrations/procore/sync/{pid}     (single project sync)
      - GET  /api/v1/integrations/procore/sync/status    (poll sync status)
      - GET  /api/v1/integrations/procore/status          (connection health)
    """

    wait_time = between(2, 5)
    weight = 1  # relative weight when running mixed

    def on_start(self):
        self._login()
        self._project_id = random.choice(DEMO_PROJECT_IDS)

    @task(3)
    @tag("procore", "sync")
    def trigger_full_sync(self):
        """Trigger a full org-level Procore sync."""
        self.client.post(
            "/api/v1/integrations/procore/sync",
            name="/api/v1/integrations/procore/sync [full]",
        )

    @task(5)
    @tag("procore", "sync")
    def trigger_project_sync(self):
        """Trigger a single-project Procore sync."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.post(
            f"/api/v1/integrations/procore/sync/{pid}",
            name="/api/v1/integrations/procore/sync/{project_id}",
        )

    @task(8)
    @tag("procore", "status")
    def poll_sync_status(self):
        """Poll sync status (the common pattern during a sync)."""
        self.client.get(
            "/api/v1/integrations/procore/sync/status",
            name="/api/v1/integrations/procore/sync/status",
        )

    @task(2)
    @tag("procore", "status")
    def check_connection_status(self):
        """Check Procore connection health."""
        self.client.get(
            "/api/v1/integrations/procore/status",
            name="/api/v1/integrations/procore/status",
        )


# ---------------------------------------------------------------------------
# Scenario 2: 20 simultaneous intelligence brief generations
# ---------------------------------------------------------------------------


class IntelligenceBriefUser(_AuthMixin, HttpUser):
    """Simulates 20 users generating weekly intelligence briefs concurrently.

    Tests:
      - POST /api/v1/projects/{pid}/intelligence-brief          (generate)
      - GET  /api/v1/projects/{pid}/intelligence-brief/latest   (fetch latest)
      - GET  /api/v1/projects/{pid}/intelligence-brief/history  (browse history)
    """

    wait_time = between(3, 8)
    weight = 2

    def on_start(self):
        self._login()
        self._project_id = random.choice(DEMO_PROJECT_IDS)

    @task(3)
    @tag("intelligence", "generate")
    def generate_brief(self):
        """Generate a new weekly intelligence brief (heavy — LLM + PDF)."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.post(
            f"/api/v1/projects/{pid}/intelligence-brief",
            name="/api/v1/projects/{pid}/intelligence-brief [generate]",
            timeout=120,
        )

    @task(5)
    @tag("intelligence", "read")
    def get_latest_brief(self):
        """Fetch the most recent brief for a project."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/intelligence-brief/latest",
            name="/api/v1/projects/{pid}/intelligence-brief/latest",
        )

    @task(2)
    @tag("intelligence", "read")
    def browse_history(self):
        """Browse brief history (paginated)."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/intelligence-brief/history?limit=10&offset=0",
            name="/api/v1/projects/{pid}/intelligence-brief/history",
        )


# ---------------------------------------------------------------------------
# Scenario 3: 50 concurrent RFI resolution requests
# ---------------------------------------------------------------------------


class RFIResolutionUser(_AuthMixin, HttpUser):
    """Simulates 50 concurrent RFI auto-resolution and draft-response calls.

    Tests:
      - POST /api/v1/projects/{pid}/rfis                       (create RFI)
      - POST /api/v1/projects/{pid}/rfis/{rid}/auto-resolve     (full pipeline)
      - POST /api/v1/projects/{pid}/rfis/{rid}/draft-response   (draft only)
      - GET  /api/v1/projects/{pid}/rfis/unnecessary            (list flagged)
      - GET  /api/v1/projects/{pid}/rfis/stats                  (aggregates)
      - GET  /api/v1/projects/{pid}/rfis                        (list all)
    """

    wait_time = between(1, 4)
    weight = 5

    _rfi_subjects = [
        "Beam connection detail at Grid C-4",
        "Concrete mix design for elevated slabs",
        "Fire rating requirement for stairwell B",
        "Waterproofing membrane at foundation wall",
        "Structural steel shop drawing approval",
        "MEP coordination at ceiling plenum Level 3",
        "Elevator pit depth clarification",
        "Rebar splice length for Grade 60",
        "Curtain wall anchor detail at parapet",
        "Smoke detector spacing in corridor",
    ]

    _rfi_questions = [
        "Drawing S-201 shows W12x26 but calc sheet specifies W14x30. Which governs?",
        "Spec Section 03 30 00 calls for 5000 PSI but structural notes say 4000 PSI. Please clarify.",
        "Is 2-hour fire rating required for the stairwell enclosure per code analysis?",
        "Should the waterproofing extend 12 inches above grade or 18 inches per detail 5/A-201?",
        "Shop drawings show moment connections but contract docs show shear connections at grid line 7.",
        "Ductwork conflicts with beam at RCP grid D-5. Can we lower duct or notch beam?",
        "Drawing A-105 shows pit depth as 5'-0\" but elevator spec requires 5'-6\". Please clarify.",
        "Detail 3/S-401 shows #8 bars lapped 48 diameters but ACI 318 Table 25.5.2.1 requires 54db.",
        'Anchor detail shows 3/4" diameter but wind load calc requires 7/8" embed. Confirm.',
        "NFPA 72 requires 30-ft spacing but reflected ceiling plan shows 35-ft. Revise?",
    ]

    def on_start(self):
        self._login()
        self._project_id = random.choice(DEMO_PROJECT_IDS)
        self._created_rfi_ids: list[str] = []

    @task(2)
    @tag("rfi", "create")
    def create_rfi(self):
        """Create a new RFI."""
        pid = random.choice(DEMO_PROJECT_IDS)
        idx = random.randrange(len(self._rfi_subjects))
        resp = self.client.post(
            f"/api/v1/projects/{pid}/rfis",
            json={
                "subject": self._rfi_subjects[idx],
                "question": self._rfi_questions[idx],
                "discipline": random.choice(["structural", "architectural", "mep", "civil"]),
                "priority": random.choice(["low", "medium", "high", "urgent"]),
            },
            name="/api/v1/projects/{pid}/rfis [create]",
        )
        if resp.status_code == 201:
            rfi_id = resp.json().get("id")
            if rfi_id:
                self._created_rfi_ids.append(rfi_id)

    @task(5)
    @tag("rfi", "resolve")
    def auto_resolve_rfi(self):
        """Run the full 3-stage RFI resolution pipeline."""
        pid = random.choice(DEMO_PROJECT_IDS)
        rfi_id = (
            random.choice(self._created_rfi_ids)
            if self._created_rfi_ids
            else random.choice(DEMO_RFI_IDS)
        )
        self.client.post(
            f"/api/v1/projects/{pid}/rfis/{rfi_id}/auto-resolve",
            name="/api/v1/projects/{pid}/rfis/{rid}/auto-resolve",
            timeout=60,
        )

    @task(4)
    @tag("rfi", "draft")
    def draft_rfi_response(self):
        """Generate an AI draft response (skip unnecessary check)."""
        pid = random.choice(DEMO_PROJECT_IDS)
        rfi_id = (
            random.choice(self._created_rfi_ids)
            if self._created_rfi_ids
            else random.choice(DEMO_RFI_IDS)
        )
        self.client.post(
            f"/api/v1/projects/{pid}/rfis/{rfi_id}/draft-response",
            name="/api/v1/projects/{pid}/rfis/{rid}/draft-response",
            timeout=60,
        )

    @task(3)
    @tag("rfi", "read")
    def list_unnecessary_rfis(self):
        """List RFIs flagged as unnecessary by the AI agent."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/rfis/unnecessary",
            name="/api/v1/projects/{pid}/rfis/unnecessary",
        )

    @task(2)
    @tag("rfi", "read")
    def get_rfi_stats(self):
        """Get aggregate RFI statistics."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/rfis/stats",
            name="/api/v1/projects/{pid}/rfis/stats",
        )

    @task(4)
    @tag("rfi", "read")
    def list_rfis(self):
        """List all RFIs with status filter."""
        pid = random.choice(DEMO_PROJECT_IDS)
        status_filter = random.choice(["open", "closed", ""])
        url = f"/api/v1/projects/{pid}/rfis"
        if status_filter:
            url += f"?status={status_filter}"
        self.client.get(url, name="/api/v1/projects/{pid}/rfis [list]")


# ---------------------------------------------------------------------------
# Scenario 4: 100 concurrent safety detection requests
# ---------------------------------------------------------------------------


class SafetyDetectionUser(_AuthMixin, HttpUser):
    """Simulates 100 concurrent safety detection / risk assessment users.

    Since image classification is not exposed as a standalone API endpoint,
    this exercises the full safety stack:
      - GET  /api/v1/projects/{pid}/safety/risk-score    (predictive risk)
      - GET  /api/v1/projects/{pid}/safety/briefing      (morning briefing)
      - GET  /api/v1/projects/{pid}/safety/trends         (trend analysis)
      - POST /api/v1/quality/defects                      (defect report + images)
      - GET  /api/v1/safety/alerts                        (query alerts)
      - PATCH /api/v1/safety/alerts/{aid}/acknowledge     (acknowledge alert)
      - GET  /api/v1/safety/stats                         (aggregate stats)
    """

    wait_time = between(0.5, 2)
    weight = 10

    _defect_types = [
        "crack",
        "spalling",
        "corrosion",
        "efflorescence",
        "exposed_rebar",
        "surface_deterioration",
        "biological_growth",
        "no_defect",
    ]

    _alert_types = [
        "ppe_violation",
        "hard_hat_missing",
        "no_vest",
        "zone_intrusion",
        "fall_hazard",
    ]

    def on_start(self):
        self._login()
        self._project_id = random.choice(DEMO_PROJECT_IDS)
        self._alert_ids: list[str] = []

    @task(8)
    @tag("safety", "risk")
    def get_risk_score(self):
        """Get today's predictive safety risk score."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/safety/risk-score",
            name="/api/v1/projects/{pid}/safety/risk-score",
        )

    @task(4)
    @tag("safety", "briefing")
    def get_safety_briefing(self):
        """Get morning safety briefing (LLM-generated)."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/projects/{pid}/safety/briefing",
            name="/api/v1/projects/{pid}/safety/briefing",
        )

    @task(3)
    @tag("safety", "trends")
    def get_risk_trends(self):
        """Get risk score history for trend analysis."""
        pid = random.choice(DEMO_PROJECT_IDS)
        days = random.choice([7, 14, 30])
        self.client.get(
            f"/api/v1/projects/{pid}/safety/trends?days={days}",
            name="/api/v1/projects/{pid}/safety/trends",
        )

    @task(5)
    @tag("safety", "defect")
    def create_defect_report(self):
        """Submit a defect report with image URL (simulates CV pipeline)."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.post(
            "/api/v1/quality/defects",
            json={
                "project_id": pid,
                "defect_type": random.choice(self._defect_types),
                "severity": random.choice(["low", "medium", "high", "critical"]),
                "description": f"Load test defect {uuid.uuid4().hex[:8]}",
                "location": f"Level {random.randint(1, 12)}, Grid {random.choice('ABCDEF')}-{random.randint(1, 10)}",
                "image_urls": [
                    f"https://constructai-test.s3.amazonaws.com/test/defect_{uuid.uuid4().hex[:8]}.jpg"
                ],
            },
            name="/api/v1/quality/defects [create]",
        )

    @task(10)
    @tag("safety", "alerts")
    def query_safety_alerts(self):
        """Query safety alerts — the highest-frequency endpoint."""
        pid = random.choice(DEMO_PROJECT_IDS)
        params = f"project_id={pid}&limit=50"
        alert_type = random.choice([*self._alert_types, None])
        if alert_type:
            params += f"&alert_type={alert_type}"
        resp = self.client.get(
            f"/api/v1/safety/alerts?{params}",
            name="/api/v1/safety/alerts [query]",
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for alert in data[:3]:
                aid = alert.get("id")
                if aid and aid not in self._alert_ids:
                    self._alert_ids.append(aid)
            # Cap stored IDs
            self._alert_ids = self._alert_ids[-100:]

    @task(3)
    @tag("safety", "alerts")
    def acknowledge_alert(self):
        """Acknowledge a safety alert."""
        if not self._alert_ids:
            return
        alert_id = random.choice(self._alert_ids)
        self.client.patch(
            f"/api/v1/safety/alerts/{alert_id}/acknowledge",
            json={
                "is_false_positive": random.random() < 0.15,
                "notes": "Load test acknowledgement",
            },
            name="/api/v1/safety/alerts/{aid}/acknowledge",
        )

    @task(4)
    @tag("safety", "stats")
    def get_safety_stats(self):
        """Get aggregated safety statistics."""
        pid = random.choice(DEMO_PROJECT_IDS)
        self.client.get(
            f"/api/v1/safety/stats?project_id={pid}",
            name="/api/v1/safety/stats",
        )
