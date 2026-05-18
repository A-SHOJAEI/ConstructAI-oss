# ConstructAI — System Overview & Improvement Research Brief

> **Purpose of this document:** This is a comprehensive technical summary of the ConstructAI platform — an AI-powered construction management system. It is intended to be fed into an AI assistant (Claude, ChatGPT, etc.) along with the prompt below to identify datasets, API integrations, and improvements needed before launching to first customers.

---

## PROMPT — Copy everything below this line into your AI assistant

I'm building **ConstructAI**, an AI-powered construction project management platform targeting mid-to-large general contractors, construction managers, and owner's representatives. The system is functional with 647 passing tests and has been through 2 rounds of security audits and code quality reviews. We're preparing for our first production customers.

Below is a complete technical description of the system — its architecture, every service module, what data it currently uses, and known gaps. I need your help with:

1. **Datasets** — Find specific, downloadable datasets (free or purchasable) that would improve each service area. For ML models that need training data, suggest the best available datasets with direct links. For services that need reference data (cost databases, regulatory codes, material specifications), identify the most authoritative sources.

2. **API Integrations** — Identify production-grade APIs we should integrate with for real-time data (material prices, weather, labor rates, permit databases, equipment telematics, etc.). For each API, provide: name, what it offers, pricing tier, and how it maps to our services.

3. **Pre-Launch Improvements** — Based on your knowledge of construction technology and what practitioners actually need, identify:
   - Features that are table-stakes for a construction management tool (things we'd be embarrassed to launch without)
   - Differentiating features that would make us stand out from Procore, Autodesk Construction Cloud, and PlanGrid
   - Data quality improvements that would make our algorithms produce trustworthy results
   - UX/workflow considerations that construction professionals would expect

4. **Competitive Positioning** — How does our feature set compare to existing tools? Where are we ahead (AI-native), and where are we behind (basic construction workflows)?

5. **Go-to-Market Data Readiness** — What data do we need on day one so that customers see value immediately without having to configure everything themselves?

For each recommendation, be specific: give names, URLs, pricing, data formats, and explain exactly which of our services would consume the data. Prioritize by impact — what gives us the most credibility and accuracy improvement per dollar/effort spent.

---

## SYSTEM ARCHITECTURE

### Tech Stack
| Layer | Technology |
|-------|-----------|
| **Backend API** | FastAPI 0.115.6, Python 3.12, async/await throughout |
| **Database** | PostgreSQL 17 + pgvector (embeddings) + PostGIS (geospatial) + TimescaleDB (time-series) |
| **Cache** | Redis 7.4 |
| **Message Queue** | Apache Kafka (event streaming) + Eclipse Mosquitto (MQTT for IoT) |
| **Object Storage** | MinIO (S3-compatible) |
| **Frontend** | Next.js 15, React 19, TypeScript, Tailwind CSS, Zustand, Recharts |
| **AI/ML** | LangGraph (agent orchestration), LiteLLM (LLM routing), Voyage AI (embeddings) |
| **Vision** | YOLO v8/v10, Vision Transformer (ViT), DeepSORT tracking, OpenCV |
| **Infrastructure** | Docker Compose, Kubernetes, Terraform |
| **Monitoring** | OpenTelemetry, Prometheus |

### Monorepo Structure
```
constructai/
├── apps/
│   ├── api/          # FastAPI backend (264 Python files)
│   └── web/          # Next.js frontend (64 TypeScript files)
├── packages/
│   └── shared-types/ # Shared TypeScript types
├── edge/             # Edge computing for IoT/cameras
├── ml/               # ML training pipelines
├── infra/            # Docker, Kubernetes, Terraform configs
├── demo/             # Synthetic data generators
└── docs/             # Documentation
```

### Database: 26 Domain Models
Projects, Organizations, Users, Documents, EVM Snapshots, EAC Forecasts, Schedule Risk Simulations, Change Orders, Cost Estimates, Cost Items, Estimate Line Items, Schedule Baselines, Schedule Activities, Price Forecasts, Inspections, Defect Reports, NCRs (Non-Conformance Reports), Compliance Checks, Safety Alerts, Safety Incidents, PPE Violations, Daily Logs, Cameras, Safety Zones, Teams, Workflows, Field Notes, Daily Reports, Equipment, Deliveries, Inventory, Feature Flags, Guardrail Logs, Conversation Memory, Tenant Config.

### API: 25 Route Files, ~100+ Endpoints
Controls, Estimating, Procurement, Quality, Scheduling, Safety, Productivity, Cameras, Zones, Documents, Communication, Evaluation, Feedback, Field Management, Logistics, Orchestrator, Teams, Users, Organizations, Projects, Admin, Auth, Password Reset, Portfolio, Health.

### AI Agent System: 11+ Specialized Agents
Orchestrated via LangGraph with PostgreSQL checkpointing:
- **Orchestrator Agent** — Routes requests to appropriate teams, manages workflow priority
- **Planning Team** — Estimating + Scheduling agents for cost/schedule planning
- **Execution Team** — Productivity + Logistics agents for field operations
- **Compliance Team** — Quality + Safety agents for regulatory adherence
- **Specialist Agents** — Controls, Estimating, Scheduling, Quality, Safety, Procurement, Productivity, Logistics, Communication, Document

---

## SERVICE MODULES — DETAILED INVENTORY

### 1. PROJECT CONTROLS

#### 1.1 Earned Value Management (EVM Engine)
- **What it does:** Calculates all standard EVM metrics — PV, EV, AC, SV, CV, SPI, CPI, EAC, ETC, VAC, TCPI, %Complete. Also computes Earned Schedule metrics (ES, SV(t), SPI(t), TCPI_BAC, TCPI_EAC, IEAC).
- **Data it uses:** Planned Value curves, Actual Cost data, earned value snapshots per reporting period.
- **Accuracy:** Uses Python `Decimal` type for financial precision. Mathematically verified against PMI PMBOK formulas.
- **Current gap:** Needs historical project performance data to validate forecasting accuracy. No benchmark dataset to compare predictions against actual project outcomes.

#### 1.2 EAC Forecaster
- **What it does:** Generates Estimate at Completion using multiple methods — Actual Cost, Percent Complete, CPI-based, composite. Produces confidence intervals using bootstrapped statistics from historical CPI/SPI variance.
- **Data it uses:** Historical CPI values from project snapshots, BAC.
- **Current gap:** Confidence intervals are only meaningful with 5+ historical data points. No pre-loaded industry benchmark data for typical CPI/SPI distributions by project type.

#### 1.3 Monte Carlo Schedule Risk Simulation
- **What it does:** Runs N-iteration schedule simulation using PERT Beta distributions (scipy.stats.beta) with proper topological sort for dependency ordering. Calculates P10/P50/P90 completion dates and per-activity criticality indices.
- **Data it uses:** Activity durations with optimistic/most_likely/pessimistic estimates, dependency network.
- **Accuracy:** Uses proper PERT Beta (not triangular). Topological sort ensures correct forward pass. Criticality calculated via full backward pass per iteration.
- **Current gap:** No correlation matrix between related activities (e.g., foundation delays correlating with structure delays). No historical calibration data for duration uncertainty ranges by activity type.

#### 1.4 S-Curve Generator
- **What it does:** Generates cumulative planned vs. actual performance curves with logistic function fitting for forecasting. Uses scipy.optimize.curve_fit with linear fallback.
- **Current gap:** Confidence bands are theoretical — need real project data to calibrate uncertainty.

#### 1.5 Change Order Analyzer
- **What it does:** AI-powered change order impact assessment with project-type-specific thresholds (commercial, infrastructure, residential). Classifies risk across cost, schedule, complexity dimensions. Recommends approve/negotiate/reject/escalate.
- **Data it uses:** Change order amount, original contract value, project type, schedule impact.
- **Current gap:** Thresholds are reasonable defaults but not calibrated from industry data. No historical change order database for pattern recognition.

### 2. COST ESTIMATING

#### 2.1 Parametric Cost Model
- **What it does:** XGBoost-based parametric estimator with fallback heuristic lookup. Features: building type, area, stories, location, quality level. Compound inflation adjustment.
- **Data it uses:** Building type base costs (only 4 types currently: commercial, residential, industrial, institutional), area-based scaling, story multiplier.
- **Current gap:** **CRITICAL** — Only 4 building subtypes with hardcoded base costs ($175-$300/sqft). No trained ML model — always falls through to heuristic. Needs historical project cost data for real model training. RSMeans or ENR data would transform accuracy.

#### 2.2 Cost Database (BLS PPI Integration)
- **What it does:** Fetches Bureau of Labor Statistics Producer Price Index data for construction materials. Maps CSI MasterFormat codes to BLS series IDs. Tracks price trends.
- **Data it uses:** 5 BLS PPI series (concrete, structural_steel, lumber, copper, asphalt). 17 cost items. CSI code prefix matching.
- **Current gap:** **CRITICAL** — Only 5 BLS series and 17 cost items. Real construction cost databases have 500+ items across all 50 CSI MasterFormat divisions. Falls back to mock data when BLS API key not configured. No RSMeans integration.

#### 2.3 Monte Carlo Cost Simulation
- **What it does:** Cost risk analysis using PERT Beta distribution sampling (with scipy fallback to triangular). Returns P10/P50/P80/P90 cost estimates, histogram data, sensitivity analysis with correlation coefficients.
- **Data it uses:** Line items with quantity, unit_cost, optional min/max bounds.
- **Current gap:** Default uncertainty range (±20%) is a blanket assumption. Needs material-specific uncertainty data (concrete ±8%, steel ±25%, labor ±15%, etc.).

#### 2.4 Quantity Extractor
- **What it does:** Parses CSV estimates and extracts quantities with unit conversion and duplicate detection.
- **Current gap:** No OCR capability for scanned documents. No IFC/BIM quantity takeoff integration.

### 3. PROCUREMENT

#### 3.1 Price Forecaster
- **What it does:** Ensemble ARIMA + Prophet time-series forecasting for construction material prices. Fetches historical data from FRED and BLS APIs. Returns forecasts with confidence intervals.
- **Data it uses:** FRED economic indicators, BLS PPI series.
- **Current gap:** Both FRED and BLS integrations fall back to synthetic data when API keys aren't configured. No commodity futures integration. No regional price adjustment.

#### 3.2 Contract Risk Analyzer
- **What it does:** LLM-powered contract clause analysis. Identifies 8 risk categories: liquidated damages, indemnification, scope creep, payment terms, change order process, dispute resolution, insurance, warranty. Scores severity as low/medium/high/critical.
- **Data it uses:** Contract text (up to 32K chars, chunked for longer documents).
- **Current gap:** No training data for construction-specific contract patterns. No clause library for benchmarking against industry standards (AIA, ConsensusDocs, EJCDC).

#### 3.3 Vendor Manager
- **What it does:** Multi-criteria vendor scoring (on-time delivery, quality, safety, financial stability, experience, price). Weighted composite score with recommendation thresholds.
- **Data it uses:** Vendor performance metrics, EMR/DART safety rates, financial data.
- **Current gap:** Weights are defaults, not calibrated from owner preferences. No OSHA inspection database integration for safety verification. No D&B integration for financial data.

#### 3.4 Procore Integration
- **What it does:** REST API client for Procore ERP system.
- **Current gap:** **100% mock implementation.** Returns synthetic data. Needs real Procore OAuth setup and API integration.

### 4. QUALITY

#### 4.1 Defect Classifier (Vision Transformer)
- **What it does:** ViT-based image classification for 12 construction defect types: crack_structural, crack_cosmetic, spalling, delamination, corrosion, water_damage, improper_alignment, missing_component, surface_defect, weld_defect, concrete_honeycombing, rebar_exposure.
- **Data it uses:** ImageNet pre-trained ViT with heuristic mapping to defect types (confidence capped at 0.4 without fine-tuning).
- **Current gap:** **CRITICAL** — No fine-tuned model. Using ImageNet class-to-defect heuristic mapping with max 40% confidence. Training pipeline exists (`ml/training/defect_classifier_train.py`) but needs labeled construction defect images. Datasets identified: Mendeley Concrete Crack Images (40K images), CODEBRIM, SDNET2018.

#### 4.2 Compliance Checker
- **What it does:** Checks against 40+ OSHA 1926 construction standards across 7 categories (general safety, scaffolding, fall protection, electrical, excavation, PPE, health services). Project-type-aware checklists. IBC basic checks.
- **Data it uses:** Hardcoded OSHA standard references with check functions.
- **Current gap:** No real-time OSHA violation database integration. No permit tracking integration. No ADA accessibility checks. Standards need domain expert review.

### 5. SCHEDULING

#### 5.1 CPM Engine (Critical Path Method)
- **What it does:** Full forward/backward pass with all 4 relationship types (FS, SS, FF, SF) and integer lag support. Calculates early/late start/finish, total float, and critical path. Cycle detection raises ValueError.
- **Data it uses:** Activity network with durations, dependencies, relationship types.
- **Accuracy:** Mathematically verified. Supports all standard relationship types.
- **Current gap:** No resource-loaded scheduling. No calendar support (non-work days, holidays). No Primavera P6 or MS Project direct import.

#### 5.2 DCMA 14-Point Schedule Health Assessment
- **What it does:** Evaluates schedule quality against Defense Contract Management Agency standards. 14 checks covering structure, logic, duration, constraints, resources, baseline, leads/lags.
- **Data it uses:** Schedule activities with relationships, durations, constraints.
- **Current gap:** Returns `insufficient_data` for checks when relationship detail isn't available. No benchmark data for typical DCMA scores by project type.

#### 5.3 Schedule Optimizer
- **What it does:** Resource leveling using heuristic algorithms. What-if scenario analysis. Builds daily resource usage profiles.
- **Current gap:** No cost-loaded scheduling. No multi-calendar support.

#### 5.4 Weather Service
- **What it does:** Assesses weather impact on schedule activities. Classifies impact levels. Computes delay probability.
- **Current gap:** **Falls back to mock weather data.** OpenWeatherMap API integration exists but needs API key configuration. No historical weather pattern analysis.

### 6. COMPUTER VISION (SAFETY)

#### 6.1 Object Detection Pipeline
- **What it does:** Full pipeline: YOLO detection → DeepSORT tracking → Zone enforcement → Temporal smoothing → Alert generation. Supports restricted zones, PPE zones, equipment-only zones. Real-time processing from RTSP/HTTP camera streams.
- **Models:** YOLO v8/v10 (Ultralytics), RTMDet (MMEngine), ViT for defects
- **Current gap:** No pre-trained construction safety model. Using general YOLO weights. Needs fine-tuning on construction site imagery (hard hats, vests, workers, equipment). No activity recognition model trained.

#### 6.2 Edge Computing
- **What it does:** On-device inference pipeline for cameras/IoT sensors. MQTT communication to backend. Model quantization and deployment.
- **Current gap:** Edge deployment configs are templates, not production-tested.

### 7. RAG (RETRIEVAL-AUGMENTED GENERATION)

#### 7.1 Document Processing Pipeline
- **What it does:** PDF extraction (PyMuPDF + pdfplumber), IFC/BIM parsing, Primavera XER/XML schedule parsing, smart document chunking with overlap.
- **Current gap:** No OCR for scanned documents. IFC parser is basic. No dwg/dxf support.

#### 7.2 Embeddings & Retrieval
- **What it does:** Voyage AI embeddings (1024-dim), hybrid search (vector similarity + BM25 keyword), cross-encoder reranking (Cohere), project-scoped results.
- **Current gap:** No construction-specific embedding model fine-tuning. No evaluation metrics (RAGAS) benchmarked on construction documents.

#### 7.3 Answer Generation
- **What it does:** LLM-based answer generation with source citations, document type weighting (specs > compliance > schedule > budget), quality scoring.
- **Current gap:** No construction terminology fine-tuning. No domain-specific prompt templates.

### 8. COMMUNICATION

- **RFI Helper** — RFI routing, tracking, response templates
- **Report Generator** — PDF report generation (ReportLab)
- **Transcriber** — Audio transcription (Faster-Whisper)
- **Current gap:** No email/notification integration. No mobile push notifications.

### 9. LOGISTICS

- **Delivery Router** — Route optimization using OR-Tools (VRP solver)
- **Equipment Tracker** — Location, utilization, availability prediction
- **Site Layout** — Geospatial constraints using Shapely
- **Simulation** — Discrete-event simulation using SimPy
- **Current gap:** No GPS/telematics integration. No equipment OEM API connections. No material supplier portal integration.

### 10. PRODUCTIVITY

- **Activity Recognizer** — Construction activity classification from video/sensor data
- **Productivity Forecaster** — Crew productivity prediction with condition adjustments
- **Telemetry Ingestor** — IoT sensor data ingestion and time-series storage
- **Current gap:** No trained activity recognition model. No benchmark productivity rates by trade.

### 11. RELIABILITY & OBSERVABILITY

- **LLM Gateway** — LiteLLM-based routing with provider fallback chains (OpenAI → Anthropic → Cohere), circuit breakers, cost tracking, usage logging
- **Circuit Breaker** — Per-provider circuit breakers (closed → open → half-open)
- **Degradation Manager** — Graceful feature reduction when services fail
- **Semantic Cache** — LLM response caching via embedding similarity
- **Offline Sync** — SQLite fallback for offline-first operation

### 12. GUARDRAILS & SAFETY

- **Domain Rules** — Construction-specific validation (schedule feasibility, budget limits, safety requirements)
- **Knowledge Verifier** — Fact-checking against knowledge base
- **Confidence Scorer** — LLM response confidence scoring
- **Hallucination Prevention** — NeMo Guardrails integration (Colang config)

### 13. MLOps

- **Model Registry** — Model versioning and promotion
- **Retraining Pipeline** — Automated retraining with drift detection
- **Canary Deployer** — Canary deployment with metric monitoring
- **Active Learning** — Uncertainty sampling for training data collection

---

## CURRENT DATA SOURCES & GAPS

### APIs Currently Integrated (but most need API keys to activate)
| API | Service | Status |
|-----|---------|--------|
| BLS Public API v2 | Cost Database, Price Forecaster | Falls back to mock without `BLS_API_KEY` |
| FRED API | Price Forecaster | Falls back to mock without `FRED_API_KEY` |
| OpenWeatherMap | Weather Service | Falls back to mock without `OPENWEATHERMAP_API_KEY` |
| OpenAI API | Contract Risk, RAG, Agents | Required for LLM features |
| Anthropic API | LLM Gateway fallback | Optional fallback |
| Voyage AI | RAG Embeddings | Required for document search |
| Cohere API | RAG Reranking | Optional but recommended |
| Procore API | Vendor/Project sync | **100% mock — not functional** |

### Data We Have
- 25 BLS PPI series IDs mapped for construction materials (fetcher script built)
- 17 construction cost items with CSI codes
- 40+ OSHA 1926 standard references
- 12 defect classification types defined
- Synthetic demo data generators (cost, schedule, documents, telemetry)
- ImageNet pre-trained ViT weights (not fine-tuned for construction)
- General YOLO weights (not fine-tuned for construction safety)

### Data We Need
| Category | What's Missing | Impact |
|----------|---------------|--------|
| **Cost Data** | RSMeans or equivalent unit cost database (500+ items) | Estimating accuracy |
| **Historical Projects** | Real project cost/schedule outcomes for model training | Parametric model, EAC forecasting |
| **Construction Images** | Labeled defect images (cracks, spalling, corrosion) | Defect classifier accuracy |
| **Safety Images** | Labeled construction site images (PPE, workers, equipment) | Safety detection accuracy |
| **Material Prices** | 10+ years of commodity prices (steel, concrete, lumber) | Price forecasting accuracy |
| **Labor Rates** | Regional labor rates by trade and union/non-union | Estimating completeness |
| **Weather History** | Historical weather data by region for delay modeling | Schedule risk accuracy |
| **Contract Corpus** | Sample construction contracts for risk analysis training | Contract risk accuracy |
| **Productivity Rates** | RS Means or industry benchmark productivity rates | Productivity forecasting |
| **Equipment Rates** | Equipment rental rates by type and region | Logistics/estimating |

---

## KNOWN STUBS & MOCK IMPLEMENTATIONS

These components return synthetic/mock data and need real implementation:

1. **Procore Client** (`procore_client.py`) — Entire module is mock. Returns fabricated vendor/project data.
2. **Weather Service** (`weather_service.py`) — Falls back to `_generate_mock_weather()` without API key.
3. **Price Forecaster** (`price_forecaster.py`) — Falls back to `_generate_fallback_ppi_series()` without API keys.
4. **Admin Endpoints** (`admin.py`) — Stub implementations for system config.
5. **Feedback Endpoints** (`feedback.py`) — Stub implementations for feedback collection.
6. **Evaluation Endpoints** (`evaluation.py`) — Stub implementations for model evaluation results.
7. **Query Optimizer** (`query_optimizer.py`) — Placeholder for pg_stat_statements integration.
8. **Alerting Service** (`alerting.py`) — Placeholder threshold checking.

---

## TEST COVERAGE

- **647 tests passing** (0 failures, 36 warnings)
- Test categories: Unit tests for all algorithms, integration tests for API endpoints, vision pipeline tests, scheduling engine tests, EVM calculation tests, Monte Carlo simulation tests, security tests
- Load testing framework exists (Locust) but not regularly executed
- No end-to-end tests with real database
- No performance benchmarks established

---

## SECURITY POSTURE

Completed 2 rounds of security audits (95 total issues fixed):
- JWT authentication with refresh tokens
- Password hashing (bcrypt)
- Rate limiting middleware
- Security headers (HSTS, CSP, X-Frame-Options)
- Input validation (Pydantic schemas with bounds)
- SQL injection prevention (SQLAlchemy parameterized queries)
- CORS configuration
- Request logging with sanitized headers
- Tenant isolation middleware
- Encryption key management

**Known gaps:** No RBAC enforcement beyond basic auth. No email verification flow. No MFA. No audit logging to external SIEM.

---

## INFRASTRUCTURE

### Development
- Docker Compose with PostgreSQL, Redis, Kafka, MinIO, Mosquitto
- Turborepo for monorepo task orchestration
- Alembic for database migrations
- Husky for git hooks

### Production (configured but not deployed)
- Kubernetes manifests (API deployment, ingress, services)
- Terraform for cloud provisioning
- Docker production builds with multi-stage Dockerfile
- Health check and readiness probe endpoints

---

## COMPETITIVE LANDSCAPE

Our platform competes with:
- **Procore** — Market leader, full construction management suite
- **Autodesk Construction Cloud (ACC)** — BIM-centric, strong document management
- **PlanGrid** (now part of ACC) — Field-focused, markup/annotation
- **Oracle Primavera** — Enterprise scheduling and controls
- **Buildertrend** — Residential/small commercial
- **Sage 300 CRE** — Accounting-focused
- **CMiC** — ERP for large contractors
- **InEight** — Estimating and controls

**Our differentiator:** AI-native platform with 11 specialized agents, real-time computer vision for safety, RAG-powered document intelligence, Monte Carlo risk simulation, and automated EVM/earned schedule analytics. No competitor offers this level of AI integration across all construction domains.

**Our weakness:** We lack the mature, battle-tested basic workflows (daily logs, RFIs, submittals, punch lists, pay applications) that practitioners use every day. The AI features are advanced but the construction management fundamentals need to match industry expectations.

---

## WHAT WE NEED HELP FINDING

1. **Free/affordable construction cost datasets** that can replace our 17-item database with 500+ items
2. **Construction defect image datasets** for fine-tuning our ViT classifier
3. **Construction site safety image datasets** for fine-tuning YOLO
4. **Historical project performance data** for training the parametric cost model
5. **APIs for real-time material pricing** beyond BLS (commodity exchanges, distributor APIs)
6. **Regional labor rate databases** (Davis-Bacon, union scales)
7. **Equipment rental rate APIs** (United Rentals, Sunbelt, Cat Rental)
8. **Permit and inspection databases** by jurisdiction
9. **Standard construction contract templates** (AIA, ConsensusDocs) for risk benchmarking
10. **Construction productivity rate databases** for baseline comparisons
11. **Any other datasets, APIs, or data sources** that would make this system produce trustworthy, practitioner-grade results from day one
