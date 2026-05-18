"""
Trigger the Change Order Processing workflow for CO-003.
Demonstrates: fan-out to Estimating + Scheduling + Controls agents in parallel.
"""
import asyncio
import sys

import httpx

BASE = "http://localhost:8000/api/v1"


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # Login
        print("Logging in as pm@buildright.dev...")
        login = await client.post(f"{BASE}/auth/login", json={
            "email": "pm@buildright.dev",
            "password": "Demo2026!",
        })
        if login.status_code != 200:
            print(f"Login failed: {login.status_code}")
            sys.exit(1)

        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        # Get project
        projects = await client.get(f"{BASE}/projects/")
        project_list = projects.json()
        project = project_list[0] if isinstance(project_list, list) else project_list["items"][0]
        project_id = project["id"]

        # Get change orders
        print(f"\nFetching change orders for {project['name']}...")
        co_resp = await client.get(f"{BASE}/controls/change-orders", params={"project_id": project_id})
        if co_resp.status_code == 200:
            cos = co_resp.json()
            cos_list = cos if isinstance(cos, list) else cos.get("items", [])
            for co in cos_list:
                status = co.get("status", "unknown")
                print(f"  {co.get('co_number')}: {co.get('title')} [{status}]")

        # Trigger change order processing workflow
        print("\nTriggering Change Order Processing for CO-003...")
        resp = await client.post(f"{BASE}/orchestrator/workflows", json={
            "workflow_type": "change_order_processing",
            "project_id": project_id,
            "input_data": {
                "change_order_number": "CO-003",
                "title": "Electrical Panel Upgrade - Code Change",
                "estimated_cost": 95000,
            },
        })

        if resp.status_code in (200, 201):
            workflow = resp.json()
            wf_id = workflow.get("id", "unknown")
            print(f"Workflow started: {wf_id}")

            for i in range(20):
                await asyncio.sleep(2)
                status_resp = await client.get(f"{BASE}/orchestrator/workflows/{wf_id}")
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    step = data.get("current_step", "unknown")
                    status = data.get("status", "unknown")
                    print(f"  [{i+1}] Step: {step} | Status: {status}")
                    if status in ("completed", "failed"):
                        break

            print(f"\nFinal: {data.get('status')}")
            if data.get("output_data"):
                import json
                print(f"Output: {json.dumps(data['output_data'], indent=2)}")
        else:
            print(f"Workflow trigger response: {resp.status_code}")
            print("Note: This demonstrates the API interaction pattern.")


if __name__ == "__main__":
    asyncio.run(main())
