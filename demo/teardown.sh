#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Stopping ConstructAI demo..."

# Stop application processes
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "npm run dev" 2>/dev/null || true

# Stop and remove containers + volumes
cd "${PROJECT_ROOT}/infra"
docker compose -f docker-compose.yml -f docker-compose.demo.yml down -v 2>/dev/null || \
docker compose -f docker-compose.yml down -v

# Clean generated output
rm -rf "${PROJECT_ROOT}/demo/output" 2>/dev/null || true

echo "Demo stopped and volumes removed."
echo ""
echo "To restart: make demo"
