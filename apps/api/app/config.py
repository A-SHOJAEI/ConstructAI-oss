from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "ConstructAI API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production
    API_PREFIX: str = "/api/v1"
    TESTING: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://constructai:constructai@localhost:5432/constructai"
    DATABASE_URL_SYNC: str = "postgresql://constructai:constructai@localhost:5432/constructai"

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "INSECURE-DEV-ONLY-CHANGE-IN-PRODUCTION-MIN-32-CHARS"
    # SECURITY [M-26]: Previous JWT secret key for rotation. During rotation:
    # 1. Set JWT_SECRET_KEY_PREVIOUS to the old key
    # 2. Set JWT_SECRET_KEY to the new key
    # 3. Token verification tries current key first, then previous key
    # 4. After all tokens expire (max REFRESH_TOKEN_EXPIRE_DAYS), remove the previous key
    JWT_SECRET_KEY_PREVIOUS: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    S3_ENDPOINT_URL: str = "http://localhost:9000"
    # SECURITY: Default MinIO credentials — insecure, for local development only.
    # Production deployments MUST override these with real S3/MinIO credentials.
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_DOCUMENTS: str = "constructai-documents"

    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:29092"

    # Embedding providers (keys are also read by voyageai / openai SDKs from env)
    VOYAGE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # H-7: LLM model names are configurable so agents can swap providers /
    # models without code changes and fall back cleanly when the primary
    # endpoint is unavailable.
    LLM_MODEL_RFI_AGENT: str = "gpt-4o"
    LLM_MODEL_FALLBACK: str = "gpt-4o-mini"

    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""

    # External data APIs
    FRED_API_KEY: str = ""
    BLS_API_KEY: str = ""

    # Weather API
    WEATHER_API_ENABLED: bool = True
    WEATHER_CACHE_TTL: int = 3600  # 1 hour
    OPENWEATHERMAP_API_KEY: str = ""

    # Procore Integration
    PROCORE_CLIENT_ID: str = ""
    PROCORE_CLIENT_SECRET: str = ""
    PROCORE_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/procore/callback"
    PROCORE_BASE_URL: str = "https://sandbox.procore.com"
    PROCORE_LOGIN_URL: str = "https://login.procore.com"
    PROCORE_API_URL: str = "https://sandbox.procore.com/rest/v1.0"
    PROCORE_WEBHOOK_SECRET: str = ""

    # Autodesk Integration
    AUTODESK_WEBHOOK_SECRET: str = ""

    # Phase 4 - Project Controls
    MONTE_CARLO_DEFAULT_ITERATIONS: int = 10000
    EVM_VARIANCE_ALERT_THRESHOLD: float = 0.10

    # Phase 4 - Quality
    DEFECT_MODEL_PATH: str = "models/defect_vit_b16.pth"
    OSHA_ECFR_BASE_URL: str = "https://www.ecfr.gov/api/versioner/v1"

    # Phase 4 - Productivity
    ACTIVITY_MODEL_PATH: str = "models/videomae_construction.pth"
    ISO15143_ENDPOINT: str = ""

    # Phase 4 - Communication
    WHISPER_MODEL_SIZE: str = "base"
    DAILY_REPORT_TEMPLATE_PATH: str = "app/templates/daily_report.html"
    MEETING_MINUTES_TEMPLATE_PATH: str = "app/templates/meeting_minutes.html"

    # Phase 4 - Privacy
    BLUR_FACES_IN_REPORTS: bool = True
    REDACT_PII_IN_TRANSCRIPTS: bool = True

    # Phase 5 - Reliability
    LITELLM_FALLBACK_MODELS: str = (
        "anthropic/claude-sonnet-4-20250514,openai/gpt-4o,gemini/gemini-2.0-flash"
    )
    SEMANTIC_CACHE_THRESHOLD: float = 0.90
    SEMANTIC_CACHE_TTL: int = 300
    CIRCUIT_BREAKER_SAFETY_FAIL_MAX: int = 3
    CIRCUIT_BREAKER_SAFETY_RESET: int = 30
    CIRCUIT_BREAKER_ROUTINE_FAIL_MAX: int = 10
    CIRCUIT_BREAKER_ROUTINE_RESET: int = 120

    # Phase 5 - Guardrails
    GUARDRAILS_COST_RANGE_TOLERANCE: float = 0.30

    # Phase 5 - Evaluation
    EVALUATION_SCHEDULE: str = "0 2 * * *"
    LANGSMITH_PROJECT: str = "constructai-production"

    # Phase 6 - Performance
    PGBOUNCER_ENABLED: bool = True
    PGBOUNCER_URL: str = "postgresql+asyncpg://constructai:constructai@localhost:6432/constructai"

    # Metrics endpoint auth (optional — set to protect /metrics)
    METRICS_TOKEN: str = ""
    # SECURITY [M-18]: Must be explicitly True to allow unauthenticated /metrics access
    METRICS_ALLOW_ANONYMOUS: bool = False

    # Phase 6 - Security
    RATE_LIMIT_DEFAULT: int = 100
    RATE_LIMIT_BURST: int = 200
    RATE_LIMIT_BACKEND: str = "redis"  # "memory" or "redis"
    ENCRYPTION_KEY: str = ""
    # SECURITY [M-26]: Previous encryption key for rotation. During rotation:
    # 1. Set ENCRYPTION_KEY_PREVIOUS to the old key
    # 2. Set ENCRYPTION_KEY to the new key
    # 3. Decryption tries current key first, then falls back to previous key
    # 4. After all data is re-encrypted, remove ENCRYPTION_KEY_PREVIOUS
    ENCRYPTION_KEY_PREVIOUS: str = ""
    # SECURITY (H-05/H-06): Only trust X-Forwarded-For from these proxy IPs
    TRUSTED_PROXY_IPS: str = ""  # Comma-separated, e.g. "10.0.0.1,10.0.0.2"

    # Phase 6 - Feature Flags
    FEATURE_FLAG_PROVIDER: str = "local"
    UNLEASH_URL: str = ""
    UNLEASH_API_KEY: str = ""

    # Phase 6 - MLOps
    MLFLOW_TRACKING_URI: str = "http://localhost:5000"
    CANARY_TRAFFIC_PERCENT: int = 5
    CANARY_EVALUATION_HOURS: int = 24

    # Phase 6 - Observability
    OTEL_EXPORTER_ENDPOINT: str = "http://localhost:4317"
    PROMETHEUS_PORT: int = 9090

    # Cookie Auth
    COOKIE_DOMAIN: str = ""  # e.g. ".constructai.dev" — blank = request host
    # SECURITY [M-11]: Default to True; development must explicitly set COOKIE_SECURE=false
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_PATH: str = "/"

    # Email / SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    EMAIL_FROM_ADDRESS: str = "noreply@constructai.dev"
    EMAIL_FROM_NAME: str = "ConstructAI"
    FRONTEND_URL: str = "http://localhost:3000"

    # SECURITY [M-07]: When False (default), SSO login for unknown users returns
    # a "pending approval" response instead of auto-creating accounts. Set to True
    # only if your org explicitly wants automatic user provisioning via SSO.
    SSO_AUTO_CREATE_USERS: bool = False

    # SSO provider configuration. Empty client_id means the provider isn't
    # enabled — the /sso/authorize route returns 400 in that case.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OKTA_CLIENT_ID: str = ""
    OKTA_CLIENT_SECRET: str = ""
    OKTA_DOMAIN: str = ""
    AZURE_AD_CLIENT_ID: str = ""
    AZURE_AD_CLIENT_SECRET: str = ""
    AZURE_AD_TENANT_ID: str = ""

    # Phase 7 - Logistics Optimization
    LOGISTICS_USE_OPTIMIZATION: bool = True
    LOGISTICS_OPTIMIZATION_TIME_LIMIT: int = 30  # seconds

    # SECURITY [S6]: Configurable bcrypt rounds (default 12)
    BCRYPT_ROUNDS: int = 12

    # Model signature verification (HMAC-SHA256)
    MODEL_SIGNATURE_KEY: str = ""

    # Per-org daily LLM token budget (default 1M tokens)
    LLM_DAILY_TOKEN_BUDGET: int = 1_000_000

    # Local vLLM endpoint (heavy reasoning / drafting; gateway priority 1).
    # Wired into the LLM gateway by app.services.reliability.llm_gateway.
    LOCAL_VLLM_BASE_URL: str = "http://localhost:8000/v1"
    LOCAL_VLLM_API_KEY: str = ""
    LOCAL_VLLM_MODEL_NAME: str = "constructai-primary"

    # Secondary on-prem Ollama endpoint — fast classification / summarization
    # under demo mode.
    LOCAL_OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_OLLAMA_MODEL_NAME: str = "gpt-oss:20b"

    # Pre-trained CV model paths (override defaults via .env).
    SAFETY_YOLO_MODEL_PATH: str = "models/safety_yolo_v1.0/best.pt"
    CONSTRUCTION_EMBEDDING_MODEL_PATH: str = "models/construction-bge-large"

    # Demo mode: gates demo-only endpoints, seeds, behaviors, and enables
    # the dual-node routing (vLLM for reasoning, Ollama for fast tasks).
    # Off in production so the canonical 5-tier fallback chain is unchanged.
    DEMO_MODE: bool = False

    # Local-only LLM mode — when True, the gateway filters cloud providers
    # (Anthropic, OpenAI, Gemini) out of the active fallback chain. Cloud
    # entries, pricing, and the LiteLLM call path remain defined in the
    # gateway so setting this flag to False re-enables cloud failover
    # without code changes.
    LOCAL_ONLY_MODE: bool = True

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    def validate_production_config(self) -> None:
        """Raise ValueError if critical secrets are missing in non-test mode.

        SECURITY (C-06/C-07/C-08): This is auto-called at import time for
        staging and production environments to prevent deployment with
        insecure defaults.
        """
        # SECURITY [S4]: Only honor TESTING bypass in dev/test environments
        if self.TESTING and self.ENVIRONMENT not in ("staging", "production"):
            return
        errors: list[str] = []
        if not self.JWT_SECRET_KEY or len(self.JWT_SECRET_KEY) < 32:
            errors.append("JWT_SECRET_KEY must be set to a secure value (min 32 chars)")
        if any(
            marker in self.JWT_SECRET_KEY.upper()
            for marker in ("INSECURE", "DEV-ONLY", "CHANGEME", "CHANGE_ME")
        ):
            errors.append(
                "JWT_SECRET_KEY contains a known insecure marker — generate a real secret"
            )
        if (
            "CHANGEME" in self.DATABASE_URL.upper()
            or "constructai:constructai" in self.DATABASE_URL
        ):
            errors.append("DATABASE_URL must use a secure password")
        if (
            "CHANGEME" in self.PGBOUNCER_URL.upper()
            or "constructai:constructai" in self.PGBOUNCER_URL
        ):
            errors.append("PGBOUNCER_URL must use a secure password")
        if not self.ENCRYPTION_KEY or len(self.ENCRYPTION_KEY) < 32:
            errors.append("ENCRYPTION_KEY must be set (min 32 chars)")
        if self.S3_ACCESS_KEY == "minioadmin" or self.S3_SECRET_KEY == "minioadmin":
            errors.append("S3 credentials must not use default MinIO values in production")
        if not self.REDIS_URL or self.REDIS_URL == "redis://localhost:6379/0":
            errors.append("REDIS_URL must be configured with authentication for production")
        if self.PROCORE_REDIRECT_URI and not self.PROCORE_REDIRECT_URI.startswith("https://"):
            errors.append("PROCORE_REDIRECT_URI must use https:// in production/staging")
        if self.FRONTEND_URL and not self.FRONTEND_URL.startswith("https://"):
            errors.append(
                "FRONTEND_URL must use https:// in production/staging (used for SSO redirects)"
            )
        # RT6-AUTH-08: Enforce COOKIE_SECURE in production
        if self.ENVIRONMENT == "production" and not self.COOKIE_SECURE:
            errors.append("COOKIE_SECURE must be True in production (HTTPS only)")
        if self.ENVIRONMENT == "production" and not self.COOKIE_DOMAIN:
            errors.append("COOKIE_DOMAIN must be set in production")
        if self.COOKIE_SAMESITE.lower() not in ("lax", "strict"):
            errors.append("COOKIE_SAMESITE must be 'lax' or 'strict' in production")
        if "*" in self.CORS_ORIGINS:
            errors.append("CORS_ORIGINS must not contain '*' in production")
        if self.RATE_LIMIT_BACKEND != "redis":
            errors.append("RATE_LIMIT_BACKEND must be 'redis' in production (currently in-memory)")
        # H-10: Require MODEL_SIGNATURE_KEY at config-init time instead of
        # lazily (previous behavior let staging silently skip verification).
        if not self.MODEL_SIGNATURE_KEY or len(self.MODEL_SIGNATURE_KEY) < 32:
            errors.append(
                "MODEL_SIGNATURE_KEY must be set to a secure value (min 32 chars) in "
                "staging/production — otherwise signed model verification is bypassed"
            )
        if errors:
            raise ValueError("Production configuration errors:\n  - " + "\n  - ".join(errors))


settings = Settings()

# SECURITY (C-06/C-07/C-08): Auto-validate critical secrets at import time
# for staging and production environments.  This prevents deployment with
# hardcoded dev-only defaults (empty JWT secret, minioadmin S3 creds, etc.).
if settings.ENVIRONMENT in ("staging", "production"):
    settings.validate_production_config()
