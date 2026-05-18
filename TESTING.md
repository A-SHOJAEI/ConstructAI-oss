# Testing Guide

## Testing Philosophy

ConstructAI follows a **TDD-first** approach with a testing pyramid:

1. **Unit Tests** (base): Test individual functions, services, and utilities in isolation
2. **Integration Tests** (middle): Test API endpoints with real database interactions
3. **E2E Tests** (top): Test full user workflows through the frontend

All new features start with failing tests that define expected behavior before implementation begins.

## Running Tests

### All Tests

```bash
make test
```

### Backend Tests (pytest)

```bash
# Run all backend tests (excludes Phase 1 TDD placeholders)
make test-backend

# Run with verbose output
cd apps/api && pytest tests/ -v --ignore=tests/phase1

# Run a specific test file
cd apps/api && pytest tests/test_auth.py -v

# Run a specific test
cd apps/api && pytest tests/test_auth.py::test_login_success -v

# Run with coverage report
cd apps/api && pytest tests/ -v --cov=app --cov-report=term-missing --ignore=tests/phase1
```

### Frontend Tests (vitest)

```bash
# Run all frontend tests
make test-frontend

# Run in watch mode
cd apps/web && npx vitest

# Run a specific test file
cd apps/web && npx vitest run tests/home.test.tsx
```

### Phase 1 TDD Placeholder Tests

```bash
# Show Phase 1 test status (expected to FAIL/SKIP)
make test-phase1
```

Phase 1 tests are located in `apps/api/tests/phase1/` and define the expected behavior for:

- **Document Upload**: PDF upload, file validation, S3 storage, database records
- **PDF Parsing**: Text extraction, table extraction, heading detection, error handling
- **Chunking**: CSI-aware chunking, size limits, metadata, table preservation
- **Embeddings**: Voyage-3-large vectors, pgvector storage, fallback, batching
- **RAG Retrieval**: Hybrid search, RRF ranking, reranking, citations

These tests are decorated with `@pytest.mark.skip(reason="Phase 1 not yet implemented")` and serve as the TDD contract for Phase 1 implementation.

## Test Data Strategy

- **Test fixtures** are defined in `apps/api/tests/conftest.py`
- Each test function gets a fresh database state (tables created and dropped per test)
- Test organizations, users, and auth headers are provided as pytest fixtures
- No shared mutable state between tests

### Key Fixtures

| Fixture | Description |
|---------|-------------|
| `db_engine` | Async SQLAlchemy engine connected to test database |
| `db_session` | Async database session with automatic rollback |
| `client` | httpx.AsyncClient configured with FastAPI test app |
| `test_org` | Pre-created test Organization instance |
| `test_user` | Pre-created test User with hashed password |
| `auth_headers` | Dict with `Authorization: Bearer <token>` for authenticated requests |

## CI/CD Pipeline

The GitHub Actions CI pipeline (`.github/workflows/ci.yml`) runs on every push/PR to `main`:

1. **Lint Job**:
   - Python: `ruff check` + `ruff format --check`
   - TypeScript: `tsc --noEmit`

2. **Backend Test Job** (requires lint):
   - Starts PostgreSQL 17 (TimescaleDB) and Redis services
   - Runs Alembic migrations
   - Runs pytest with coverage
   - Requires >= 80% code coverage

3. **Frontend Test Job** (requires lint):
   - Installs Node.js dependencies
   - Runs vitest

## Coverage Requirements

| Component | Minimum Coverage | Tool |
|-----------|-----------------|------|
| Backend | >= 80% | pytest-cov |
| Frontend | All tests passing | vitest |

### Viewing Coverage

```bash
# Terminal coverage report
cd apps/api && pytest tests/ --cov=app --cov-report=term-missing --ignore=tests/phase1

# HTML coverage report
cd apps/api && pytest tests/ --cov=app --cov-report=html --ignore=tests/phase1
# Open htmlcov/index.html in browser
```

## Writing New Tests

### Backend Test Template

```python
import pytest


async def test_feature_success(client, auth_headers):
    response = await client.post(
        "/api/v1/feature/",
        json={"field": "value"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["field"] == "value"


async def test_feature_unauthorized(client):
    response = await client.post("/api/v1/feature/", json={"field": "value"})
    assert response.status_code in (401, 403)
```

### Frontend Test Template

```typescript
import { expect, test, describe } from "vitest";
import { render, screen } from "@testing-library/react";
import { MyComponent } from "@/components/my-component";

describe("MyComponent", () => {
  test("renders correctly", () => {
    render(<MyComponent />);
    expect(screen.getByText("Expected Text")).toBeInTheDocument();
  });
});
```
