# ConstructAI Demo Environment

One-command local demo showcasing all 11 agents, the orchestration layer, and every dashboard.
Runs entirely on a developer's laptop with no GPU required.

## Quick Start

```bash
make demo
# Wait ~2 minutes for infrastructure + seed data
# Open http://localhost:3000
# Login: pm@buildright.dev / Demo2026!
```

## Demo Project: Riverside Mixed-Use Development

- **Type**: 5-story mixed-use (retail + office + residential)
- **Value**: $45M contract
- **Schedule**: 18 months (May 2025 - Oct 2026), currently at month 10
- **Status**: Behind schedule (SPI 0.88), over budget (CPI 0.91)
- **Location**: 100 Riverside Drive, Roanoke, VA 24011

### Demo Credentials

| Role | Email | Password |
|------|-------|----------|
| Project Manager | pm@buildright.dev | Demo2026! |
| Safety Manager | safety@buildright.dev | Demo2026! |
| Owner Rep | owner@riverside.dev | Demo2026! |
| Platform Admin | admin@constructai.dev | Demo2026! |
| Superintendent | super@buildright.dev | Demo2026! |
| Architect | architect@arcdesign.dev | Demo2026! |

### Demo Data Summary

| Domain | Records | Highlights |
|--------|---------|------------|
| Organizations | 3 | Owner, GC, Architect |
| Users | 9 | Multiple roles across orgs |
| Schedule Activities | 50 | DCMA issues planted for detection |
| EVM Snapshots | 10 | S-curve showing divergence at month 5 |
| Safety Alerts | 15 | P1-P4, 2 false positives |
| Cameras | 4 | North gate, crane, loading dock, rooftop |
| Safety Zones | 3 | Exclusion, restricted, warning |
| Inspections | 8 | Foundation, steel, MEP, drywall |
| Defects | 12 | Cracks, misalignments, coordination |
| Punch List Items | 15 | 8 open, 4 in progress, 3 completed |
| Compliance Checks | 5 | IBC egress, ADA clearances |
| Change Orders | 3 | Approved, pending, new |
| Daily Reports | 10 | Weather, labor, equipment, narrative |
| Meeting Minutes | 3 | OAC, safety, coordination |
| RFIs | 5 | Open, responded, closed |
| Project Facts | 15 | Decisions, constraints, risks |
| Workflows | 3 | Onboarding, CO processing, safety |
| Specifications | 5 | Div 03, 05, 07, 09, 26 PDFs |

---

## Demo Script (20 minutes)

### 1. Login & Overview (2 min)

Log in as **Sarah Chen** (pm@buildright.dev). The project dashboard shows:
- **SPI 0.88** - 12% behind schedule due to foundation issues in months 5-6
- **CPI 0.91** - 9% over budget from change orders and recovery costs
- **3 active change orders** (1 approved, 1 awaiting, 1 pending)
- **15 safety alerts** in the past 30 days (2 P1 critical)
- **8 open punch list items** across multiple trades

### 2. Document Intelligence (3 min)

Navigate to **Documents**. Show 5 uploaded specifications:
1. Click on **Section 03 30 00 - Cast-in-Place Concrete**
2. Ask: *"What is the required compressive strength for foundation concrete?"*
3. Show RAG answer: **5,000 PSI** Class A per Section 2.01 mix design table
4. Show citation highlighting the exact passage in the specification
5. Try conflict detection between concrete and steel specifications

### 3. EVM & Project Controls (3 min)

Navigate to **Controls** dashboard:
1. Show S-curve chart with PV/EV/AC lines diverging
2. Point out the **month 5 inflection** where foundation issues started
3. Current state: SPI 0.88, CPI 0.91
4. **EAC forecast**: ~$49.5M vs. $45M budget (10% overrun trending)
5. Show Monte Carlo risk histogram with P50/P80/P90 completion dates
6. Show critical risk drivers table

### 4. Schedule Analysis (2 min)

Show the schedule with 50 activities and trigger **DCMA 14-point check**:
- **3 missing predecessors** (A012, A056, A066) - DCMA Check #1
- **2 excessive lags** > 5 days (A024: 10-day lag, A076: 8-day lag) - Check #5
- **4 high-float activities** > 44 days (A012, A056, A066, A079) - Check #7
- **1 negative lag** (A046: -3 day lead) - Check #4

Show critical path: Mobilization -> Excavation -> Foundation -> Steel -> Deck -> MEP -> Drywall -> Finishes -> Commissioning

### 5. Change Order Cascade (3 min)

Navigate to **Change Orders**:
1. Show CO-001 (approved, $350K rooftop terrace)
2. Show CO-002 (impact analysis complete, $280K foundation redesign)
3. Open **CO-003** (pending, $95K electrical panel upgrade)
4. Trigger analysis: `make demo-change-order`
5. Show orchestrator **fan-out** to 3 agents in parallel:
   - Estimating Agent: cost impact breakdown
   - Scheduling Agent: schedule impact (5 days, critical path?)
   - Controls Agent: risk assessment
6. Show consolidated impact report
7. Show human-in-the-loop approval gate

### 6. Safety Monitoring (3 min)

Navigate to **Safety** dashboard:
1. Show camera grid (4 cameras: north gate, crane zone, loading dock, rooftop)
2. Show alert timeline with P1/P2/P3/P4 severity badges
3. Show **crane exclusion zone** polygon on site map
4. Show false positive feedback workflow (2 alerts marked as FP)
5. Mention temporal smoothing and alert deduplication
6. **Switch login** to safety@buildright.dev to demonstrate role-based access

### 7. Quality & Punch List (2 min)

Navigate to **Quality**:
1. Show 8 inspections (foundation, structural steel, MEP, drywall)
2. Show defect classifications (crack, misalignment, coordination conflict)
3. Show punch list: 8 open, 4 in progress, 3 completed
4. Show compliance checks: IBC egress (passed), ADA clearances (2 failed)

### 8. Daily Reports & Communication (2 min)

Navigate to **Reports**:
1. Show auto-generated daily report with weather, labor, equipment
2. Show meeting minutes with extracted action items and decisions
3. Show RFIs with AI-suggested responses
4. Show project memory/facts panel (15 facts across 5 categories)

---

## Services & URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Frontend | http://localhost:3000 | See demo credentials above |
| API Docs (Swagger) | http://localhost:8000/docs | N/A |
| pgAdmin | http://localhost:5050 | admin@constructai.dev / demo2026 |
| Kafka UI | http://localhost:8080 | N/A |
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |

## Make Targets

```bash
make demo              # Full setup: infra + migrate + seed + start
make demo-seed         # Re-seed demo data (requires running infra)
make demo-teardown     # Stop everything and remove volumes
make demo-open         # Open all dashboard browser tabs
make demo-walkthrough  # Interactive CLI walkthrough guide
make demo-onboarding   # Trigger new project onboarding workflow
make demo-change-order # Trigger change order analysis cascade
make demo-safety       # Trigger safety incident response
make demo-models       # Download ML models for CPU inference
```

## Troubleshooting

### Port already in use

```bash
make demo-teardown
make demo
```

### LLM calls failing

Check API keys in `.env`. For demos without API keys, enable the semantic cache:
```bash
export DEMO_MODE=true
```
This uses pre-computed results for core workflows.

### Slow inference

CPU models are used by design for laptop demos. Pre-computed results are used
where possible. The YOLO11n and RTMPose-s models are specifically chosen for
CPU performance.

### Database errors

Clean slate:
```bash
make demo-teardown
make demo
```

### Missing Python packages

```bash
cd apps/api && pip install -e ".[dev]"
pip install reportlab  # For PDF generation
```

## Architecture

```
make demo
  |
  +-- docker-compose.yml + docker-compose.demo.yml
  |     +-- PostgreSQL 17 (TimescaleDB) :5432
  |     +-- Redis 7.4                    :6379
  |     +-- Kafka (KRaft)                :9092
  |     +-- MinIO                        :9000/:9001
  |     +-- Mosquitto MQTT               :1883
  |     +-- pgAdmin                      :5050
  |     +-- Kafka UI                     :8080
  |
  +-- alembic upgrade head (7 migrations, 54+ tables)
  |
  +-- demo/seed/seed_all.py
  |     +-- 3 orgs, 9 users, 1 project
  |     +-- 50 schedule activities with DCMA issues
  |     +-- 10 EVM snapshots (S-curve data)
  |     +-- 15 safety alerts, 4 cameras, 3 zones
  |     +-- 8 inspections, 12 defects, 15 punch items
  |     +-- 3 change orders, 5 RFIs, 10 daily reports
  |     +-- 15 project facts, 3 workflow histories
  |
  +-- uvicorn app.main:app :8000
  +-- next dev              :3000
```
