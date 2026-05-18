# Local Development Setup

## Prerequisites
- Python 3.11+
- Node.js 20+
- Docker and Docker Compose
- Git

## Quick Start

### 1. Clone and Install
```bash
git clone https://github.com/constructai/constructai.git
cd constructai
```

### 2. Start Infrastructure
```bash
cd infra
docker compose up -d
# Starts: PostgreSQL, Redis, Kafka, MinIO, Mosquitto, MediaMTX
```

### 3. Backend Setup
```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### 4. Frontend Setup
```bash
cd apps/web
npm install
npm run dev
# Visit http://localhost:3000
```

### 5. Verify
```bash
curl http://localhost:8000/api/v1/health
# {"status": "healthy"}
```

## Running Tests
```bash
cd apps/api
pytest tests/ -v
```

## Common Issues
- **Port conflicts**: Check that ports 5432, 6379, 9092, 9000, 8000, 3000 are available
- **Database migrations**: Run `alembic upgrade head` after pulling new changes
- **Missing deps**: Run `pip install -e ".[dev]"` to update dependencies
