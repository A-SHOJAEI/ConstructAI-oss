#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "============================================"
echo "  ConstructAI Demo Setup"
echo "============================================"
echo ""

# 1. Check prerequisites
echo "[1/8] Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker required. Install from https://docker.com"; exit 1; }
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || { echo "ERROR: Python 3.12+ required"; exit 1; }
PYTHON=$(command -v python3 2>/dev/null || command -v python)
echo "       Docker:  $(docker --version | head -1)"
echo "       Python:  $($PYTHON --version)"
echo "       OK"

# 2. Start infrastructure
echo ""
echo "[2/8] Starting infrastructure (postgres, redis, kafka, minio, mosquitto)..."
cd "${PROJECT_ROOT}/infra"
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
cd "${PROJECT_ROOT}"
echo "       Waiting for services to be healthy..."
sleep 15

# Wait for postgres specifically
for i in $(seq 1 30); do
    if docker exec constructai-postgres pg_isready -U constructai > /dev/null 2>&1; then
        echo "       PostgreSQL ready"
        break
    fi
    sleep 2
done
echo "       OK"

# 3. Install dependencies
echo ""
echo "[3/8] Installing Python dependencies..."
cd "${PROJECT_ROOT}/apps/api" && pip install -e ".[dev]" -q 2>/dev/null || pip install -e ".[dev]"
cd "${PROJECT_ROOT}"
echo "       OK"

# 4. Run migrations
echo ""
echo "[4/8] Running database migrations (001-007)..."
cd "${PROJECT_ROOT}/apps/api" && alembic upgrade head
cd "${PROJECT_ROOT}"
echo "       OK"

# 5. Download models
echo ""
echo "[5/8] Downloading ML models for CPU inference..."
bash "${PROJECT_ROOT}/demo/models/download_models.sh"
echo "       OK"

# 6. Generate synthetic documents
echo ""
echo "[6/8] Generating synthetic construction documents..."
cd "${PROJECT_ROOT}"
$PYTHON -m demo.generators.generate_spec_pdf demo/output/specs 2>/dev/null || echo "       (spec generation skipped - install reportlab)"
$PYTHON -m demo.generators.generate_cost_data demo/output/cost_data.csv 2>/dev/null || true
$PYTHON -m demo.generators.generate_daily_report_pdf demo/output/daily_report.pdf 2>/dev/null || true
echo "       OK"

# 7. Seed demo data
echo ""
echo "[7/8] Seeding demo data (Riverside Mixed-Use Development)..."
cd "${PROJECT_ROOT}/apps/api" && $PYTHON -m demo.seed.seed_all
cd "${PROJECT_ROOT}"
echo "       OK"

# 8. Start application
echo ""
echo "[8/8] Starting application..."
cd "${PROJECT_ROOT}/apps/api" && uvicorn app.main:app --reload --port 8000 &
API_PID=$!

# Start frontend if node is available
if command -v node >/dev/null 2>&1 && [ -d "${PROJECT_ROOT}/apps/web" ]; then
    cd "${PROJECT_ROOT}/apps/web" && npm run dev &
    WEB_PID=$!
fi
cd "${PROJECT_ROOT}"

sleep 5

echo ""
echo "============================================"
echo "  ConstructAI Demo Ready!"
echo ""
echo "  Frontend:     http://localhost:3000"
echo "  API Docs:     http://localhost:8000/docs"
echo "  pgAdmin:      http://localhost:5050"
echo "  Kafka UI:     http://localhost:8080"
echo "  MinIO:        http://localhost:9001"
echo ""
echo "  Login:  pm@buildright.dev / Demo2026!"
echo ""
echo "  Run: python demo/scripts/open_dashboards.py"
echo "  Run: python demo/scripts/demo_walkthrough.py"
echo "============================================"

wait $API_PID ${WEB_PID:-} 2>/dev/null || true
