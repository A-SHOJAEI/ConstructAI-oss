# Security Policy

## Reporting a vulnerability

If you find a security vulnerability in ConstructAI, **please do not open a public GitHub issue**. Vulnerabilities reported in public can be exploited before a fix is available.

Instead, email **shojaei@vt.edu** with:

- A description of the vulnerability
- Steps to reproduce
- The affected component(s) (API endpoint, ML pipeline, infra config, etc.)
- The version / commit you reproduced it on
- Your suggested fix, if any
- Whether you'd like public credit when the fix is released

You should expect:

| Step | Timeline |
|---|---|
| Acknowledgement of your report | within 3 business days |
| Triage and severity assessment | within 7 business days |
| Patch or mitigation timeline communicated | within 14 business days |
| Public disclosure (coordinated with you) | typically 30–90 days after patch availability |

For severe vulnerabilities (RCE, auth bypass, secret exfiltration), we move faster.

## Scope

The following are in scope:

- Code in this repository (`apps/`, `packages/`, `ml/`, `edge/`, `infra/`, `bin/`, `scripts/`)
- Default container configurations in `infra/`
- The documented API surface in `docs/API_REFERENCE.md`

The following are **not** in scope:

- Third-party dependencies (please report to their upstream — we will track and rebuild against fixes)
- Self-hosted misconfigurations (these are deployment-specific)
- Use in violation of the [LICENSE](./LICENSE) or [COMMERCIAL.md](./COMMERCIAL.md)
- Reports from automated scanners without manual validation

## Hardening

ConstructAI includes the following security-relevant features in the default build:

- Row-level security (RLS) for multi-tenant isolation
- JWT authentication with bcrypt password hashing
- CSRF protection (double-submit cookie)
- Rate limiting on auth and webhook endpoints
- Audit logging for sensitive operations
- TLS-ready (terminate at the reverse proxy)
- Security headers middleware

For production deployments, you are responsible for:

- Generating strong `JWT_SECRET_KEY`, `ENCRYPTION_KEY`, and database passwords
- Configuring `COOKIE_SECURE=true` and a proper `COOKIE_DOMAIN`
- Setting `TRUSTED_PROXY_IPS` if behind a reverse proxy
- Rotating credentials regularly
- Reviewing `apps/api/.env.example` for all required secrets

## Public disclosure

After a fix is released, we publish a brief advisory in `CHANGELOG.md` and tag the release. We will credit the reporter unless they request anonymity.
