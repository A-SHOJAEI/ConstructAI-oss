# Getting Started with ConstructAI

## Prerequisites

- **Docker** and **Docker Compose** (v2.20+)
- **Python 3.12+** (for local API development)
- **Node.js 20+** and **npm** (for frontend development)
- **Java 17+ JRE** (required for MPXJ schedule import)

## 1. Clone and Configure

```bash
git clone <repo-url> constructai
cd constructai
cp .env.example .env
```

Edit `.env` and set the required values:

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | Database password | `your-secure-password` |
| `JWT_SECRET_KEY` | JWT signing key (32+ chars) | `$(openssl rand -hex 32)` |
| `ENCRYPTION_KEY` | Fernet key for token encryption | `$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")` |

### External API Keys

| Variable | Service | Purpose | Required? |
|----------|---------|---------|-----------|
| `OPENAI_API_KEY` | OpenAI | LLM for intelligence briefs, RFI resolution, bid scoring | Yes (for AI features) |
| `ANTHROPIC_API_KEY` | Anthropic | LLM fallback (via LiteLLM) | Optional |
| `FRED_API_KEY` | Federal Reserve (FRED) | Material price forecasting | Optional |
| `BLS_API_KEY` | Bureau of Labor Statistics | Labor cost indices (PPI/wage) | Optional |
| `OPENWEATHERMAP_API_KEY` | OpenWeatherMap | Weather impact analysis (backup provider) | Optional |
| `VOYAGE_API_KEY` | Voyage AI | Embedding fallback (if no local BGE model) | Optional |

### Procore Integration

| Variable | Description |
|----------|-------------|
| `PROCORE_CLIENT_ID` | OAuth app client ID from Procore Developer Portal |
| `PROCORE_CLIENT_SECRET` | OAuth app secret |
| `PROCORE_REDIRECT_URI` | Callback URL (default: `http://localhost:3000/api/procore/callback`) |
| `PROCORE_BASE_URL` | API base (sandbox: `https://sandbox.procore.com`) |
| `PROCORE_LOGIN_BASE_URL` | Login base (sandbox: `https://login-sandbox.procore.com`) |

### S3 / MinIO

| Variable | Default | Description |
|----------|---------|-------------|
| `S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO endpoint |
| `S3_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `S3_SECRET_KEY` | `changeme` | MinIO secret key |
| `S3_BUCKET_NAME` | `constructai` | Storage bucket name |

## 2. Start Infrastructure

```bash
cd infra
docker compose up -d
```

This starts:
- **TimescaleDB** (PostgreSQL 17) on port 5432
- **Redis** on port 6379
- **Kafka** (KRaft mode) on port 9092 / 29092
- **MinIO** (S3-compatible) on port 9000 (API) / 9001 (console)
- **Mosquitto** (MQTT) on port 1883
- **MediaMTX** (RTSP) on port 8554

## 3. Start the Full Stack

```bash
# From the infra directory — starts everything
docker compose --profile app up -d
```

Or start individual services:

```bash
# API server
docker compose up -d api

# Frontend
docker compose up -d web

# Background workers
docker compose up -d celery-worker celery-beat

# Kafka event consumer (Procore webhooks)
docker compose up -d kafka-consumer
```

## 4. Local Development (without Docker)

### Backend API

```bash
cd apps/api

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"

# Run database migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd apps/web

# Install dependencies
npm install

# Start dev server
npm run dev
```

The frontend will be available at `http://localhost:3000`.

### Celery Workers

```bash
cd apps/api

# Worker (processes background tasks)
celery -A app.workers.document_worker worker --loglevel=info --concurrency=4

# Beat (schedules recurring tasks)
celery -A app.workers.document_worker beat --loglevel=info
```

## 5. Create Your First Project

### Register and authenticate

```bash
# Register a user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "pm@example.com",
    "password": "SecurePass123!",
    "full_name": "Project Manager"
  }'

# Login to get JWT token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "pm@example.com", "password": "SecurePass123!"}'
# Save the returned access_token as $TOKEN
```

### Create an organization and project

```bash
# Create organization
curl -X POST http://localhost:8000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Construction", "industry": "commercial"}'

# Create project
curl -X POST http://localhost:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Downtown Office Tower",
    "organization_id": "<org_id>",
    "location": "Austin, TX",
    "budget": 25000000,
    "start_date": "2025-03-01",
    "end_date": "2026-12-31"
  }'
```

## 6. Connect Procore

1. **Create a Procore Developer App** at [developer.procore.com](https://developers.procore.com)
2. Set the redirect URI to match `PROCORE_REDIRECT_URI`
3. Add `PROCORE_CLIENT_ID` and `PROCORE_CLIENT_SECRET` to `.env`

```bash
# Get OAuth connect URL
curl http://localhost:8000/api/v1/integrations/procore/connect \
  -H "Authorization: Bearer $TOKEN"
# Open the returned URL in a browser to authorize

# Check connection status
curl http://localhost:8000/api/v1/integrations/procore/status \
  -H "Authorization: Bearer $TOKEN"

# Sync project data from Procore
curl -X POST http://localhost:8000/api/v1/integrations/procore/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<project_id>", "procore_project_id": "<procore_id>"}'
```

## 7. Import a P6 Schedule

ConstructAI supports Primavera P6 XML (.xml) and XER (.xer) schedule files via MPXJ.

```bash
curl -X POST http://localhost:8000/api/v1/scheduling/import \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@schedule.xml" \
  -F "project_id=<project_id>"
```

Once imported, you can:
- Run **Monte Carlo simulation** for schedule risk analysis
- Compute **EVM metrics** with earned value tracking
- View the **S-curve** and **critical path**

## 8. Safety Cameras

ConstructAI processes RTSP camera feeds for safety compliance (PPE detection, hazard zones).

```bash
# Register a camera
curl -X POST http://localhost:8000/api/v1/cameras \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project_id>",
    "name": "Gate Camera 1",
    "rtsp_url": "rtsp://mediamtx:8554/gate1",
    "zone_id": "<zone_id>"
  }'
```

Detection results are published via MQTT to `constructai/safety/<project_id>/detections`.

## 9. Bid History Import

Import historical bid data from CSV for the AI scoring engine:

```bash
curl -X POST http://localhost:8000/api/v1/orgs/<org_id>/bid-opportunities/import-csv \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@bid_history.csv"
```

CSV format:
```csv
name,estimated_value,bid_type,delivery_method,status,win_probability
Downtown Tower,25000000,competitive,design_build,won,0.85
Hospital Wing,18000000,negotiated,cm_at_risk,lost,0.35
```

## 10. Running Tests

```bash
cd apps/api

# Run all tests
pytest -v

# Run E2E workflow test
pytest tests/e2e/test_full_workflow.py -v

# Run with coverage
pytest --cov=app --cov-report=html

# Frontend tests
cd ../web
npm test
```

## 11. Key URLs

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | http://localhost:3000 | Next.js dashboard |
| API | http://localhost:8000 | FastAPI backend |
| API Docs | http://localhost:8000/docs | Swagger UI |
| MinIO Console | http://localhost:9001 | S3 storage browser |
| Kafka | localhost:29092 | Kafka broker (host) |

## Architecture Overview

```
                    ┌──────────────┐
                    │   Next.js    │ :3000
                    │   Frontend   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   FastAPI    │ :8000
                    │   Backend    │
                    └──┬───┬───┬───┘
                       │   │   │
          ┌────────────┤   │   ├────────────┐
          │            │   │   │            │
   ┌──────▼──────┐  ┌──▼───▼──┐  ┌────────▼────────┐
   │ TimescaleDB │  │  Redis  │  │     MinIO        │
   │  (pgvector) │  │ (cache/ │  │  (S3 storage)    │
   │             │  │  broker)│  │                  │
   └─────────────┘  └────┬───┘  └──────────────────┘
                         │
                  ┌──────▼───────┐
                  │    Celery    │
                  │ Worker/Beat  │
                  └──────────────┘

   ┌──────────┐   ┌──────────┐   ┌──────────────┐
   │  Kafka   │   │Mosquitto │   │  MediaMTX     │
   │ (events) │   │  (MQTT)  │   │  (RTSP)       │
   └──────────┘   └──────────┘   └──────────────┘
```
