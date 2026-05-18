from fastapi import APIRouter

from app.api.v1 import (
    admin,
    ambient_field,
    ask,
    auth,
    bid_opportunities,
    cameras,
    carbon,
    cash_flow,
    change_orders,
    changeflow,
    closeout,
    communication,
    contract_intelligence,
    controls,
    cross_project,
    daily_logs,
    daily_reports,
    demo_backup,
    digital_twin,
    document_compare,
    documents,
    drawings,
    drone,
    estimating,
    evaluation,
    exports,
    feedback,
    field_management,
    health,
    heat,
    instant_pay,
    insurance,
    intelligence_brief,
    logistics,
    offline_sync,
    orchestrator,
    organizations,
    osha,
    password_reset,
    pay_applications,
    payroll,
    plan_takeoff,
    portfolio,
    predictive_safety,
    procore,
    procore_webhooks,
    procurement,
    productivity,
    progress_tracking,
    project_members,
    project_reports,
    projects,
    public,
    punch_lists,
    quality,
    rfis,
    safety,
    schedule_optimization,
    scheduling,
    sitescribe,
    sso,
    subcontractor_portal,
    submittals,
    sustainability,
    teams,
    translation,
    users,
    voice,
    wages,
    workforce,
    zones,
)
from app.api.v1.integrations import autodesk

api_router = APIRouter()

api_router.include_router(
    health.router,
    tags=["Health"],
)
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"],
)
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["Users"],
)
api_router.include_router(
    organizations.router,
    prefix="/organizations",
    tags=["Organizations"],
)
api_router.include_router(
    projects.router,
    prefix="/projects",
    tags=["Projects"],
)
api_router.include_router(
    documents.router,
    prefix="/documents",
    tags=["Documents"],
)
api_router.include_router(
    estimating.router,
    prefix="/estimating",
    tags=["Estimating"],
)
api_router.include_router(
    scheduling.router,
    prefix="/scheduling",
    tags=["Scheduling"],
)
api_router.include_router(
    schedule_optimization.router,
    prefix="/scheduling",
    tags=["Schedule Optimization"],
)
api_router.include_router(
    logistics.router,
    prefix="/logistics",
    tags=["Logistics"],
)
api_router.include_router(
    procurement.router,
    prefix="/procurement",
    tags=["Procurement"],
)
api_router.include_router(
    cameras.router,
    prefix="/cameras",
    tags=["Cameras"],
)
api_router.include_router(
    zones.router,
    prefix="/zones",
    tags=["Zones"],
)
api_router.include_router(
    safety.router,
    prefix="/safety",
    tags=["Safety"],
)
api_router.include_router(
    controls.router,
    prefix="/controls",
    tags=["Project Controls"],
)
api_router.include_router(
    quality.router,
    prefix="/quality",
    tags=["Quality"],
)
api_router.include_router(
    field_management.router,
    prefix="/field",
    tags=["Field Management"],
)
api_router.include_router(
    productivity.router,
    prefix="/productivity",
    tags=["Productivity"],
)
api_router.include_router(
    communication.router,
    prefix="/communication",
    tags=["Communication"],
)
api_router.include_router(
    teams.router,
    prefix="/teams",
    tags=["Teams"],
)
api_router.include_router(
    orchestrator.router,
    prefix="/orchestrator",
    tags=["Orchestrator"],
)
api_router.include_router(
    evaluation.router,
    prefix="/evaluation",
    tags=["Evaluation"],
)
api_router.include_router(
    portfolio.router,
    prefix="/portfolio",
    tags=["Portfolio"],
)
api_router.include_router(
    admin.router,
    prefix="/admin",
    tags=["Admin"],
)
api_router.include_router(
    feedback.router,
    prefix="/feedback",
    tags=["Feedback"],
)
api_router.include_router(
    password_reset.router,
    prefix="/auth",
    tags=["Authentication"],
)
api_router.include_router(
    rfis.router,
    prefix="/projects",
    tags=["RFIs"],
)
api_router.include_router(
    submittals.router,
    prefix="/projects",
    tags=["Submittals"],
)
api_router.include_router(
    daily_logs.router,
    prefix="/projects",
    tags=["Daily Logs"],
)
api_router.include_router(
    punch_lists.router,
    prefix="/projects",
    tags=["Punch Lists"],
)
api_router.include_router(
    osha.router,
    prefix="/osha",
    tags=["OSHA Enforcement"],
)
api_router.include_router(
    procore.router,
    prefix="/integrations/procore",
    tags=["Procore Integration"],
)
api_router.include_router(
    procore_webhooks.router,
    prefix="/webhooks/procore",
    tags=["Procore Webhooks"],
)
api_router.include_router(
    change_orders.router,
    prefix="/controls",
    tags=["Change Order Lifecycle"],
)
api_router.include_router(
    pay_applications.router,
    prefix="/pay-applications",
    tags=["Pay Applications"],
)
api_router.include_router(
    drawings.router,
    prefix="/projects",
    tags=["Drawings"],
)
api_router.include_router(
    intelligence_brief.router,
    prefix="/projects",
    tags=["Intelligence Brief"],
)
api_router.include_router(
    bid_opportunities.router,
    prefix="/orgs",
    tags=["Bid Intelligence"],
)
api_router.include_router(
    predictive_safety.router,
    prefix="/projects",
    tags=["Predictive Safety"],
)
api_router.include_router(
    project_members.router,
    prefix="/projects",
    tags=["Project Members"],
)
api_router.include_router(
    exports.router,
    prefix="/projects",
    tags=["Exports"],
)
api_router.include_router(
    voice.router,
    tags=["Voice"],
)
api_router.include_router(
    document_compare.router,
    tags=["Document Comparison"],
)
api_router.include_router(
    sso.router,
    prefix="/auth/sso",
    tags=["SSO"],
)
api_router.include_router(
    translation.router,
    prefix="/translation",
    tags=["Translation"],
)
api_router.include_router(
    cash_flow.router,
    prefix="/projects",
    tags=["Cash Flow"],
)
api_router.include_router(
    ask.router,
    prefix="/projects",
    tags=["Ask ConstructAI"],
)
api_router.include_router(
    demo_backup.router,
    prefix="/demo",
    tags=["Demo Backup"],
)
api_router.include_router(
    autodesk.router,
    prefix="/integrations/autodesk",
    tags=["Autodesk Integration"],
)
api_router.include_router(
    progress_tracking.router,
    prefix="/projects",
    tags=["Progress Tracking"],
)
api_router.include_router(
    contract_intelligence.router,
    prefix="/projects",
    tags=["Contract Intelligence"],
)
api_router.include_router(
    daily_reports.router,
    prefix="/projects",
    tags=["Daily Reports"],
)
api_router.include_router(
    sustainability.router,
    prefix="/projects",
    tags=["Sustainability"],
)
api_router.include_router(
    subcontractor_portal.router,
    prefix="/projects",
    tags=["Subcontractor Portal"],
)
api_router.include_router(
    workforce.router,
    prefix="/projects",
    tags=["Workforce Analytics"],
)
api_router.include_router(
    cross_project.router,
    prefix="/orgs",
    tags=["Cross-Project Analytics"],
)
api_router.include_router(
    payroll.router,
    prefix="/projects",
    tags=["Certified Payroll"],
)
api_router.include_router(
    insurance.router,
    prefix="/orgs",
    tags=["Insurance & Risk Export"],
)
api_router.include_router(
    ambient_field.router,
    prefix="/projects",
    tags=["Ambient Field Intelligence"],
)
api_router.include_router(
    plan_takeoff.router,
    # Plan-takeoff routes are NOT scoped by URL path — the project_id arrives
    # in the multipart form body. Mounting under "/projects" caused the
    # router's POST "" path to shadow the project-create endpoint
    # (POST /api/v1/projects), returning 422 for "missing file/project_id".
    prefix="/plan-takeoffs",
    tags=["Plan Takeoff"],
)
api_router.include_router(
    instant_pay.router,
    prefix="/projects",
    tags=["Instant Pay"],
)
api_router.include_router(
    instant_pay.webhook_router,
    prefix="/webhooks/instant-pay",
    tags=["Instant Pay Webhooks"],
)
api_router.include_router(
    offline_sync.router,
    prefix="/projects",
    tags=["Offline Sync"],
)
api_router.include_router(
    digital_twin.router,
    prefix="/projects",
    tags=["Digital Twin"],
)
api_router.include_router(
    drone.router,
    prefix="/projects",
    tags=["Drone/UAV"],
)
api_router.include_router(
    project_reports.router,
    prefix="/projects",
    tags=["Project Reports"],
)
api_router.include_router(
    heat.router,
    prefix="/projects",
    tags=["HeatShield"],
)
api_router.include_router(
    changeflow.router,
    prefix="/projects",
    tags=["ChangeFlow T&M"],
)
api_router.include_router(
    wages.router,
    prefix="/projects",
    tags=["WageGuard"],
)
api_router.include_router(
    closeout.router,
    prefix="/projects",
    tags=["CloseoutIQ"],
)
api_router.include_router(
    sitescribe.router,
    prefix="/projects",
    tags=["SiteScribe"],
)
api_router.include_router(
    carbon.router,
    prefix="/projects",
    tags=["CarbonLens"],
)
api_router.include_router(
    public.router,
    tags=["Public"],
)
