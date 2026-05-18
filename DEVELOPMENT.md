# Development Guide

## Phase-by-Phase Build Plan

### Phase 0: Project Bootstrap (Week 1) - COMPLETE

- Monorepo scaffolding with Turborepo
- FastAPI backend with async SQLAlchemy
- Next.js 15 frontend with Tailwind CSS
- PostgreSQL 17 with TimescaleDB, pgvector, PostGIS
- Redis, Kafka, MinIO, Mosquitto infrastructure
- CI/CD with GitHub Actions
- Auth system (JWT, bcrypt)
- CRUD for organizations, users, projects

### Phase 1: Document & Knowledge Engine (Weeks 2-4)

- Document upload to MinIO (PDF, DOCX, XLSX, DWG)
- PDF parsing with PyMuPDF + pdfplumber
- CSI-aware document chunking (<=600 tokens)
- Voyage-3-large embeddings stored in pgvector
- Hybrid RAG retrieval (BM25 + vector + Cohere reranker)
- Document Agent for Q&A with citations

### Phase 2: Pre-Construction Agents (Weeks 5-8)

- Estimating Agent: quantity takeoffs, cost databases, bid analysis
- Scheduling Agent: CPM generation, resource leveling, what-if scenarios
- Logistics Agent: site logistics, delivery scheduling, material tracking
- Procurement Agent: RFQ generation, bid comparison, vendor management

### Phase 3: Real-Time Vision Pipeline (Weeks 9-12)

- Video ingestion from job site cameras
- YOLO-based object detection (PPE, equipment, hazards)
- Safety Agent: real-time violation detection, alerts
- Edge deployment with MQTT for low-latency inference

### Phase 4: Construction Phase Agents (Weeks 13-16)

- Project Controls Agent: earned value analysis, forecasting
- Quality Agent: inspection workflows, defect tracking
- Productivity Agent: labor tracking, efficiency metrics
- Communication Agent: meeting summaries, RFI drafting

### Phase 5: Orchestration & Intelligence (Weeks 17-20)

- Orchestrator Agent: multi-agent coordination via LangGraph
- Guardrails: cost limits, safety thresholds, approval workflows
- Memory: conversation history, project context persistence
- Reliability: retry logic, fallback chains, circuit breakers

### Phase 6: Production Hardening (Weeks 21-24)

- Performance: connection pooling, caching, query optimization
- Security: row-level security, audit logging, encryption
- Multi-tenancy: org isolation, subscription tiers
- MLOps: model versioning, A/B testing, monitoring
- Observability: structured logging, metrics, tracing

## Directory Structure

```
constructai/
├── apps/
│   ├── api/                    # FastAPI backend
│   │   ├── alembic/            # Database migrations
│   │   ├── app/
│   │   │   ├── api/v1/         # API route handlers
│   │   │   ├── models/         # SQLAlchemy models
│   │   │   ├── schemas/        # Pydantic request/response schemas
│   │   │   ├── services/       # Business logic layer
│   │   │   └── utils/          # Shared utilities
│   │   └── tests/              # Pytest test suite
│   └── web/                    # Next.js frontend
│       ├── src/
│       │   ├── app/            # Next.js App Router pages
│       │   ├── components/     # Reusable React components
│       │   ├── lib/            # Client utilities
│       │   └── types/          # TypeScript type definitions
│       └── tests/              # Vitest test suite
├── packages/
│   └── shared-types/           # Shared TypeScript types
└── infra/                      # Docker Compose, init scripts
```

## Adding a New Agent

To add a new AI agent (e.g., "Safety Agent"):

1. **Create the model** in `apps/api/app/models/safety.py`
2. **Create schemas** in `apps/api/app/schemas/safety.py`
3. **Create the service** in `apps/api/app/services/safety.py`
4. **Create API routes** in `apps/api/app/api/v1/safety.py`
5. **Register routes** in `apps/api/app/api/router.py`
6. **Write tests** in `apps/api/tests/test_safety.py`
7. **Create migration** with `cd apps/api && alembic revision --autogenerate -m "add safety tables"`
8. **Run migration** with `cd apps/api && alembic upgrade head`

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://constructai:constructai@localhost:5432/constructai` |
| `DATABASE_URL_SYNC` | Sync PostgreSQL connection string (for Alembic) | `postgresql://constructai:constructai@localhost:5432/constructai` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Secret key for JWT tokens | `dev-secret-...` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token lifetime | `7` |
| `S3_ENDPOINT_URL` | MinIO/S3 endpoint | `http://localhost:9000` |
| `S3_ACCESS_KEY` | MinIO/S3 access key | `minioadmin` |
| `S3_SECRET_KEY` | MinIO/S3 secret key | `minioadmin` |
| `S3_BUCKET_DOCUMENTS` | Document storage bucket | `constructai-documents` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka bootstrap servers | `localhost:29092` |
| `MQTT_BROKER_HOST` | MQTT broker host | `localhost` |
| `MQTT_BROKER_PORT` | MQTT broker port | `1883` |
| `NEXT_PUBLIC_API_URL` | Frontend API base URL | `http://localhost:8000` |

## Database Migrations

```bash
# Run all pending migrations
cd apps/api && alembic upgrade head

# Create a new migration
cd apps/api && alembic revision --autogenerate -m "description"

# Downgrade one step
cd apps/api && alembic downgrade -1

# View current revision
cd apps/api && alembic current
```
