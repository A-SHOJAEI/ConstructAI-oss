"""
Create 3 completed workflow executions for history:

1. new_project_onboarding - showing all steps passed
2. change_order_processing - for CO-001 showing fan-out/fan-in
3. safety_incident_response - for P1 crane zone alert showing notification cascade
"""
from datetime import datetime, timedelta, timezone

from app.database import async_session
from app.models import WorkflowExecution

NOW = datetime.now(timezone.utc)


async def seed_workflows(ctx: dict) -> dict:
    project_id = ctx["project_id"]

    async with async_session() as db:
        # 1. New Project Onboarding
        onboarding = WorkflowExecution(
            workflow_type="new_project_onboarding",
            project_id=project_id,
            status="completed",
            current_step="completed",
            steps_completed=[
                {
                    "step": "document_ingestion",
                    "agent": "document_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=60, hours=2)),
                    "completed_at": str(NOW - timedelta(days=60, hours=1, minutes=45)),
                    "output": {"documents_processed": 5, "chunks_created": 50},
                },
                {
                    "step": "cost_estimation",
                    "agent": "estimating_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=60, hours=1, minutes=45)),
                    "completed_at": str(NOW - timedelta(days=60, hours=1, minutes=20)),
                    "output": {"estimate_total": 45000000, "line_items": 50, "confidence": 0.82},
                },
                {
                    "step": "schedule_analysis",
                    "agent": "scheduling_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=60, hours=1, minutes=20)),
                    "completed_at": str(NOW - timedelta(days=60, hours=1)),
                    "output": {"activities_created": 50, "critical_path_length": 420, "dcma_score": 72},
                },
                {
                    "step": "site_layout_optimization",
                    "agent": "logistics_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=60, hours=1)),
                    "completed_at": str(NOW - timedelta(days=60, minutes=40)),
                    "output": {"layouts_generated": 3, "best_score": 0.87},
                },
                {
                    "step": "project_controls_setup",
                    "agent": "controls_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=60, minutes=40)),
                    "completed_at": str(NOW - timedelta(days=60, minutes=20)),
                    "output": {"evm_baseline_set": True, "risk_simulation_run": True},
                },
            ],
            input_data={
                "project_name": "Riverside Mixed-Use Development",
                "document_ids": ctx.get("document_ids", []),
            },
            output_data={
                "status": "success",
                "summary": "Project onboarding completed. 5 documents processed, cost estimate generated, schedule created with 50 activities, site layout optimized, and EVM baseline established.",
            },
            started_at=NOW - timedelta(days=60, hours=2),
            completed_at=NOW - timedelta(days=60, minutes=20),
        )
        db.add(onboarding)

        # 2. Change Order Processing (CO-001)
        co_processing = WorkflowExecution(
            workflow_type="change_order_processing",
            project_id=project_id,
            status="completed",
            current_step="completed",
            steps_completed=[
                {
                    "step": "cost_impact_analysis",
                    "agent": "estimating_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=30, hours=1)),
                    "completed_at": str(NOW - timedelta(days=30, minutes=45)),
                    "output": {"cost_impact": 350000, "breakdown_items": 6},
                },
                {
                    "step": "schedule_impact_analysis",
                    "agent": "scheduling_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=30, hours=1)),
                    "completed_at": str(NOW - timedelta(days=30, minutes=40)),
                    "output": {"schedule_impact_days": 12, "critical_path_affected": True},
                },
                {
                    "step": "risk_assessment",
                    "agent": "controls_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=30, hours=1)),
                    "completed_at": str(NOW - timedelta(days=30, minutes=35)),
                    "output": {"risk_score": 3.2, "key_risks": ["weather", "coordination"]},
                },
                {
                    "step": "consolidation",
                    "agent": "orchestrator",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=30, minutes=35)),
                    "completed_at": str(NOW - timedelta(days=30, minutes=30)),
                    "output": {
                        "recommendation": "Approve with weather contingency plan",
                        "total_impact": {"cost": 350000, "schedule_days": 12, "risk": 3.2},
                    },
                },
                {
                    "step": "human_approval",
                    "agent": "human_in_the_loop",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=30, minutes=30)),
                    "completed_at": str(NOW - timedelta(days=15)),
                    "output": {"approved_by": "David Riverside", "decision": "approved"},
                },
            ],
            input_data={
                "change_order": "CO-001",
                "title": "Add Rooftop Terrace - Owner Request",
                "requested_cost": 350000,
            },
            output_data={
                "status": "approved",
                "final_cost": 350000,
                "schedule_impact": 12,
                "approved_by": "David Riverside",
            },
            started_at=NOW - timedelta(days=30, hours=1),
            completed_at=NOW - timedelta(days=15),
        )
        db.add(co_processing)

        # 3. Safety Incident Response
        safety_response = WorkflowExecution(
            workflow_type="safety_incident_response",
            project_id=project_id,
            status="completed",
            current_step="completed",
            steps_completed=[
                {
                    "step": "alert_detection",
                    "agent": "safety_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=10, hours=3)),
                    "completed_at": str(NOW - timedelta(days=10, hours=3) + timedelta(seconds=2)),
                    "output": {
                        "alert_type": "zone_breach",
                        "priority": "P1",
                        "camera": "Crane Zone Camera",
                        "confidence": 0.94,
                    },
                },
                {
                    "step": "immediate_notification",
                    "agent": "communication_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=10, hours=3) + timedelta(seconds=2)),
                    "completed_at": str(NOW - timedelta(days=10, hours=3) + timedelta(seconds=5)),
                    "output": {
                        "notifications_sent": [
                            {"channel": "sms", "recipient": "James Okafor", "status": "delivered"},
                            {"channel": "push", "recipient": "Mike Rodriguez", "status": "delivered"},
                            {"channel": "email", "recipient": "Sarah Chen", "status": "delivered"},
                        ],
                    },
                },
                {
                    "step": "crane_operations_halt",
                    "agent": "safety_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=10, hours=3) + timedelta(seconds=5)),
                    "completed_at": str(NOW - timedelta(days=10, hours=3) + timedelta(seconds=8)),
                    "output": {"action": "crane_halt_signal_sent", "mqtt_topic": "site/crane/halt"},
                },
                {
                    "step": "incident_documentation",
                    "agent": "communication_agent",
                    "status": "completed",
                    "started_at": str(NOW - timedelta(days=10, hours=2, minutes=50)),
                    "completed_at": str(NOW - timedelta(days=10, hours=2, minutes=40)),
                    "output": {
                        "incident_report_generated": True,
                        "osha_recordable": False,
                        "corrective_actions": [
                            "Additional signage at crane zone boundary",
                            "Mandatory crane zone orientation for all workers",
                        ],
                    },
                },
            ],
            input_data={
                "alert_type": "zone_breach",
                "camera": "Crane Zone Camera",
                "priority": "P1",
            },
            output_data={
                "status": "resolved",
                "response_time_seconds": 8,
                "notifications_sent": 3,
                "crane_halted": True,
                "incident_documented": True,
            },
            started_at=NOW - timedelta(days=10, hours=3),
            completed_at=NOW - timedelta(days=10, hours=2, minutes=40),
        )
        db.add(safety_response)

        await db.commit()

    return {}
