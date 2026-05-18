# ConstructAI API Reference

**Base URL:** `http://localhost:8000/api/v1`

All endpoints require `Authorization: Bearer <token>` unless noted otherwise.
Interactive docs: `http://localhost:8000/docs` (Swagger UI)

---

## Authentication

### POST `/auth/register`
Register a new user account.
```json
// Request
{ "email": "user@example.com", "password": "SecurePass123!", "full_name": "Jane Doe" }

// Response 201
{ "id": "uuid", "email": "user@example.com", "full_name": "Jane Doe", "is_verified": false }
```

### POST `/auth/login`
Authenticate and receive JWT tokens.
```json
// Request
{ "email": "user@example.com", "password": "SecurePass123!" }

// Response 200
{ "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer" }
```

### POST `/auth/refresh`
Refresh an expired access token.
```json
// Request
{ "refresh_token": "eyJ..." }

// Response 200
{ "access_token": "eyJ...", "token_type": "bearer" }
```

### POST `/auth/mfa/setup` | POST `/auth/mfa/verify` | POST `/auth/mfa/verify-setup` | DELETE `/auth/mfa/disable`
MFA (TOTP) lifecycle endpoints.

### GET `/auth/me`
Get current authenticated user profile.

---

## Organizations

### POST `/organizations/`
Create a new organization (platform admin only).
```json
// Request
{ "name": "Acme Construction", "industry": "commercial" }
```

### GET `/organizations/` | GET `/organizations/{org_id}`
List or get organization details.

---

## Projects

### POST `/projects/`
```json
// Request
{
  "name": "Downtown Office Tower",
  "organization_id": "uuid",
  "location": "Austin, TX",
  "budget": 25000000,
  "start_date": "2025-03-01",
  "end_date": "2026-12-31"
}
```

### GET `/projects/` | GET `/projects/{project_id}` | PATCH `/projects/{project_id}`
List, get, or update project details.

---

## Project Controls (EVM)

### POST `/controls/evm-snapshots`
Create an EVM snapshot with computed metrics.
```json
// Request
{
  "project_id": "uuid",
  "snapshot_date": "2025-06-01",
  "bac": 10000000,
  "pv": 3500000,
  "ev": 3200000,
  "ac": 3400000
}

// Response 201 — includes computed SPI, CPI, SV, CV, EAC, ETC, VAC, TCPI
{
  "id": "uuid",
  "spi": 0.914,
  "cpi": 0.941,
  "sv": -300000,
  "cv": -200000,
  "eac": 10625000,
  "etc": 7225000,
  "vac": -625000,
  "tcpi": 1.030
}
```

### GET `/controls/evm-snapshots?project_id=uuid`
List all EVM snapshots for a project (trend data).

### GET `/controls/s-curve/{project_id}`
Get S-curve data (PV/EV/AC plotted over time).
```json
// Response 200
{
  "bac": 10000000,
  "data_points": [
    { "date": "2025-03-01", "pv": 500000, "ev": 480000, "ac": 510000 }
  ]
}
```

### POST `/controls/schedule-risk`
Run Monte Carlo schedule risk simulation.
```json
// Request
{
  "project_id": "uuid",
  "iterations": 10000,
  "confidence_levels": [50, 80, 90]
}

// Response 200
{
  "p50": 425,
  "p80": 448,
  "p90": 462,
  "mean_duration": 430.5,
  "histogram": [{ "bin_start": 400, "bin_end": 410, "count": 245 }]
}
```

---

## Change Order Lifecycle

### POST `/controls/pcos`
Create a Potential Change Order.
```json
// Request
{
  "project_id": "uuid",
  "title": "Foundation redesign",
  "description": "Soil conditions require deeper footings",
  "estimated_cost": 85000,
  "schedule_impact_days": 14,
  "reason": "unforeseen_conditions"
}
```

### GET `/controls/pcos?project_id=uuid` | GET `/controls/pcos/{pco_id}`
List or get PCO details.

### PATCH `/controls/pcos/{pco_id}`
Update PCO or transition status (draft → pending → approved/rejected).

### POST `/controls/cors`
Create a Change Order Request linking one or more PCOs.
```json
// Request
{
  "project_id": "uuid",
  "title": "COR-001: Foundation Changes",
  "pco_ids": ["uuid1", "uuid2"]
}
```

### POST `/controls/cors/{cor_id}/approve`
Approve COR — creates a formal Change Order, updates SOV if applicable.

### GET `/controls/change-orders/cumulative-impact?project_id=uuid`
Get cumulative cost and schedule impact of all approved change orders.
```json
// Response 200
{
  "total_approved_cost": 285000,
  "total_schedule_impact_days": 28,
  "original_contract_value": 10000000,
  "revised_contract_value": 10285000,
  "change_order_count": 4
}
```

---

## Pay Applications (G702/G703)

### POST `/pay-applications/sov`
Bulk create Schedule of Values line items.
```json
// Request
{
  "project_id": "uuid",
  "items": [
    { "item_number": "1", "description": "Site Work", "scheduled_value": 500000 },
    { "item_number": "2", "description": "Concrete", "scheduled_value": 1200000 }
  ]
}
```

### GET `/pay-applications/sov?project_id=uuid`
List SOV items for a project.

### POST `/pay-applications/`
Create a new pay application (period billing).
```json
// Request
{
  "project_id": "uuid",
  "application_number": 3,
  "period_from": "2025-05-01",
  "period_to": "2025-05-31"
}
```

### GET `/pay-applications/{pay_app_id}/pdf/g702`
Download AIA G702 Application and Certificate for Payment (PDF).

### GET `/pay-applications/{pay_app_id}/pdf/g703`
Download AIA G703 Continuation Sheet (PDF).

### POST `/pay-applications/{pay_app_id}/submit` | POST `/{pay_app_id}/certify`
Status transitions: draft → submitted → certified.

---

## RFIs

### POST `/projects/{project_id}/rfis`
Create an RFI with auto-generated number.
```json
// Request
{
  "subject": "Beam connection detail at Grid C-4",
  "question": "Drawing S-201 shows W12x26 but calc sheet specifies W14x30. Which governs?",
  "discipline": "structural",
  "priority": "high",
  "due_date": "2025-06-15"
}

// Response 201
{ "id": "uuid", "rfi_number": "RFI-003", "status": "open", "ai_status": null }
```

### GET `/projects/{project_id}/rfis?status=open&discipline=structural`
List RFIs with optional filters.

### POST `/projects/{project_id}/rfis/{rfi_id}/auto-resolve`
Run the 3-stage RFI Resolution Agent:
1. Unnecessary check (searches specs, historical RFIs, meeting minutes)
2. AI draft response (RAG + OSHA standards for safety topics)
3. Verification (hallucination, contradiction, completeness)

```json
// Response 200
{
  "was_unnecessary": false,
  "draft_response": "Per Section 03 30 00 Part 2.1.B, the W14x30...",
  "confidence": 0.87,
  "sources": ["Structural Specs, p. 42", "RFI-001"],
  "verification": { "passed": true, "warnings": 0, "errors": 0 }
}
```

### POST `/projects/{project_id}/rfis/{rfi_id}/draft-response`
Generate AI draft only (skip unnecessary check).

### GET `/projects/{project_id}/rfis/unnecessary`
List RFIs flagged as unnecessary by the AI agent.

### GET `/projects/{project_id}/rfis/stats`
Aggregate RFI statistics (count by status, avg resolution time, discipline breakdown).

---

## Intelligence Briefs

### POST `/projects/{project_id}/intelligence-brief`
Generate a weekly intelligence brief (LLM-powered).
```json
// Response 201
{
  "id": "uuid",
  "health_score": 72,
  "sub_scores": { "schedule": 65, "cost": 80, "risk": 70, "productivity": 75 },
  "summary": "Project is tracking 2 weeks behind schedule...",
  "action_items": [
    { "priority": "high", "description": "Escalate concrete delivery delay", "owner": "PM" }
  ],
  "pdf_url": "https://minio:9000/constructai/briefs/..."
}
```

### GET `/projects/{project_id}/intelligence-brief/latest`
Get the most recent intelligence brief with presigned PDF URL.

### GET `/projects/{project_id}/intelligence-brief/history?limit=10&offset=0`
Paginated list of all intelligence briefs.

---

## Bid Intelligence

### POST `/orgs/{org_id}/bid-opportunities`
Create a bid opportunity for evaluation.
```json
// Request
{
  "name": "Municipal Water Treatment Plant",
  "estimated_value": 45000000,
  "bid_type": "competitive",
  "delivery_method": "design_bid_build",
  "due_date": "2025-07-15",
  "location": "Dallas, TX",
  "project_type": "water_treatment"
}
```

### POST `/orgs/{org_id}/bid-opportunities/{id}/score`
Run AI scoring agent.
```json
// Response 200
{
  "composite_score": 73.5,
  "recommendation": "pursue",
  "factors": {
    "project_fit": 85,
    "competition": 60,
    "capacity": 70,
    "profitability": 78,
    "risk": 65
  },
  "reasoning": "Strong project fit based on historical win rate in water treatment..."
}
```

### POST `/orgs/{org_id}/bid-opportunities/{id}/decide`
Record human bid/no-bid decision.
```json
// Request
{ "decision": "bid", "notes": "Aligned with strategic growth plan" }
```

### POST `/orgs/{org_id}/bid-opportunities/{id}/outcome`
Record win/loss outcome for analytics.
```json
// Request
{ "outcome": "won", "contract_value": 44200000 }
```

### GET `/orgs/{org_id}/bid-analytics`
Win rate analytics, distributions by type and delivery method.

### POST `/orgs/{org_id}/bid-opportunities/import-csv`
Bulk import historical bid data from CSV.

---

## Predictive Safety

### GET `/projects/{project_id}/safety/risk-score`
Get today's predictive safety risk assessment.
```json
// Response 200
{
  "overall_score": 68,
  "risk_level": "YELLOW",
  "categories": {
    "fall": { "score": 75, "weight": 0.30 },
    "struck_by": { "score": 60, "weight": 0.25 },
    "excavation": { "score": 55, "weight": 0.20 },
    "electrical": { "score": 80, "weight": 0.15 },
    "heat": { "score": 70, "weight": 0.10 }
  },
  "weather_factors": { "temperature": 92, "wind_mph": 18, "precipitation": 0 }
}
```

### GET `/projects/{project_id}/safety/briefing`
Get morning safety briefing text (LLM-generated or template).

### GET `/projects/{project_id}/safety/trends?days=30`
Get risk score history with trend analysis.
```json
// Response 200
{
  "scores": [
    { "date": "2025-05-01", "overall_score": 72, "risk_level": "YELLOW" }
  ],
  "trend": "improving",
  "average_score": 70.5
}
```

---

## Safety Alerts

### GET `/safety/alerts?project_id=uuid&type=ppe_violation`
Query safety alerts with optional filters.

### PATCH `/safety/alerts/{alert_id}/acknowledge`
Acknowledge a safety alert.

### GET `/safety/stats?project_id=uuid`
Aggregated safety statistics.

### WS `/safety/ws/{project_id}`
WebSocket for real-time safety alert streaming.

---

## Cameras

### POST `/cameras/` | GET `/cameras/` | GET `/cameras/{id}` | PATCH `/cameras/{id}` | DELETE `/cameras/{id}`
CRUD for RTSP camera registrations.

---

## Documents & RAG

### POST `/documents/upload`
Upload a document for processing (PDF, IFC, CSV, DOCX).
```bash
curl -X POST /api/v1/documents/upload \
  -F "file=@specs.pdf" \
  -F "project_id=uuid" \
  -F "document_type=specification"
```

### POST `/documents/search`
Hybrid search over project documents.
```json
// Request
{ "project_id": "uuid", "query": "concrete mix design requirements", "limit": 10 }

// Response 200
{
  "results": [
    {
      "chunk_text": "Section 03 30 00 - Cast-In-Place Concrete...",
      "score": 0.92,
      "document_title": "Project Specifications",
      "metadata": { "csi_section": "03 30 00", "part_number": 2 }
    }
  ]
}
```

### POST `/documents/ask`
Ask a natural language question over indexed documents.
```json
// Request
{ "project_id": "uuid", "question": "What is the minimum concrete compressive strength?" }

// Response 200
{ "answer": "Per Section 03 30 00, the minimum compressive strength is 4000 psi at 28 days.", "sources": [...] }
```

---

## Estimating

### POST `/estimating/estimates`
Create a new cost estimate.
```json
// Request
{
  "project_id": "uuid",
  "name": "Schematic Design Estimate",
  "building_type": "office",
  "gross_area_sf": 125000,
  "num_stories": 8,
  "quality_level": 3,
  "location": "Austin, TX"
}
```

### POST `/estimating/estimates/{id}/monte-carlo`
Run Monte Carlo cost simulation.
```json
// Response 200
{
  "mean_cost": 28500000,
  "p50": 28200000,
  "p80": 30100000,
  "p90": 31400000,
  "confidence_interval": [25800000, 32200000]
}
```

### GET `/estimating/cost-items?category=concrete&csi_code=03`
Search cost items database (46,000+ items).

---

## Scheduling

### POST `/scheduling/{project_id}/schedule/import`
Import a P6 XML or XER schedule file via MPXJ.

### POST `/scheduling/baselines` | GET `/scheduling/baselines`
Create and list schedule baselines.

### POST `/scheduling/baselines/{id}/cpm`
Run Critical Path Method analysis.

### POST `/scheduling/dcma-check`
Run DCMA 14-point schedule assessment.

### POST `/scheduling/weather-impact`
Analyze weather impact on schedule activities.
```json
// Request
{
  "project_id": "uuid",
  "location": "Austin, TX",
  "activities": ["concrete_pour", "crane_operations", "exterior_painting"]
}
```

---

## Drawings

### POST `/projects/{project_id}/drawing-sets`
Create a drawing set.
```json
// Request
{ "name": "IFC Set - Rev 3", "discipline": "structural", "issue_date": "2025-05-01" }
```

### POST `/projects/{project_id}/drawing-sets/{set_id}/upload`
Bulk upload drawing files to a set.

### POST `/projects/{project_id}/drawings/{drawing_id}/revisions`
Upload a new revision.

### GET `/projects/{project_id}/drawings/{drawing_id}/revisions/{rev_id}/download`
Get presigned download URL for a drawing revision.

### GET `/projects/{project_id}/drawings/{drawing_id}/compare?rev_a=uuid&rev_b=uuid`
Compare two drawing revisions.

### POST `/projects/{project_id}/drawings/{drawing_id}/revisions/{rev_id}/markups`
Create a markup annotation on a revision.

### POST `/projects/{project_id}/drawings/{drawing_id}/links`
Link drawing to RFI, submittal, or punch list.

---

## Communication

### POST `/communication/meetings`
Create a meeting minutes record.
```json
// Request
{
  "project_id": "uuid",
  "title": "OAC Meeting #12",
  "meeting_type": "oac",
  "meeting_date": "2025-06-01",
  "attendees": ["pm@acme.com", "architect@design.com"],
  "agenda_items": [
    { "topic": "Schedule update", "notes": "2 weeks behind on foundations" }
  ]
}
```

### POST `/communication/meetings/{meeting_id}/transcribe`
Upload and transcribe meeting audio (extracts agenda items and action items).

### GET `/communication/meetings/action-items/overdue`
List all overdue action items across meetings.

### POST `/communication/submittals` | GET `/communication/submittals`
Create and list submittals.

### POST `/communication/daily-reports` | GET `/communication/daily-reports`
Generate and list daily construction reports.

---

## Procore Integration

### GET `/integrations/procore/connect`
Get OAuth authorization URL to connect Procore.

### GET `/integrations/procore/callback`
Handle OAuth callback (sets up connection).

### GET `/integrations/procore/status`
Check current Procore connection status.

### POST `/integrations/procore/sync`
Trigger full data sync from Procore.
```json
// Request
{ "project_id": "uuid", "procore_project_id": "12345" }

// Response 200
{
  "status": "syncing",
  "sync_id": "uuid",
  "entities": ["projects", "rfis", "submittals", "daily_logs", "punch_lists"]
}
```

### GET `/integrations/procore/sync/status`
Get latest sync status and results.

### POST `/integrations/procore/disconnect`
Disconnect Procore integration and revoke tokens.

---

## Quality

### POST `/quality/inspections` | GET `/quality/inspections`
Create and list quality inspections.

### POST `/quality/defects` | GET `/quality/defects`
Create and list defect reports (supports image classification via CV model).

### POST `/quality/ncrs` | GET `/quality/ncrs`
Create and list Non-Conformance Reports.

### GET `/quality/compliance` | GET `/quality/compliance-checklists`
Compliance tracking and reference checklists.

---

## Health Check

### GET `/health`
Basic liveness check. **No auth required.**
```json
{ "status": "healthy" }
```

### GET `/health/ready`
Readiness check (verifies database connectivity). **No auth required.**

---

## Common Patterns

### Pagination
List endpoints support cursor-based pagination:
```
GET /api/v1/projects/?limit=20&offset=0
```

### Error Responses
```json
// 400 Bad Request
{ "detail": "Validation error message" }

// 401 Unauthorized
{ "detail": "Not authenticated" }

// 403 Forbidden
{ "detail": "Insufficient permissions" }

// 404 Not Found
{ "detail": "Resource not found" }

// 422 Unprocessable Entity
{ "detail": [{ "loc": ["body", "field"], "msg": "error", "type": "value_error" }] }
```

### Rate Limiting
API requests are rate-limited per user. Default: 100 requests/minute.

### Webhooks (Procore)
Procore webhooks are received at `POST /api/v1/webhooks/procore` and routed to internal handlers via Kafka. RFI creation events trigger automatic unnecessary RFI detection.

---

## Background Tasks (Celery Beat)

| Task | Schedule | Description |
|------|----------|-------------|
| `refresh-fred-prices` | Daily 14:00 UTC | Refresh FRED material price series |
| `refresh-bls-ppi` | Daily 14:30 UTC | Refresh BLS Producer Price Indices |
| `generate-weekly-briefs` | Monday 11:00 UTC | Generate intelligence briefs for all active projects |
