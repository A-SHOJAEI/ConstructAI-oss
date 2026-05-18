#!/bin/bash
# M-52: Automated backup restore rehearsal.
#
# A backup that's never restored isn't a backup — it's a hope. This script
# pulls the most recent backup from S3, decrypts + decompresses it, loads
# it into a temporary PostgreSQL container, and runs a smoke-test query.
# Designed to run weekly from cron or CI.
#
# Exits non-zero on any failure so the invoking scheduler can alert.

set -euo pipefail

# Required env vars
: "${S3_BACKUP_BUCKET:?Must set S3_BACKUP_BUCKET}"
: "${BACKUP_ENCRYPTION_KEY:?Must set BACKUP_ENCRYPTION_KEY (GPG recipient email or fingerprint)}"
: "${POSTGRES_USER:?Must set POSTGRES_USER}"
: "${POSTGRES_PASSWORD:?Must set POSTGRES_PASSWORD}"

# Optional
AWS_REGION="${AWS_REGION:-us-east-1}"
RESTORE_CONTAINER="${RESTORE_CONTAINER:-constructai-restore-test}"
RESTORE_PORT="${RESTORE_PORT:-55432}"
POSTGRES_VERSION="${POSTGRES_VERSION:-17-alpine}"
WORKDIR="$(mktemp -d)"
cleanup() {
    # Kill the temp container and wipe the workdir regardless of success.
    docker rm -f "$RESTORE_CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# 1. Find the most recent backup object in S3.
echo "==> Listing backups in s3://$S3_BACKUP_BUCKET/"
LATEST="$(aws s3 ls "s3://$S3_BACKUP_BUCKET/" --region "$AWS_REGION" \
            | sort | tail -n 1 | awk '{print $4}')"
if [ -z "$LATEST" ]; then
    echo "FATAL: no backup objects found" >&2
    exit 1
fi
echo "    latest: $LATEST"

# 2. Download + decrypt + decompress.
echo "==> Downloading backup"
aws s3 cp "s3://$S3_BACKUP_BUCKET/$LATEST" "$WORKDIR/$LATEST" --region "$AWS_REGION"

echo "==> Decrypting (GPG)"
gpg --batch --yes --decrypt --output "$WORKDIR/backup.sql.gz" "$WORKDIR/$LATEST"

echo "==> Decompressing"
gunzip -f "$WORKDIR/backup.sql.gz"

# 3. Spin up a throwaway Postgres to restore into.
echo "==> Starting throwaway PostgreSQL ($POSTGRES_VERSION) on port $RESTORE_PORT"
docker run -d --rm \
    --name "$RESTORE_CONTAINER" \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_DB=constructai_restore \
    -p "$RESTORE_PORT":5432 \
    "postgres:$POSTGRES_VERSION" >/dev/null

# Wait for readiness (max 60s).
for i in $(seq 1 60); do
    if docker exec "$RESTORE_CONTAINER" pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
        break
    fi
    sleep 1
    [ "$i" -eq 60 ] && { echo "FATAL: restore DB didn't become ready" >&2; exit 1; }
done

# 4. Restore the dump.
echo "==> Restoring dump (this can take several minutes on production-sized backups)"
PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h localhost -p "$RESTORE_PORT" -U "$POSTGRES_USER" \
    -d constructai_restore \
    -v ON_ERROR_STOP=1 \
    < "$WORKDIR/backup.sql"

# 5. Smoke tests: schema + row counts on safety-critical tables.
echo "==> Smoke test: schema"
TABLE_COUNT="$(PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h localhost -p "$RESTORE_PORT" -U "$POSTGRES_USER" \
    -d constructai_restore -t -A \
    -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")"
if [ "$TABLE_COUNT" -lt 50 ]; then
    echo "FATAL: restored schema has only $TABLE_COUNT tables (expected 50+)" >&2
    exit 1
fi
echo "    $TABLE_COUNT tables present"

for tbl in organizations users projects audit_logs rfis; do
    ROWS="$(PGPASSWORD="$POSTGRES_PASSWORD" psql \
        -h localhost -p "$RESTORE_PORT" -U "$POSTGRES_USER" \
        -d constructai_restore -t -A \
        -c "SELECT COUNT(*) FROM $tbl")"
    echo "    $tbl: $ROWS rows"
done

echo "==> Backup restore test PASSED ($LATEST)"
