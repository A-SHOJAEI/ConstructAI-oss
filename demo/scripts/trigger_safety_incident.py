"""
Trigger a Safety Incident Response by publishing a mock P1 event to Kafka.
Demonstrates: detection -> notification -> crane halt -> documentation cascade.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

BASE = "http://localhost:8000/api/v1"


async def main():
    # Try Kafka first, fall back to API
    try:
        from aiokafka import AIOKafkaProducer

        print("Publishing P1 safety event to Kafka...")
        producer = AIOKafkaProducer(bootstrap_servers="localhost:29092")
        await producer.start()

        event = {
            "event_type": "safety_alert",
            "priority": "P1",
            "alert_type": "zone_breach",
            "camera_id": "crane_zone_camera",
            "zone_id": "crane_exclusion",
            "description": "Worker detected in crane exclusion zone during active lift",
            "confidence": 0.94,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "detections": [
                {"class": "person", "confidence": 0.96, "bbox": [120, 340, 180, 520]},
            ],
        }

        await producer.send_and_wait(
            "safety.alerts",
            json.dumps(event).encode("utf-8"),
        )
        await producer.stop()
        print("Event published to safety.alerts topic")

    except ImportError:
        print("aiokafka not available. Using REST API fallback...")
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            login = await client.post(f"{BASE}/auth/login", json={
                "email": "safety@buildright.dev",
                "password": "Demo2026!",
            })
            if login.status_code != 200:
                print(f"Login failed: {login.status_code}")
                sys.exit(1)

            token = login.json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"

            projects = await client.get(f"{BASE}/projects/")
            project_list = projects.json()
            project = project_list[0] if isinstance(project_list, list) else project_list["items"][0]

            # Post safety alert via API
            print(f"\nCreating P1 safety alert for {project['name']}...")
            resp = await client.post(f"{BASE}/safety/alerts", json={
                "project_id": project["id"],
                "priority": "P1",
                "alert_type": "zone_breach",
                "description": "Worker detected in crane exclusion zone during active lift - DEMO TRIGGER",
                "confidence": 0.94,
                "detections": [
                    {"class": "person", "confidence": 0.96, "bbox": [120, 340, 180, 520]},
                ],
            })

            if resp.status_code in (200, 201):
                alert = resp.json()
                print(f"Alert created: {alert.get('id', 'unknown')}")
                print(f"Priority: {alert.get('priority')}")
                print(f"Type: {alert.get('alert_type')}")
            else:
                print(f"Alert creation response: {resp.status_code}")
                print("Note: This demonstrates the safety incident API pattern.")

    # Trigger workflow via API
    print("\nTriggering Safety Incident Response workflow...")
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        login = await client.post(f"{BASE}/auth/login", json={
            "email": "safety@buildright.dev",
            "password": "Demo2026!",
        })
        if login.status_code == 200:
            token = login.json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"

            projects = await client.get(f"{BASE}/projects/")
            project_list = projects.json()
            project = project_list[0] if isinstance(project_list, list) else project_list["items"][0]

            resp = await client.post(f"{BASE}/orchestrator/workflows", json={
                "workflow_type": "safety_incident_response",
                "project_id": project["id"],
                "input_data": {
                    "alert_type": "zone_breach",
                    "priority": "P1",
                    "camera": "Crane Zone Camera",
                },
            })
            if resp.status_code in (200, 201):
                wf = resp.json()
                print(f"Workflow started: {wf.get('id', 'unknown')}")
            else:
                print("Note: This demonstrates the safety incident workflow pattern.")


if __name__ == "__main__":
    asyncio.run(main())
