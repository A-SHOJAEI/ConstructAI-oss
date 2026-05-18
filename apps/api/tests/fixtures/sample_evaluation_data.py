"""Benchmark test datasets for evaluation framework."""

from __future__ import annotations

SAMPLE_RAG_QUERIES = [
    {
        "question": ("What is the required concrete strength for the foundation?"),
        "expected_answer": (
            "The foundation requires 4000 PSI concrete per specification section 03 30 00."
        ),
        "context": (
            "Section 03 30 00 - Cast-in-Place Concrete. "
            "Foundation concrete shall achieve minimum "
            "compressive strength of 4000 PSI at 28 days. "
            "Use Type I/II Portland cement."
        ),
        "actual_answer": (
            "The foundation requires 4000 PSI concrete "
            "strength at 28 days as specified in "
            "section 03 30 00."
        ),
    },
    {
        "question": "What fall protection is required?",
        "expected_answer": ("Fall protection is required at 6 feet per OSHA 1926.501."),
        "context": (
            "OSHA 29 CFR 1926.501 requires fall protection "
            "for workers at heights of 6 feet or more in "
            "construction. Guardrails, safety nets, or "
            "personal fall arrest systems are acceptable."
        ),
        "actual_answer": (
            "Fall protection required at 6 feet per "
            "OSHA 1926.501. Acceptable methods include "
            "guardrails, safety nets, or personal "
            "fall arrest systems."
        ),
    },
    {
        "question": "What is the project schedule duration?",
        "expected_answer": "The project is 18 months.",
        "context": (
            "Project duration is 18 months from notice "
            "to proceed. Critical path runs through "
            "structural steel erection."
        ),
        "actual_answer": (
            "Project duration is 18 months, with critical path through steel erection."
        ),
    },
]

SAMPLE_AGENT_EVALUATION_RESULTS = [
    {
        "agent_name": "estimating_agent",
        "metric_name": "mape_conceptual",
        "metric_value": 0.12,
        "benchmark_target": 0.15,
    },
    {
        "agent_name": "safety_agent",
        "metric_name": "map_50",
        "metric_value": 0.87,
        "benchmark_target": 0.85,
    },
    {
        "agent_name": "document_agent",
        "metric_name": "precision_at_5",
        "metric_value": 0.83,
        "benchmark_target": 0.80,
    },
]

SAMPLE_DRIFT_REFERENCE = {
    "estimating_accuracy": [
        0.88,
        0.90,
        0.87,
        0.91,
        0.89,
        0.90,
        0.88,
        0.92,
        0.87,
        0.89,
    ],
    "safety_detection_rate": [
        0.85,
        0.87,
        0.86,
        0.84,
        0.88,
        0.85,
        0.87,
        0.86,
        0.84,
        0.88,
    ],
}

SAMPLE_DRIFT_CURRENT = {
    "estimating_accuracy": [
        0.82,
        0.80,
        0.79,
        0.81,
        0.78,
    ],
    "safety_detection_rate": [
        0.85,
        0.86,
        0.84,
        0.87,
        0.85,
    ],
}
