# Developer Environment Setup

## Required Tools
- Python 3.11+ (3.12 recommended)
- Node.js 20+ (LTS)
- Docker Desktop 4.x
- Git 2.40+
- VS Code or PyCharm

## Recommended VS Code Extensions
- Python (ms-python)
- Ruff (charliermarsh.ruff)
- Pylance (ms-python.vscode-pylance)
- ESLint (dbaeumer.vscode-eslint)
- Tailwind CSS IntelliSense
- Docker (ms-azuretools.vscode-docker)

## Environment Variables
Create `apps/api/.env`:
```
DATABASE_URL=postgresql+asyncpg://constructai:constructai@localhost:5432/constructai
REDIS_URL=redis://localhost:6379/0
JWT_SECRET_KEY=dev-secret-change-in-production-minimum-32-chars
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
KAFKA_BOOTSTRAP_SERVERS=localhost:29092
```

## First-Time Setup
```bash
# 1. Clone
git clone https://github.com/constructai/constructai.git
cd constructai

# 2. Infrastructure
cd infra && docker compose up -d && cd ..

# 3. Backend
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
pytest tests/ -v  # Verify all tests pass

# 4. Frontend
cd ../web && npm install && npm run dev
```
