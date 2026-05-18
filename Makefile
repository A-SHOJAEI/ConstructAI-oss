.PHONY: setup start stop test lint migrate seed clean \
       provision-certs provision-certs-force \
       demo demo-seed demo-teardown demo-open demo-walkthrough \
       demo-onboarding demo-change-order demo-safety demo-models

# === Setup ===
setup:
	@echo "Installing root dependencies..."
	npm install
	@echo "Installing frontend dependencies..."
	cd apps/web && npm install
	@echo "Installing backend dependencies..."
	cd apps/api && pip install -e ".[dev]"
	@echo "Copying .env files..."
	cp -n .env.example .env 2>/dev/null || true
	cp -n apps/api/.env.example apps/api/.env 2>/dev/null || true
	cp -n apps/web/.env.example apps/web/.env 2>/dev/null || true
	@echo "Setup complete!"

# === Infrastructure ===
start:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Waiting for services..."
	@sleep 5
	@echo "Infrastructure running."

stop:
	docker compose -f infra/docker-compose.yml down

# === Certificate & Credential Provisioning ===
provision-certs:
	@echo "=== Provisioning TLS certificates and service credentials ==="
	@echo ""
	bash infra/scripts/generate-tls-certs.sh --output-dir infra/certs
	@echo ""
	bash infra/scripts/generate-kafka-credentials.sh infra/certs/kafka
	@echo ""
	bash infra/scripts/generate-mqtt-credentials.sh infra/mosquitto/passwd
	@echo ""
	@echo "=== All certificates and credentials provisioned ==="

provision-certs-force:
	@echo "=== Regenerating ALL TLS certificates and credentials ==="
	@echo ""
	bash infra/scripts/generate-tls-certs.sh --output-dir infra/certs --force
	@echo ""
	rm -f infra/certs/kafka/kafka_server_jaas.conf
	bash infra/scripts/generate-kafka-credentials.sh infra/certs/kafka
	@echo ""
	rm -f infra/mosquitto/passwd
	bash infra/scripts/generate-mqtt-credentials.sh infra/mosquitto/passwd
	@echo ""
	@echo "=== All certificates and credentials regenerated ==="

# === Database ===
migrate:
	cd apps/api && alembic upgrade head

seed:
	cd apps/api && python -m app.utils.seed

# === Testing ===
test: test-backend test-frontend

test-backend:
	cd apps/api && pytest tests/ -v --cov=app --cov-report=term-missing --ignore=tests/phase1

test-frontend:
	cd apps/web && npx vitest run

test-phase1:
	cd apps/api && pytest tests/phase1/ -v --no-header 2>&1 | head -50 || true
	@echo ""
	@echo "Phase 1 tests are TDD placeholders — expected to FAIL until Phase 1 is implemented."

test-all:
	cd apps/api && pytest tests/ -v --cov=app --cov-report=term-missing

# === Linting ===
lint: lint-backend lint-frontend

lint-backend:
	cd apps/api && ruff check . && ruff format --check .

lint-frontend:
	cd apps/web && npx eslint src/ && npx prettier --check "src/**/*.{ts,tsx}"

# === Development Servers ===
dev-backend:
	cd apps/api && uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd apps/web && npm run dev

# === Cleanup ===
clean:
	docker compose -f infra/docker-compose.yml down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/web/.next apps/web/node_modules

# === Demo ===
demo:
	bash demo/setup.sh

demo-seed:
	cd apps/api && python -m demo.seed.seed_all

demo-teardown:
	bash demo/teardown.sh

demo-open:
	python demo/scripts/open_dashboards.py

demo-walkthrough:
	python demo/scripts/demo_walkthrough.py

demo-onboarding:
	python demo/scripts/trigger_onboarding.py

demo-change-order:
	python demo/scripts/trigger_change_order.py

demo-safety:
	python demo/scripts/trigger_safety_incident.py

demo-models:
	bash demo/models/download_models.sh
