"""
Trigger the New Project Onboarding workflow via API.
Demonstrates: Document -> Estimating -> Scheduling -> Logistics -> Controls cascade.
"""
import asyncio
import sys

import httpx

BASE = "http://localhost:8000/api/v1"


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # Login as PM
        print("Logging in as pm@buildright.dev...")
        login = await client.post(f"{BASE}/auth/login", json={
            "email": "pm@buildright.dev",
            "password": "Demo2026!",
        })
        if login.status_code != 200:
            print(f"Login failed: {login.status_code} {login.text}")
            sys.exit(1)

        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        # Get demo project
        projects = await client.get(f"{BASE}/projects/")
        project_list = projects.json()
        if not project_list:
            print("No projects found. Run 'make demo-seed' first.")
            sys.exit(1)

        project = project_list[0] if isinstance(project_list, list) else project_list["items"][0]
        project_id = project["id"]
        print(f"Project: {project['name']} ({project_id})")

        # Trigger onboarding workflow
        print("\nTriggering New Project Onboarding workflow...")
        resp = await client.post(f"{BASE}/orchestrator/workflows", json={
            "workflow_type": "new_project_onboarding",
            "project_id": project_id,
            "input_data": {"trigger": "demo"},
        })

        if resp.status_code in (200, 201):
            workflow = resp.json()
            wf_id = workflow.get("id", "unknown")
            print(f"Workflow started: {wf_id}")
            print(f"Status: {workflow.get('status', 'unknown')}")

            # Poll for completion
            for i in range(30):
                await asyncio.sleep(2)
                status_resp = await client.get(f"{BASE}/orchestrator/workflows/{wf_id}")
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    step = data.get("current_step", "unknown")
                    status = data.get("status", "unknown")
                    print(f"  [{i+1}] Step: {step} | Status: {status}")
                    if status in ("completed", "failed"):
                        break

            print(f"\nFinal status: {data.get('status')}")
        else:
            print(f"Workflow trigger failed: {resp.status_code}")
            print(f"Response: {resp.text}")
            print("\nNote: The orchestrator endpoint may not be configured for demo mode.")
            print("This script demonstrates the API interaction pattern.")


if __name__ == "__main__":
    asyncio.run(main())
