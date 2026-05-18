# Contributing to ConstructAI

Thanks for your interest in contributing! This document covers what to expect.

## License of contributions

By submitting a contribution (pull request, patch, issue with code, etc.), you agree that your contribution is licensed under the [PolyForm Noncommercial 1.0.0](./LICENSE) license, and that the project maintainer may also relicense it under a separate commercial license without further notice. If you cannot agree to this, please do not submit contributions.

This is the same model used by most dual-licensed source-available projects (e.g., Sentry, Sidekiq, Plausible).

## Before you start

Before opening a large pull request, please open an issue first to discuss the change. Small fixes (typos, obvious bugs, tightening a docstring) can go straight to PR.

For non-trivial changes, the easiest way to align is:

1. Open an issue describing the problem and your proposed approach
2. Wait for a thumbs-up before investing significant time
3. Submit the PR referencing the issue

## What we accept

We welcome:

- Bug fixes with tests
- Documentation improvements (`docs/`, READMEs, code comments)
- Performance improvements with measurements
- New ML model integrations (with reference paper / benchmark)
- Accessibility improvements (a11y in `apps/web/`)
- New integrations with construction-domain tools (Procore, Autodesk, BIM 360, etc.)

We are more cautious about:

- Architectural rewrites — please discuss first
- New top-level dependencies — justify the benefit vs. the maintenance burden
- Changes to the security model — open a discussion before coding

## Development setup

See [DEVELOPMENT.md](./DEVELOPMENT.md) and `docs/runbooks/local-dev-setup.md` for environment setup.

```bash
# Quick start
make setup          # install Python venv + npm deps
make start          # bring up Postgres, Redis, Kafka, MinIO, Mosquitto
make migrate        # run alembic migrations
make seed           # seed test data
make dev-backend    # uvicorn on :8000
make dev-frontend   # Next.js on :3000
```

## Coding conventions

- **Python**: `ruff check --fix` + `ruff format` (line length 100, target Python 3.12). Run with `make lint-backend`.
- **TypeScript**: `eslint --fix` + `prettier --write`. Run with `make lint-frontend`.
- **Pre-commit hooks**: Husky runs both linters on staged files. Don't bypass with `--no-verify` unless you have a reason.
- **Tests**: every behavioral change should have a corresponding test. Backend: `pytest`. Frontend unit: `vitest`. Frontend e2e: `playwright`.
- **Async first**: backend uses async SQLAlchemy + httpx — match the style of nearby code.
- **No emojis** in code or commit messages unless explicitly requested.
- **Comments**: only write comments when the *why* is non-obvious. Don't restate the *what*.

## Commit messages

Use conventional commits style:

```
feat(scope): short imperative description
fix(scope): short imperative description
chore(scope): short imperative description
docs(scope): short imperative description
test(scope): short imperative description
```

Examples:

- `feat(rfi): index answered RFIs into pgvector for similarity search`
- `fix(auth): refresh-token rotation must invalidate the old token`
- `test(estimating): pin parametric model output shape`

## Pull request checklist

Before requesting review:

- [ ] Branch is rebased on the latest `main`
- [ ] Lint passes locally (`make lint`)
- [ ] Tests pass locally (`make test`)
- [ ] New code has tests (or you explain why not)
- [ ] No secrets, API keys, or personal info in the diff
- [ ] No checked-in datasets or model weights (use `.gitignore`)
- [ ] PR description explains the *why*, not just the *what*

## Reporting bugs

Open an issue with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Your environment (OS, Python version, Node version, Docker version)
- Relevant logs (please redact secrets first)

For security issues, see [SECURITY.md](./SECURITY.md) instead — do not file them publicly.

## Code of conduct

Be kind, be specific, assume good faith, and focus on the work. We don't have a formal CoC document; treating people the way you'd want to be treated in code review is the standard.

## Questions

If something here is unclear or you need clarification before contributing, open a discussion issue or email **shojaei@vt.edu**.
