# Disabled workflows

The `ci.yml` and `cd.yml` here are the **original** pipelines from the private development environment. They are kept for reference, not active in this repo.

The active CI workflow at `.github/workflows/ci.yml` is a slimmed-down version that runs without secrets, services, or paid runners:

- `Lint (ruff)` — `ruff format --check` + `ruff check` on `apps/api/`
- `Tests (web)` — `vitest run --coverage` with a 14% lines floor
- `Dockerfile check` — `docker buildx build --check` on both Dockerfiles (lints Dockerfile syntax & best practices without actually building)

## Why these were disabled

The original pipelines were written for a private dev environment with:

- Self-hosted runners with ~10 GB+ free disk for ML deps (`timm`, `faster_whisper`, `confluent_kafka`, `open3d`)
- Populated GitHub Secrets: `STAGING_HOST`, `STAGING_USER`, `STAGING_SSH_KEY`, `KUBE_CONFIG`, `TF_PLAN_AWS_*`
- A GHCR-published API + web image pair
- Real staging and production environments to deploy to
- An `aquasecurity/trivy-action@0.28.0` reference that no longer resolves

None of that exists for the public mirror, so a straight copy fails on first push.

## Path back to a fuller pipeline

Rough priority order — each item should be a self-contained PR:

1. **Backend tests (`test-backend`)** — port from `ci.yml`. There are ~6,700 tests; a clean-room CI run will turn up flaky/stale tests that the local dev environment hides (we already saw this on the frontend, where a freshly mocked icon and a recent label rename broke two tests). Recommend landing the job with a lower coverage floor first (10–20%) and ratcheting up. Will need:
   - `services: postgres + redis` blocks (already in the disabled file)
   - "Free disk space" step before installing ML extras
   - Test-DB URL passed via env (`PYTEST_DATABASE_URL` overrides the default 5530 port that the conftest uses for the host's local stack)

2. **Terraform gate (`terraform`)** — opts in on `infra/terraform/**` path changes, so harmless. Just bring it back as-is.

3. **Container image build & publish** — most of `cd.yml` (`build-api`, `build-web`). Cuts to make:
   - Drop the `aquasecurity/trivy-action@0.28.0` step (or pin a real version like `@0.30.0`)
   - Decide whether to push to GHCR under your org — if yes, no secrets needed (uses `GITHUB_TOKEN`); if no, do `push: false` and just verify the build
   - Drop `deploy-staging` and `deploy-production` until those environments exist

4. **Deploy pipelines** — `deploy-staging` and `deploy-production`. Only worth wiring up once a real environment exists; until then these are a 100% failure rate that hides nothing useful.

5. **Reusable workflow patterns** — once 3 of these have landed, consider extracting common setup (Python, Node, caches) into composite actions under `.github/actions/`.
