# ConstructAI — Feature Roadmap

> **Purpose**: This document captures the product analysis and feature roadmap for making ConstructAI the best-in-class construction management AI platform. It serves as the reference for all future development phases.
>
> **Created**: 2026-03-15
> **Status**: Active

---

## Table of Contents

1. [Current State Assessment](#current-state-assessment)
2. [Competitive Advantages](#competitive-advantages)
3. [Feature Roadmap (3 Phases)](#feature-roadmap)
4. [Phase 1 — Game Changers](#phase-1--game-changers)
5. [Phase 2 — Market Differentiators](#phase-2--market-differentiators)
6. [Phase 3 — Future Moat](#phase-3--future-moat)
7. [Priority Matrix](#priority-matrix)
8. [Market Context](#market-context)

---

## Current State Assessment

### Fully Functional (Production-Ready)

| Domain | Key Capabilities |
|--------|-----------------|
| **Document Management** | Upload, parse, embed, search, RAG Q&A, spec-aware chunking, OSHA integration |
| **Estimating** | 46K+ cost items, Monte Carlo simulation, XGBoost parametric model, regional factors |
| **Scheduling** | CPM analysis, DCMA 14-point checks, weather impact (3-provider), P6/MSP import |
| **Procurement** | FRED/BLS price forecasting (ARIMA+Prophet), contract risk NLP, vendor scoring |
| **Safety** | YOLOv8 PPE detection, predictive risk (5 categories), OSHA compliance, real-time alerts |
| **Quality** | ViT-B/16 defect classification (8-class, 90.28% accuracy), compliance checklists |
| **Controls** | EVM (12 metrics), PCO→COR→CO lifecycle, G702/G703 pay apps, S-curve generation |
| **Communication** | 3-stage RFI resolution agent, meeting transcription, submittals, daily logs, punch lists, drawings |
| **Intelligence** | Multi-agent weekly brief (LangGraph), health scoring, PDF export |
| **Procore Integration** | OAuth, bidirectional sync, webhooks, HMAC verification |
| **Bid Management** | Bid tracking, decision engine, win/loss analytics, CSV import/export |
| **Field Management** | Equipment, materials, permits, risk register, punch lists |
| **Logistics** | NSGA-II site layout optimization, delivery routing, discrete-event simulation |
| **ML/CV Pipelines** | 4 trained models (YOLO, ViT, BGE embeddings, XGBoost cost) |

### Gaps

| Domain | Status |
|--------|--------|
| Portfolio Analytics | Stubbed endpoints only |
| Generative Scheduling | Not built |
| Payment Workflow Automation | Not built |
| Progress Tracking from Photos | Not built |
| Digital Twin | Not built |
| Certified Payroll | Not built |
| Sustainability/LEED Tracking | Not built |
| Natural Language Interface | Not built |

---

## Competitive Advantages

Features where ConstructAI is ahead of all competitors:

1. **3-Stage RFI Resolution Agent** (LangGraph) — Procore just launched a basic version; ours has unnecessary detection + auto-drafting + verification
2. **FRED/BLS Price Forecasting** — Real market data integration with ARIMA+Prophet ensemble; no competitor has this
3. **Construction-Specialized RAG** — Fine-tuned BGE embeddings + OSHA knowledge base + spec-aware chunking
4. **Dual CV Models** — Safety (YOLOv8, 13 classes) + Defect (ViT, 8 classes) in one platform
5. **Parametric Cost Model** — XGBoost with 24 features and prediction intervals; unique in the market
6. **Multi-Agent Orchestration** — LangGraph-based agents for RFI resolution, weekly briefs, scheduling, procurement
7. **Predictive Safety Risk** — Weather-aware, OSHA-mapped, 5-category scoring with personalized briefings

---

## Feature Roadmap

### Phase 1 — Game Changers (Current Sprint)

These features leverage existing infrastructure for maximum impact with moderate effort:

#### 1.1 "Ask ConstructAI" — Natural Language Project Interface
- Conversational AI to query all project data: schedules, costs, RFIs, safety, weather
- Routes queries to appropriate agents via the orchestrator
- Returns structured answers with citations and visualizations
- Examples: "What's the SPI on Building C?", "Show me open RFIs over 14 days", "Why did concrete slip last week?"
- **Foundation**: Existing RAG pipeline + orchestrator agent + all domain services

#### 1.2 Generative Schedule Optimization ("What-If Engine")
- Generate and compare thousands of schedule scenarios
- "What if we add a night shift?", "What if steel delivery slips 2 weeks?"
- Combines CPM engine + weather data + Monte Carlo + cost impact
- Returns ranked scenarios with cost/time/risk tradeoffs and confidence intervals
- **Foundation**: Existing CPM engine + weather service + Monte Carlo + price forecaster

#### 1.3 Predictive Cash Flow & Payment Waterfall
- Forecast cash flow per project and across portfolio
- Model payment waterfall: owner → GC → subs → suppliers
- Predict payment timing from historical patterns
- Auto-generate lien waiver tracking
- **Foundation**: Existing pay application math + FRED price forecasting + change order tracking

#### 1.4 Multilingual Jobsite Communication
- Real-time translation of safety briefings, daily reports, RFIs, meeting minutes
- Voice commands in any language
- Translated push notifications for safety alerts
- Primary: English ↔ Spanish (30%+ of US construction workforce)
- **Foundation**: Existing LLM gateway + voice transcription + safety alerts

### Phase 2 — Market Differentiators

#### 2.1 AI Progress Tracking from Site Photos
- Upload daily photos → CV compares against schedule → auto-calculate % complete
- Integrates with CPM (auto-update), EVM (auto-recalculate), safety (detect hazards)
- **Foundation**: Existing YOLOv8 + ViT + camera infrastructure

#### 2.2 Contract Intelligence Agent
- Upload contracts → AI extracts key clauses → compare against standards → flag risks
- Auto-populate project settings from contract terms (retainage, LD, notice periods)
- **Foundation**: Existing RAG pipeline + LLM gateway + document ingestion

#### 2.3 Automated Daily Report Generation
- Auto-generate from: badge data, weather API, camera feeds, equipment telemetry, safety alerts
- Superintendent reviews and approves vs. writing from scratch
- **Foundation**: Existing weather service + safety alerts + camera infrastructure

#### 2.4 LEED v5 & Sustainability Tracking
- Embodied carbon per material (from 46K cost items), lifecycle carbon calculations
- LEED v5 credits documentation, salvaged materials tracking
- **Foundation**: Existing cost database + regional factors

#### 2.5 Subcontractor Portal
- Lightweight portal for subs: submit manpower, upload receipts, view SOV, submit pay apps
- Translated interface (from Phase 1 multilingual)

#### 2.6 Workforce Analytics & Forecasting
- Labor productivity by trade/activity, forecast needs across portfolio
- Identify skill gaps, predict overtime, flag fatigue risks
- **Foundation**: Existing productivity service + predictive risk engine

#### 2.7 Cross-Project Learning ("Institutional Memory")
- Extend project memory to learn across projects within an org
- "Last 5 hospital projects: concrete pours in winter took 15% longer"
- Data moat that grows with usage
- **Foundation**: Existing project memory service + fact extractor

### Phase 3 — Future Moat

#### 3.1 Digital Twin Integration
- Import BIM/IFC, overlay real-time sensor data, create living 3D model
- **Foundation**: Existing IFC parser

#### 3.2 Drone/UAV Data Integration
- Ingest orthomosaics, point clouds, video → process with CV
- Earthwork volume calculations, deviation analysis

#### 3.3 Certified Payroll & Prevailing Wage
- Automated prevailing wage lookup, WH-347 generation, OSHA electronic reporting

#### 3.4 Insurance & Risk Data Export
- Package safety data for underwriters, auto-generate EMR documents

---

## Priority Matrix

| Feature | Effort | Impact | Revenue | Phase |
|---------|:------:|:------:|:-------:|:-----:|
| Ask ConstructAI (NL Interface) | Medium | Very High | High | **1** |
| Generative Schedule Optimization | High | Very High | Very High | **1** |
| Predictive Cash Flow | Medium | Very High | High | **1** |
| Multilingual Communication | Low | High | Medium | **1** |
| AI Progress Tracking | High | Very High | Very High | 2 |
| Contract Intelligence Agent | Medium | High | High | 2 |
| Auto Daily Reports | Medium | High | Medium | 2 |
| LEED v5 / Sustainability | Medium | High | High | 2 |
| Subcontractor Portal | Medium | High | High | 2 |
| Workforce Analytics | Medium | High | High | 2 |
| Cross-Project Learning | High | Very High | Very High | 2 |
| Digital Twin | Very High | Very High | Very High | 3 |
| Drone Integration | High | High | High | 3 |
| Certified Payroll | High | High | Very High | 3 |
| Insurance Data Export | Low | Medium | Medium | 3 |

---

## Market Context

- Construction management software market: **$10.6B** (2025) → **$17.7B** by 2031 (8.9% CAGR)
- Construction AI market: projected **$20B** by 2026
- AI-driven progress analytics: fastest segment at **14.1% CAGR**
- Cloud solutions: **62%** market share, growing at 12% CAGR
- 95% of construction data goes unused → 28% average budget overruns
- 70% of contractors report regular payment delays
- 499,000 new workers needed in 2026; 94% of contractors can't fill positions
- LEED v5 (April 2025) now requires embodied carbon quantification
- OSHA expanded electronic reporting for companies with 100+ employees
- Agentic AI market: $7.6B (2025) → $50.3B by 2030 (45.8% CAGR)

### Key Competitors
- **Procore**: Market leader, launched Procore Assist (NL) and Agent Builder in 2025
- **ALICE Technologies**: Generative scheduling, revenue doubling YoY
- **Autodesk ACC**: BIM 6.0 convergence, Neural CAD
- **nPlan**: AI schedule risk trained on 750K+ schedules
- **Briq**: AI financial forecasting (CoPilot)
- **OpenSpace/Buildots/Doxel**: CV progress tracking

### ConstructAI's Unfair Advantage
No competitor has ALL of these in one platform: RFI resolution agents, construction RAG, parametric cost models, predictive safety, computer vision, price forecasting, and multi-agent orchestration. The Phase 1 features leverage this integrated data advantage to create capabilities no point solution can match.

---

*Last updated: 2026-03-15*
