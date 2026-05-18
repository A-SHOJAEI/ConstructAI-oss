# ---- Builder Stage ----
# Pinned to a specific patch for reproducibility. When rotating, also update
# the runtime stage below and rebuild the image digest allowlist in CI.
# TODO(ops): replace tag with @sha256:<digest> pin once the registry mirror
# tracks digests.
FROM python:3.12.7-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Compute the install extras for this build. The `point_cloud` extra
# (open3d) is included only on x86_64 because no ARM64 Python 3.12 wheel
# ships for open3d as of 2026-04. ARM64 hosts (NVIDIA DGX Spark / GB10
# Grace) skip it; nothing in apps/api/app/ imports open3d directly so this
# is a no-op functionally.
RUN ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "amd64" ]; then \
      EXTRAS="execution,vision,production,point_cloud"; \
    else \
      EXTRAS="execution,vision,production"; \
    fi && \
    echo "Installing extras for $ARCH: $EXTRAS" && \
    pip install --no-cache-dir ".[$EXTRAS]"

COPY . .
RUN ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "amd64" ]; then \
      EXTRAS="execution,vision,production,point_cloud"; \
    else \
      EXTRAS="execution,vision,production"; \
    fi && \
    pip install --no-cache-dir ".[$EXTRAS]"

# Verify critical production dependencies are present. Use
# importlib.metadata so we don't have to actually import the modules at
# build time — some C-extension packages (torch, ultralytics) only resolve
# their ABI deps at runtime and would falsely fail an import check inside
# the slim image. The pip "Requirement already satisfied" lines from the
# preceding install steps already prove the packages are on disk.
RUN python <<'PY'
import importlib.metadata as md
import sys
required = ["confluent-kafka", "timm", "faster-whisper", "ultralytics", "JPype1"]
failures = []
for name in required:
    try:
        v = md.version(name)
        print(f"OK: {name}=={v}")
    except md.PackageNotFoundError:
        failures.append(name)
        print(f"MISSING: {name}")
if failures:
    print(f"FATAL: Missing {len(failures)} deps: {failures}")
    sys.exit(1)
PY

# Download MPXJ JARs during build
RUN python -m app.services.scheduling.download_mpxj || true

# ---- Runtime Stage ----
FROM python:3.12.7-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

RUN useradd -m -r appuser && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
