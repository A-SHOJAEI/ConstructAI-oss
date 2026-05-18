# Changelog

All notable changes to ConstructAI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-09

### Added
- **Project Selector**: Multi-project support with persistent project selection
- **Email Service**: SMTP-based email for verification, password reset, and safety alerts
- **httpOnly Cookie Auth**: Dual auth support (cookies for browser, Bearer for API)
- **CSRF Protection**: Double-submit cookie pattern for mutation endpoints
- **Admin User CRUD**: Create, edit, deactivate users with role management
- **Notification Preferences**: Per-user notification toggle settings
- **Error Boundary**: React error boundary with retry capability
- **Dark Mode**: Light/dark/system theme support with Tailwind CSS
- **Responsive Sidebar**: Mobile-friendly sidebar with overlay pattern
- **Breadcrumbs**: Auto-generated breadcrumb navigation
- **Pagination**: Reusable pagination component
- **CSV Export**: Export safety alerts, RFIs, and daily logs to CSV
- **Voice Transcription**: Audio upload with Whisper-based transcription
- **Voice Q&A**: Voice-activated RAG query over project documents
- **Document Comparison**: Structured diff between document versions
- **Delay Predictor**: ML-based schedule delay risk assessment
- **Compliance Reports**: Automated safety, quality, and schedule compliance reports
- **Canary Deployments**: Redis-backed model canary deployment with auto-promote/rollback
- **Active Learning API**: Annotation batch generation for model improvement
- **CV Regression Suite**: Automated regression tests for YOLO and ViT models
- **SSO Authentication**: Google and Microsoft OIDC sign-in
- **Keyboard Shortcuts**: Global keyboard navigation with help dialog
- **Onboarding Tour**: Guided first-use tour for new users
- **Audit Log Viewer**: Admin UI for reviewing audit trail
- **Help Panel**: In-app contextual help system
- **Loki + Promtail**: Log aggregation for monitoring stack
- **Helm Charts**: Kubernetes deployment with HPA, network policies, ingress
- **Argo Rollouts**: Canary deployment strategy with Prometheus analysis
- **k6 Load Tests**: API performance testing scripts

### Changed
- Migrated from `python-jose` to `PyJWT` for JWT handling
- Production CD workflow switched from SSH/docker-compose to Helm-based deployment
- Token blacklist and rate limiting moved from in-memory to Redis

### Security
- Redis-backed token blacklist and account lockout
- CSRF middleware for cookie-based auth
- Canary deployer state persisted to Redis instead of in-memory

## [0.1.0] - 2026-02-28

### Added
- Initial release with all Phase 0-7 features
- FastAPI backend with 46 API route modules
- Next.js 15 frontend with 20 pages
- 11 AI agents (document, estimating, scheduling, safety, quality, etc.)
- YOLOv8 safety detection and ViT defect classification
- RAG pipeline with construction-specialized embeddings
- OSHA 29 CFR 1926 knowledge base
- RFI resolution agent with 3-stage pipeline
- Procore integration with webhook processing
- WebSocket real-time updates
- Full RBAC with multi-tenancy
- 644 backend tests, 28 frontend tests
- Docker Compose production deployment
- Prometheus + Grafana monitoring
- Terraform infrastructure modules
