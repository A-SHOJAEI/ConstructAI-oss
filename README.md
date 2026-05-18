# ConstructAI

**AI-powered construction management platform — source-available**

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm--NC%201.0.0-blue.svg)](./LICENSE)
[![Commercial Licensing](https://img.shields.io/badge/Commercial-Contact%20Required-orange.svg)](./COMMERCIAL.md)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Node 20+](https://img.shields.io/badge/Node-20%2B-green.svg)](https://nodejs.org/)

ConstructAI is a full-lifecycle construction management platform powered by a coordinated multi-agent AI system. It covers pre-construction through closeout: document management, estimating, scheduling, safety monitoring (computer vision), quality control (defect classification), submittals, RFIs, change orders, daily logs, EVM-based cost controls, and more.

The platform was built to run **on-prem** on NVIDIA DGX-class hardware with local LLM inference (vLLM + Ollama), but it also runs comfortably on a developer laptop with cloud LLM fallback.

---

## What's in the box

| Layer | Description | Stack |
|-------|-------------|-------|
| **Web client** | Operator dashboard, mobile-responsive | Next.js 15, React 19, TanStack Query, Zustand, Tailwind CSS, Radix UI |
| **API** | REST + WebSocket, multi-tenant, RLS-enforced | FastAPI, async SQLAlchemy 2.0, Pydantic v2, JWT auth |
| **Domain services** | Estimating, scheduling, safety, quality, RFIs, submittals, controls, cash flow | Python 3.12, asyncpg |
| **Agent orchestration** | 11 coordinated AI agents (RFI resolution, safety, estimating, etc.) | LangGraph, multi-agent workflows |
| **LLM gateway** | 2-tier local-first with cloud fallback, circuit-broken, budget-tracked | LiteLLM, vLLM (OpenAI-compatible), Ollama |
| **RAG pipeline** | Spec-aware chunking, RFI similarity search, OSHA knowledge base | pgvector, Voyage AI, optional fine-tuned BGE |
| **Computer vision** | PPE + worker detection, defect classification | YOLOv8, ViT, ONNX, optional TensorRT |
| **Edge** | IoT, camera feeds, Jetson inference | MQTT, Kafka, MediaMTX (RTMP) |

### Infrastructure (Docker Compose)

- PostgreSQL 17 with TimescaleDB + pgvector + PostGIS
- Redis 7.4
- Apache Kafka (KRaft mode, no Zookeeper)
- MinIO (S3-compatible object storage)
- Eclipse Mosquitto (MQTT broker)
- MediaMTX (RTMP for camera streams)

---

## Quick start

### Prerequisites

- Docker and Docker Compose v2
- Node.js ≥ 20.0.0, npm ≥ 10.8.2
- Python ≥ 3.12
- ~16 GB RAM for the full Docker stack (less if you skip Kafka/MediaMTX)
- GPU optional — required only for local LLM inference and on-prem ML training

### Setup

```bash
# 1. Copy the env template and fill in secrets
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, JWT_SECRET_KEY, ENCRYPTION_KEY, etc.
# Generate strong values for production:
#   openssl rand -hex 32           # JWT_SECRET_KEY
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY

# 2. Install Python venv + npm dependencies
make setup

# 3. Bring up infrastructure
make start

# 4. Run database migrations
make migrate

# 5. Seed demo data (optional)
make seed

# 6. Start backend (port 8000)
make dev-backend

# 7. Start frontend in a separate terminal (port 3000)
make dev-frontend
```

### Verify

| Service | URL | Expected |
|---|---|---|
| Frontend | http://localhost:3000 | Login page |
| API docs (Swagger) | http://localhost:8000/docs | FastAPI interactive UI |
| API docs (ReDoc) | http://localhost:8000/redoc | Static API reference |
| Health check | http://localhost:8000/api/v1/health | `{"status": "healthy"}` |

### LLM backends

By default the LLM gateway is set up for **2 local providers** in a primary/secondary topology. To enable cloud fallback set `LLM_LEGACY_CLOUD_FALLBACK=1` and provide `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` in `.env`.

| Tier | Provider | Default endpoint | Use case |
|---|---|---|---|
| Primary (reasoning) | vLLM (OpenAI-compatible) | `http://localhost:8000/v1` | Heavy reasoning, drafting, agentic flows |
| Secondary (fast) | Ollama (OpenAI-compatible) | `http://localhost:11434/v1` | Classification, summarization, fast routing |

The model registry, circuit breakers, and pricing tables live in `apps/api/app/services/reliability/llm_gateway.py`.

### Computer vision models

CV models are **not** committed to the repo (they're 20+ GB). The defaults expected by the app:

- `models/safety_yolo_v1.0/best.pt` — PPE & worker detection (YOLOv8-L)
- `models/defect_vit_v1.1/best_model.pth` — 8-class defect classification (ViT-B/16)
- `models/construction-bge-large/` — fine-tuned construction embeddings (optional; falls back to Voyage AI)

To train your own (with the included pipelines):

```bash
python ml/training/train_safety_yolo.py --dataset-dir constructai-data/safety
python ml/training/train_defect_vit.py --dataset-dir constructai-data/defects
python -m apps.api.app.ml.training.construction_embeddings \
    --ifc-dir constructai-data/ifc-bim/ifc-bim-qa \
    --osha-xml constructai-data/osha/cfr-title29-chapterXVII.xml
```

---

## Testing

```bash
make test              # Backend + frontend
make test-backend      # pytest --cov=app (requires Docker services up)
make test-frontend     # vitest unit tests

cd apps/web && npx playwright test     # end-to-end (Playwright)
```

Backend tests require PostgreSQL and Redis to be running — `make start` brings them up.

---

## Project structure

```
constructai/
├── apps/
│   ├── api/                FastAPI backend (Python 3.12, async)
│   │   ├── app/
│   │   │   ├── api/v1/     65+ REST routes by domain
│   │   │   ├── models/     SQLAlchemy ORM (50+ tables, RLS-enforced)
│   │   │   ├── schemas/    Pydantic request/response
│   │   │   ├── services/   Domain logic (agents/, rag/, safety/, ...)
│   │   │   ├── middleware/ Rate limiting, CSRF, audit, security headers
│   │   │   └── workers/    Celery async jobs
│   │   ├── alembic/        Async migrations (40+)
│   │   ├── ml/training/    Construction embedding fine-tuning
│   │   └── tests/          pytest-asyncio
│   └── web/                Next.js 15 frontend (App Router, RSC)
├── packages/
│   └── shared-types/       Shared TypeScript types
├── ml/
│   └── training/           CV training pipelines (YOLO, ViT)
├── edge/                   Jetson inference, IoT, camera capture
├── infra/                  Docker Compose, Terraform, K8s manifests, monitoring
├── docs/                   Architecture, runbooks, ADRs, API reference
├── bin/                    Demo helpers, track switcher
└── scripts/                Smoke tests, utilities
```

---

## Documentation

- [Development guide](DEVELOPMENT.md) — local setup, common workflows
- [Testing guide](TESTING.md) — testing strategy, coverage, e2e
- [Contributing](CONTRIBUTING.md) — pull request workflow, conventions
- [Security policy](SECURITY.md) — how to report vulnerabilities
- [System overview](docs/CONSTRUCTAI_SYSTEM_OVERVIEW.md)
- [API reference](docs/API_REFERENCE.md)
- [Architecture decisions](docs/adr/) — ADRs for database, agent framework, edge hardware, etc.
- [Runbooks](docs/runbooks/) — local dev, edge deployment, JWT rotation, prod deploy

---

## License

ConstructAI is distributed under the **[PolyForm Noncommercial 1.0.0](./LICENSE)** license. It is **source-available**, not open source by the OSI definition — commercial use requires a separate license.

You may **freely**:

- Use, modify, and run ConstructAI for personal research, learning, or hobby projects
- Use it within charities, research, or educational settings
- Fork it, modify it, and redistribute under the same terms
- Contribute back via pull requests

You **need a commercial license** if your organization uses ConstructAI to:

- Power a paid product or service
- Run it as part of for-profit business operations
- Embed it in a commercial offering

See [COMMERCIAL.md](./COMMERCIAL.md) for how to obtain a commercial license — email **shojaei@vt.edu**.

---

## Contributing

We welcome bug reports, documentation improvements, and pull requests. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the workflow. By contributing, you agree your work is licensed under the PolyForm-NC terms and that the maintainer may also relicense it commercially.

## Security

If you find a vulnerability, **please do not file a public issue**. Email **shojaei@vt.edu** — see [SECURITY.md](./SECURITY.md) for the disclosure policy.

## Acknowledgements

ConstructAI uses many great open-source projects, including: FastAPI, Next.js, PostgreSQL, pgvector, TimescaleDB, LangGraph, LiteLLM, vLLM, Ollama, YOLOv8, Hugging Face Transformers, Voyage AI embeddings, Apache Kafka, MinIO, Mosquitto, and Eclipse Foundation tooling.
