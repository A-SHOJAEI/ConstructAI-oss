#!/usr/bin/env bash
set -euo pipefail

# Generate Kafka SCRAM-SHA-512 credentials and JAAS configuration.
#
# This script creates:
#   - Random SCRAM-SHA-512 passwords for the broker admin and client (constructai) user
#   - A JAAS config file at $OUTPUT_DIR/kafka_server_jaas.conf
#
# Usage:
#   ./generate-kafka-credentials.sh [output_dir]
#
# Arguments:
#   output_dir   Directory for the JAAS config file (default: ./certs/kafka)
#
# The generated credentials are printed to stdout so you can copy them into
# your .env file.
#
# Examples:
#   ./generate-kafka-credentials.sh
#   ./generate-kafka-credentials.sh /etc/constructai/certs/kafka

OUTPUT_DIR="${1:-./certs/kafka}"

mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Generate random passwords (32 characters, alphanumeric + symbols)
# ---------------------------------------------------------------------------
_random_password() {
  # Use /dev/urandom for cryptographically secure randomness.
  # Fall back to openssl if /dev/urandom is not available.
  if [[ -r /dev/urandom ]]; then
    tr -dc 'A-Za-z0-9!@#%^*_+=' < /dev/urandom | head -c 32 || true
  elif command -v openssl &>/dev/null; then
    openssl rand -base64 32 | tr -d '/+=' | head -c 32
  else
    echo "ERROR: No secure random source available (/dev/urandom or openssl required)." >&2
    exit 1
  fi
}

ADMIN_PASSWORD="$(_random_password)"
CLIENT_PASSWORD="$(_random_password)"

JAAS_FILE="$OUTPUT_DIR/kafka_server_jaas.conf"

# ---------------------------------------------------------------------------
# Check idempotency: skip if JAAS file already exists
# ---------------------------------------------------------------------------
if [[ -f "$JAAS_FILE" ]]; then
  echo "JAAS config already exists at $JAAS_FILE"
  echo "Delete it first or specify a different output directory to regenerate."
  echo ""
  echo "Existing file contents (passwords redacted):"
  sed 's/password="[^"]*"/password="***"/g' "$JAAS_FILE"
  exit 0
fi

# ---------------------------------------------------------------------------
# Write JAAS config
# ---------------------------------------------------------------------------
cat > "$JAAS_FILE" <<EOF
KafkaServer {
    org.apache.kafka.common.security.scram.ScramLoginModule required
    username="admin"
    password="${ADMIN_PASSWORD}"
    user_admin="${ADMIN_PASSWORD}"
    user_constructai="${CLIENT_PASSWORD}";
};

KafkaClient {
    org.apache.kafka.common.security.scram.ScramLoginModule required
    username="constructai"
    password="${CLIENT_PASSWORD}";
};
EOF

chmod 600 "$JAAS_FILE"

# ---------------------------------------------------------------------------
# Print summary to stdout
# ---------------------------------------------------------------------------
echo "=== Kafka SASL Credentials Generated ==="
echo ""
echo "JAAS config written to: $JAAS_FILE"
echo ""
echo "Add these to your .env file:"
echo "------------------------------------------------------------"
echo "KAFKA_ADMIN_USER=admin"
echo "KAFKA_ADMIN_PASSWORD=${ADMIN_PASSWORD}"
echo "KAFKA_CLIENT_USER=constructai"
echo "KAFKA_CLIENT_PASSWORD=${CLIENT_PASSWORD}"
echo "------------------------------------------------------------"
echo ""
echo "IMPORTANT: Store these credentials securely. They are not"
echo "recoverable from the JAAS file without regenerating."
echo ""
echo "Done."
