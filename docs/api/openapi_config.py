"""OpenAPI 3.1 customization for ConstructAI API."""
from __future__ import annotations

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service health checks"},
    {"name": "Authentication", "description": "JWT authentication"},
    {"name": "Users", "description": "User management"},
    {"name": "Organizations", "description": "Organization management"},
    {"name": "Projects", "description": "Project management"},
    {"name": "Documents", "description": "Document AI and RAG search"},
    {"name": "Estimating", "description": "Cost estimating with RSMeans"},
    {"name": "Scheduling", "description": "CPM scheduling and optimization"},
    {"name": "Logistics", "description": "Site logistics and delivery"},
    {"name": "Procurement", "description": "Procurement and price forecasting"},
    {"name": "Safety", "description": "Real-time safety monitoring"},
    {"name": "Cameras", "description": "Camera management"},
    {"name": "Zones", "description": "Safety zone configuration"},
    {"name": "Project Controls", "description": "EVM, change orders, risk"},
    {"name": "Quality", "description": "Inspection, defects, NCRs"},
    {"name": "Productivity", "description": "Crew and equipment tracking"},
    {"name": "Communication", "description": "Reports, RFIs, meetings"},
    {"name": "Teams", "description": "Multi-agent team workflows"},
    {"name": "Orchestrator", "description": "Multi-agent orchestration"},
    {"name": "Evaluation", "description": "Agent performance metrics"},
    {"name": "Portfolio", "description": "Executive dashboards"},
    {"name": "Admin", "description": "Tenant provisioning and feature flags"},
    {"name": "Feedback", "description": "User feedback collection"},
]

OPENAPI_CONFIG = {
    "title": "ConstructAI API",
    "version": "1.0.0",
    "description": (
        "AI-powered construction management platform API. "
        "Provides 11 specialized AI agents for document analysis, "
        "cost estimating, scheduling, safety monitoring, quality "
        "inspection, and project controls."
    ),
    "contact": {
        "name": "ConstructAI Engineering",
        "email": "engineering@constructai.dev",
    },
    "license_info": {
        "name": "Proprietary",
    },
}
