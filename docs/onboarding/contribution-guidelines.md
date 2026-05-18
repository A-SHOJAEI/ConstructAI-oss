# Contribution Guidelines

## Branch Strategy
- `main`: Production-ready code
- `develop`: Integration branch
- `feature/*`: Feature branches
- `fix/*`: Bug fix branches

## Pull Request Process
1. Create a feature branch from `develop`
2. Write tests first (TDD encouraged)
3. Implement the feature
4. Ensure all tests pass: `pytest tests/ -v`
5. Ensure no lint errors: `ruff check .`
6. Create PR with description of changes
7. Require 1 approval before merge

## Code Standards
- Python: ruff with project config (100 char lines)
- TypeScript: ESLint with project config
- All files start with `from __future__ import annotations`
- Use `logging` module, never `print()`
- Async functions for all I/O operations

## Testing Requirements
- All new features must have tests
- Maintain >80% code coverage on critical paths
- Test files in `tests/` directory
- Use pytest-asyncio for async tests
- Mock external services (DB, Redis, APIs)

## Commit Messages
Follow conventional commits:
- `feat: add cost estimation agent`
- `fix: correct EVM calculation formula`
- `docs: update API documentation`
- `test: add RBAC permission matrix tests`
- `refactor: simplify agent state management`
