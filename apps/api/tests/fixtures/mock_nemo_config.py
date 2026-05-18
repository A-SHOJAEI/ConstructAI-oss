"""NeMo Guardrails mock configuration for tests."""

from __future__ import annotations

MOCK_COLANG_CONFIG = {
    "models": [],
    "rails": {
        "input": {
            "flows": [
                {
                    "name": "construction_topic_check",
                    "allowed_topics": [
                        "construction",
                        "project management",
                        "safety",
                    ],
                },
            ],
        },
        "output": {
            "flows": [
                {"name": "no_pii_in_reports"},
                {"name": "no_legal_advice"},
            ],
        },
    },
}

MOCK_TOPIC_CHECK_ALLOWED = {
    "allowed": True,
    "matched_topics": ["construction", "safety"],
}

MOCK_TOPIC_CHECK_BLOCKED = {
    "allowed": False,
    "message": ("Query does not appear to be construction-related"),
}

MOCK_GUARDRAIL_PASS = {
    "agent_name": "document_agent",
    "passed": True,
    "confidence_score": 0.92,
    "routing_decision": "auto_approve",
    "validation_errors": [],
    "latency_ms": 150,
}

MOCK_GUARDRAIL_FAIL = {
    "agent_name": "cost_estimate",
    "passed": False,
    "confidence_score": 0.45,
    "routing_decision": "expert_escalation",
    "validation_errors": [
        {
            "stage": "domain_rules",
            "rule": "rsmeans_range",
            "message": "Cost $2000/cy above RSMeans range",
            "severity": "warning",
        },
    ],
    "latency_ms": 200,
}
