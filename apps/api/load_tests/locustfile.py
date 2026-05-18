"""Locust load test: 100 concurrent users, mixed workload."""

from __future__ import annotations

import uuid

from locust import HttpUser, between, task


class ConstructAIUser(HttpUser):
    """Simulated ConstructAI platform user."""

    wait_time = between(1, 5)
    token: str = ""
    project_id: str = str(uuid.uuid4())

    def on_start(self):
        """Login and get auth token."""
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "loadtest@constructai.dev",
                "password": "loadtest123",
            },
        )
        if response.status_code == 200:
            self.token = response.json().get(
                "access_token",
                "",
            )
        self.client.headers.update(
            {"Authorization": f"Bearer {self.token}"},
        )

    @task(3)
    def view_project_dashboard(self):
        """Most common: view project dashboard."""
        self.client.get(
            f"/api/v1/projects/{self.project_id}",
        )

    @task(2)
    def query_documents(self):
        """Search documents via RAG."""
        self.client.post(
            "/api/v1/documents/search",
            json={
                "query": "concrete specifications",
                "project_id": self.project_id,
            },
        )

    @task(1)
    def view_evm(self):
        """View EVM snapshot."""
        self.client.get(
            f"/api/v1/controls/evm?project_id={self.project_id}",
        )

    @task(1)
    def view_safety_alerts(self):
        """View recent safety alerts."""
        self.client.get(
            f"/api/v1/safety/alerts?project_id={self.project_id}",
        )

    @task(1)
    def view_portfolio(self):
        """View executive portfolio."""
        self.client.get("/api/v1/portfolio")

    @task(1)
    def submit_feedback(self):
        """Submit agent feedback."""
        self.client.post(
            "/api/v1/feedback",
            json={
                "agent_name": "document_agent",
                "rating": 1,
                "feedback_text": "Helpful response",
            },
        )
