# ConstructAI Sales Demo Script

**Project:** Metro Center Office Tower
**Duration:** 30–60 minutes
**Audience:** Construction executives, PMs, and IT decision-makers

---

## Pre-Demo Setup

### 1. Generate Demo Data

```bash
cd apps/api

# First run — creates all demo data
python scripts/generate_demo_project.py

# To reset and regenerate fresh data
python scripts/generate_demo_project.py --clean
```

The script creates the **"Metro Center Office Tower"** project — a 4-story + basement, 85,000 SF commercial office building in Roanoke, VA. The project is 5 months into a 14-month, $12.5M schedule and includes realistic data across every module.

### 2. Demo Credentials

| Role | Email | Password |
|------|-------|----------|
| Project Manager (Org Admin) | `sarah.chen@metro-demo.com` | `DemoPass123!` |
| Superintendent | `mike.rodriguez@metro-demo.com` | `DemoPass123!` |
| Safety Manager | `lisa.thompson@metro-demo.com` | `DemoPass123!` |
| Architect | `david.kim@metro-demo.com` | `DemoPass123!` |
| MEP Coordinator | `rachel.patel@metro-demo.com` | `DemoPass123!` |
| Field Engineer | `james.wilson@metro-demo.com` | `DemoPass123!` |

### 3. Key Numbers to Remember

| Metric | Value | Talking Point |
|--------|-------|---------------|
| Contract Value | $12,500,000 | Mid-market commercial |
| Billed to Date | ~$3.98M (31.8%) | 4 pay apps processed |
| Schedule Progress | ~35% | 5 months into 14-month project |
| SPI | 0.88 | 12% behind schedule — show why |
| CPI | 0.94 | 6% over budget — CO exposure |
| Open RFIs | 10 | 2 are overdue |
| Health Score | 68/100 (YELLOW) | AI caught the trend early |

---

## Act 1: Project Dashboard (5 min)

**Goal:** Establish the project context and hook with the AI health score.

### Talking Points

1. **Open the project** — "Metro Center Office Tower"
   - 4-story + basement commercial office, Roanoke VA
   - $12.5M contract, 14-month schedule, currently at month 5
   - *"This is a real-world mid-market project — exactly the kind your teams run every day."*

2. **Health Score: 68 (YELLOW)**
   - *"The first thing your PM sees every morning. ConstructAI analyzed last night's data and flagged this project as YELLOW — not in crisis, but trending in the wrong direction."*
   - Point out: schedule health 62, cost health 72, risk 65, productivity 74

3. **Key Insight**
   - *"Most platforms show you what happened. ConstructAI tells you what's about to happen — and what to do about it."*

---

## Act 2: AI Intelligence Brief (5 min)

**Goal:** Demonstrate the AI's ability to synthesize project-wide data into actionable intelligence.

### Talking Points

1. **Open the weekly Intelligence Brief**
   - Generated automatically every Monday
   - Executive summary: SPI trending down, 3 delay events identified, CO exposure at $43K

2. **Walk through the action items:**
   - *"Accelerate curtain wall installation — the system identified this as the biggest schedule risk."*
   - *"Resolve pending PCOs before they age out — $43K in exposure."*
   - *"Increase MEP crew size — current staffing won't support the rough-in schedule."*

3. **Key Insight**
   - *"Your superintendent doesn't have to spend 2 hours Sunday night writing this report. The AI reads every daily log, every RFI, every pay app — and synthesizes it into 3 things you need to do this week."*

---

## Act 3: Schedule & CPM Analysis (10 min)

**Goal:** Show schedule depth — CPM, delays, and progress tracking.

### Talking Points

1. **Schedule Overview**
   - 215 activities across 9 WBS phases
   - Critical path: Foundation → Structure → MEP Rough-in → Commissioning
   - *"This isn't a toy Gantt chart. It's a full CPM schedule with predecessors, float, and early/late dates."*

2. **Progress at Month 5**
   - Phases 1–2 (Mobilization, Foundation): 100% complete
   - Phase 3 (Structure): ~80% — Floor 4 in progress
   - Phase 4 (Envelope): ~30% started — curtain wall on Floors B and 1
   - Phase 5 (MEP Rough-in): ~15% started
   - *"We're 35% through the schedule but only 35% complete. That's where the SPI of 0.88 comes from."*

3. **Delay Events**
   - Week 4: 5-day weather delay on foundation excavation
   - Week 14: 8-day RFI delay waiting for curtain wall anchor detail
   - Week 18: 3-day material delay — electrical switchgear
   - *"Three different types of delay — weather, information, and materials. The system tracked all of them and their impact on the critical path."*

4. **Key Insight**
   - *"Your schedulers spend hours updating P6. ConstructAI ingests the P6 export, overlays daily log data, and flags variances automatically."*

---

## Act 4: Cost Controls (10 min)

**Goal:** Demonstrate full cost lifecycle — SOV, pay apps, change orders, and EVM.

### Talking Points

1. **Schedule of Values (52 line items)**
   - Mapped to CSI divisions (01 through 33)
   - *"The SOV was set up once. Every pay application since then auto-calculates based on field-reported progress."*

2. **Pay Applications (G702/G703)**
   - 4 monthly pay apps processed
   - Show Pay App #4 (~$1.05M): line items, retainage (10%), previous billing
   - *"This is a real AIA G702/G703. Your PM clicks 'Generate', reviews the numbers, and sends it to the owner. What used to take a full day now takes 20 minutes."*

3. **Change Orders**
   - 3 Approved COs totaling $95,000:
     - CO-001: Dewatering ($45K) — field condition
     - CO-002: Beam upgrade ($32K) — design error
     - CO-003: Fire code compliance ($18K) — regulatory
   - 2 Pending PCOs ($43K exposure):
     - PCO-004: Curtain wall revision ($28K) — owner-directed
     - PCO-005: Additional fire stopping ($15K) — regulatory
   - *"Every change order has a full audit trail — who initiated it, why, the cost impact, and the schedule impact. No more lost PCOs."*

4. **Earned Value Management**
   - 5 monthly snapshots showing trends
   - CPI ≈ 0.94 (6% over budget), SPI ≈ 0.88 (12% behind)
   - *"The EVM chart tells the story: CPI is stable — we're managing costs. But SPI is declining — those three delays compounded. The AI caught this trend at month 3 and recommended acceleration."*

5. **Key Insight**
   - *"Cost controls is where GCs lose money — not because the numbers are wrong, but because they see them too late. ConstructAI gives you the EVM dashboard in real-time, not at the quarterly review."*

---

## Act 5: RFI & Submittal Management (10 min)

**Goal:** Showcase the AI-powered RFI resolution engine — the headline feature.

### Talking Points

1. **RFI Dashboard**
   - 25 total RFIs: 15 closed, 5 open, 3 pending, 2 overdue
   - *"Standard RFI tracking, but watch what happens when a new RFI comes in."*

2. **AI-Resolved RFI Demo — Unnecessary Detection**
   - Show the RFI about concrete cure time
   - *"A field engineer submitted this RFI: 'What is the minimum cure time for the 4000 PSI mix before post-tensioning?' The AI searched the project specs and found the answer in Section 03 30 00, paragraph 3.4.2 — 7 days minimum."*
   - Stage 1 flagged it as unnecessary with source citation
   - *"This RFI never needed to leave the site. The answer was already in the spec. The AI found it in 8 seconds — versus the 3-5 day round-trip to the architect."*

3. **AI-Resolved RFI Demo — Draft Response**
   - Show the RFI about duct routing conflict
   - *"This one was legitimate — a real MEP coordination issue. The AI drafted a response using the project drawings, ASHRAE standards, and similar RFIs from past projects. The architect reviewed it, made one edit, and approved."*
   - Show the 3-stage pipeline: retrieve → draft → verify (hallucination check, contradiction check, completeness check)

4. **Submittal Tracking**
   - 30 submittals across all statuses
   - Shop drawings, product data, samples, test reports
   - *"Same AI intelligence applied to submittals — auto-checking spec compliance, flagging missing data, tracking review cycles."*

5. **Key Insight**
   - *"Industry data shows 30% of RFIs are unnecessary — the answer already exists somewhere in the project documents. ConstructAI eliminates those instantly. For the remaining 70%, it drafts responses that are right 85% of the time, cutting architect response time from 5 days to same-day."*

---

## Act 6: Field Operations (5 min)

**Goal:** Show daily operations — logs, punch lists, drawings.

### Talking Points

1. **Daily Logs (100+ entries)**
   - One per workday for 5 months
   - Weather, crew counts, trades, equipment, safety topics
   - Crew ramp from 15 to 65 workers over the project
   - *"Your superintendent fills this out on a tablet in 5 minutes. The AI reads every word — that's how it knows about the weather delays, the equipment mobilization, and the productivity trends."*

2. **Punch List — Pre-Drywall Walkthrough**
   - 40 items from Basement & Floors 1-2 walkthrough
   - 15 open, 12 in progress, 8 resolved, 5 verified
   - Categories: MEP rough-in, framing, fire stopping, waterproofing
   - *"The superintendent did the walkthrough, marked items on the drawing overlay, assigned them to trades — all from the field. No paper lists to transcribe."*

3. **Drawing Management**
   - 5 sets (A, S, M, E, P), 20 sheets, revision tracking
   - *"Every drawing revision is tracked. When the architect issues ASI #3, the system flags which RFIs and submittals reference the affected sheets."*

4. **Key Insight**
   - *"Field data isn't just a record — it's the fuel for every AI insight. The daily logs feed the schedule analysis, the cost projections, and the risk scoring."*

---

## Act 7: Safety & Compliance (5 min)

**Goal:** Demonstrate the predictive safety engine and real-time monitoring.

### Talking Points

1. **Daily Risk Score: 35 (Moderate)**
   - Category breakdown: Fall 40, Struck-by 30, Excavation 15, Electrical 45, Heat 25
   - *"Every morning, the system calculates a site-specific risk score based on today's planned activities, the weather forecast, the crew mix, and historical incident data."*

2. **Camera-Based Safety Zones**
   - 2 cameras: Tower Crane Area, Main Entrance
   - 3 zones: Crane swing (active), PPE required (entrance), Excavation (foundation)
   - *"The CV system watches these zones 24/7. If someone enters the crane swing zone without authorization, the super gets an alert in 30 seconds."*

3. **Near-Miss Alerts**
   - Alert 1: Worker without hard hat near crane zone — acknowledged, no injury
   - Alert 2: Unauthorized vehicle in excavation zone — acknowledged, no injury
   - *"Two near-misses caught by the system, both resolved without incident. On a traditional site, neither of these would have been documented."*

4. **Inspection Tracking**
   - 5 inspections: 2 complete (scoring 92+), 2 in progress, 1 scheduled
   - Zero OSHA violations, zero recordable incidents
   - *"EMR trending down, zero incidents — that's not luck, that's predictive safety."*

5. **Key Insight**
   - *"Safety isn't reactive. ConstructAI tells you what's going to go wrong today and what to do about it — before anyone gets hurt."*

---

## Act 8: Bid Decision Engine (5 min)

**Goal:** Show the bid/no-bid AI engine learning from historical data.

### Talking Points

1. **Bid History (20 opportunities)**
   - 6 won, 12 lost, 2 no-bid
   - *"Your company has been using ConstructAI for a year. Every bid — won or lost — trains the AI to make better recommendations."*

2. **Pattern Recognition**
   - Won bids: strong in commercial and institutional, $5-15M range, hard bid
   - Lost bids: healthcare, projects over $20M, negotiated contracts
   - *"The AI learned that your company wins 60% of commercial hard bids under $15M, but only 15% of healthcare negotiated bids over $20M. That's not gut feel — that's data."*

3. **Live Scoring Demo — New Opportunity**
   - "Roanoke County Elementary School" — $8M, K-12, hard bid, due in 3 weeks
   - AI Score: 72/100 — **PURSUE**
   - Win probability: 58%
   - *"New opportunity just came in. The AI scored it 72 — PURSUE. It's in your sweet spot: local, education sector, hard bid, right size. The factors show high marks on geographic fit, type match, and capacity."*

4. **Key Insight**
   - *"Most GCs pursue everything and win 20%. ConstructAI helps you pursue the right 60% and win 40%. That's the difference between growing profitably and growing broke."*

---

## Closing & Q&A (5 min)

### Summary Slide

| Module | What They Saw |
|--------|---------------|
| Dashboard | AI health score catching problems early |
| Intelligence | Weekly brief replacing 2-hour manual reports |
| Schedule | Full CPM with delay tracking and variance analysis |
| Cost | SOV → Pay Apps → Change Orders → EVM in one system |
| RFI/Submittals | AI resolving 30% of RFIs instantly, drafting the rest |
| Field Ops | Daily logs feeding AI insights, punch lists on tablets |
| Safety | Predictive risk scoring, CV-powered zone monitoring |
| Bid Engine | AI learning from history to pick the right projects |

### Closing Lines

- *"Everything you saw today is from one project with 5 months of data. Imagine this across your entire portfolio — 10, 20, 50 projects."*
- *"ConstructAI doesn't replace your people. It makes your best PM's instincts available to every project, every day."*
- *"The ROI model is simple: eliminate 30% of unnecessary RFIs, catch cost overruns 60 days earlier, reduce safety incidents by 40%, and win more of the right bids."*

### Common Questions

**Q: How long does implementation take?**
A: 2-4 weeks for core modules. If you're on Procore, we sync directly — no double entry.

**Q: Does it work with our scheduling software?**
A: We import from P6, MS Project, and Asta. The CPM engine runs natively.

**Q: What about data security?**
A: SOC 2 Type II, per-tenant data isolation, role-based access control with MFA. Your data never trains models for other customers.

**Q: Can we start with just one module?**
A: Absolutely. Most customers start with RFI/Submittal management or Cost Controls, then expand. The AI gets smarter as you add modules because it can cross-reference more data.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Demo data missing | Run `python scripts/generate_demo_project.py --clean` |
| Login fails | Verify email_verified=True on demo users |
| Dates look stale | Re-run the generator — all dates are relative to today |
| API returns 403 | Check the user role — use Sarah Chen (org_admin) for full access |
| Health score not showing | Verify IntelligenceBrief and DailyRiskScore records exist |
