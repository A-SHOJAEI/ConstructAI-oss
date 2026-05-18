# Code Structure

## Monorepo Layout
```
constructai/
├── apps/
│   ├── api/          # FastAPI backend
│   │   ├── app/
│   │   │   ├── api/v1/       # API endpoints
│   │   │   ├── models/       # SQLAlchemy models
│   │   │   ├── schemas/      # Pydantic schemas
│   │   │   ├── services/     # Business logic
│   │   │   │   ├── agents/   # 11 LangGraph agents
│   │   │   │   ├── security/ # RBAC, encryption
│   │   │   │   ├── tenant/   # Multi-tenant
│   │   │   │   └── ...
│   │   │   └── middleware/   # Request middleware
│   │   ├── tests/            # pytest test suite
│   │   └── alembic/          # DB migrations
│   └── web/          # Next.js frontend
├── docs/             # Documentation
└── infra/            # Docker, monitoring
```

## Key Patterns
- **Models**: SQLAlchemy 2.0 with `mapped_column()`, UUID primary keys
- **Schemas**: Pydantic v2 with `model_config = {"from_attributes": True}`
- **Agents**: LangGraph `StateGraph` with `TypedDict` state, `build_X()` and `run_X()` pattern
- **Tests**: pytest-asyncio with `asyncio_mode = "auto"`, class-based organization
- **Linting**: ruff with 100-char line length
