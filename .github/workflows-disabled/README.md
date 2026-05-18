# Disabled workflows

The `ci.yml` and `cd.yml` here were written for the private development environment, which had:

- A populated `.env` with database, Redis, JWT, and external API credentials
- Self-hosted runners with ~10 GB+ free disk space for ML dependencies (`timm`, `faster_whisper`, `confluent_kafka`)
- GitHub Secrets: `STAGING_HOST`, `STAGING_USER`, `STAGING_SSH_KEY`, `KUBE_CONFIG`, `TF_PLAN_AWS_*`
- A GHCR-published API + web image pair
- Staging and production environments to deploy to

None of those exist for the public mirror, so the workflows are kept here for reference and will be ported back into `.github/workflows/` one piece at a time as the open-source CI is built up.

Path back to a fuller CI pipeline (rough order):

1. **Backend tests** — port `test-backend` job from `ci.yml`. Needs `services: postgres + redis` (already wired) plus a freed-disk step for ML deps. Should run on push and PR.
2. **Frontend tests** — port `test-frontend` job. Already self-contained.
3. **Terraform gate** — port `terraform` job. Already opts in via `if:` on infra path changes; harmless to re-enable.
4. **Container image build** — fork off the CD pipeline; build to GHCR without Trivy scan + deploy until a community deployment story is set.
