#!/bin/bash
set -euo pipefail
# Automated PostgreSQL backup script
# Run via cron: 0 2 * * * /path/to/backup-db.sh

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DB_HOST="${DB_HOST:-postgres}"
DB_NAME="${DB_NAME:-constructai}"
DB_USER="${DB_USER:-constructai}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Starting backup: $BACKUP_FILE"
pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"

# GPG encrypt the backup
GPG_RECIPIENT="${GPG_RECIPIENT:-}"
if [ -n "$GPG_RECIPIENT" ]; then
    echo "Encrypting backup with GPG..."
    gpg --batch --yes --recipient "$GPG_RECIPIENT" --trust-model always --encrypt "$BACKUP_FILE"
    rm -f "$BACKUP_FILE"
    BACKUP_FILE="${BACKUP_FILE}.gpg"
    echo "Backup encrypted: $BACKUP_FILE"
fi

# Generate SHA-256 checksum
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
sha256sum "$BACKUP_FILE" > "$CHECKSUM_FILE"
echo "Checksum generated: $CHECKSUM_FILE"

# Upload to S3 if configured
if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
    S3_DEST="s3://${S3_BACKUP_BUCKET}/db-backups/$(basename "$BACKUP_FILE")"
    aws s3 cp "$BACKUP_FILE" "$S3_DEST" --sse aws:kms
    aws s3 cp "$CHECKSUM_FILE" "${S3_DEST}.sha256" --sse aws:kms
    echo "Uploaded to S3: $S3_DEST"

    # Verify upload integrity
    echo "Verifying upload integrity..."
    LOCAL_CHECKSUM=$(sha256sum "$BACKUP_FILE" | awk '{print $1}')
    S3_ETAG=$(aws s3api head-object --bucket "$S3_BACKUP_BUCKET" --key "db-backups/$(basename "$BACKUP_FILE")" --query 'Metadata' --output text 2>/dev/null || true)
    DOWNLOADED_FILE="${BACKUP_DIR}/.verify_$(basename "$BACKUP_FILE")"
    aws s3 cp "$S3_DEST" "$DOWNLOADED_FILE" --quiet
    DOWNLOADED_CHECKSUM=$(sha256sum "$DOWNLOADED_FILE" | awk '{print $1}')
    rm -f "$DOWNLOADED_FILE"
    if [ "$LOCAL_CHECKSUM" != "$DOWNLOADED_CHECKSUM" ]; then
        echo "ERROR: Checksum verification failed after S3 upload!"
        exit 1
    fi
    echo "Checksum verification passed."
fi

# Clean old backups
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz*" -mtime +${RETENTION_DAYS} -delete
echo "Backup complete: $BACKUP_FILE"
