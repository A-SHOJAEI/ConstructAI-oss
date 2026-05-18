#!/usr/bin/env bash
set -euo pipefail

# Generate MQTT (Mosquitto) password file for the constructai user.
#
# This script creates a password file at infra/mosquitto/passwd that
# Mosquitto uses for client authentication.
#
# If mosquitto_passwd is available, it generates a proper hashed password
# file. Otherwise, it creates a placeholder with instructions.
#
# Usage:
#   ./generate-mqtt-credentials.sh [output_file]
#
# Arguments:
#   output_file   Path to the password file (default: ./mosquitto/passwd)
#                 Relative paths are resolved from the script's parent dir (infra/).
#
# Examples:
#   ./generate-mqtt-credentials.sh
#   ./generate-mqtt-credentials.sh /etc/mosquitto/passwd

# Resolve default relative to the infra/ directory (parent of scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

OUTPUT_FILE="${1:-${INFRA_DIR}/mosquitto/passwd}"

# ---------------------------------------------------------------------------
# Ensure output directory exists
# ---------------------------------------------------------------------------
OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"
mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Generate a random password
# ---------------------------------------------------------------------------
_random_password() {
  if [[ -r /dev/urandom ]]; then
    tr -dc 'A-Za-z0-9!@#%^*_+=' < /dev/urandom | head -c 32 || true
  elif command -v openssl &>/dev/null; then
    openssl rand -base64 32 | tr -d '/+=' | head -c 32
  else
    echo "ERROR: No secure random source available." >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Check idempotency: skip if password file already exists
# ---------------------------------------------------------------------------
if [[ -f "$OUTPUT_FILE" ]]; then
  # Only skip if it's a real password file (not a placeholder comment).
  if ! grep -q '^# PLACEHOLDER' "$OUTPUT_FILE" 2>/dev/null; then
    echo "MQTT password file already exists at $OUTPUT_FILE"
    echo "Delete it first to regenerate."
    exit 0
  fi
fi

MQTT_USER="constructai"
MQTT_PASSWORD="$(_random_password)"

# ---------------------------------------------------------------------------
# Generate password file
# ---------------------------------------------------------------------------
if command -v mosquitto_passwd &>/dev/null; then
  echo "Using mosquitto_passwd to generate hashed password file ..."

  # Create empty file first, then add the user.
  : > "$OUTPUT_FILE"
  mosquitto_passwd -b "$OUTPUT_FILE" "$MQTT_USER" "$MQTT_PASSWORD"

  chmod 600 "$OUTPUT_FILE"
  echo ""
  echo "=== MQTT Credentials Generated ==="
  echo ""
  echo "Password file: $OUTPUT_FILE"
  echo "Format: mosquitto_passwd hashed (ready to use)"
else
  echo "WARNING: mosquitto_passwd not found."
  echo "Creating a placeholder password file."
  echo ""

  cat > "$OUTPUT_FILE" <<EOF
# PLACEHOLDER - mosquitto_passwd was not available when this file was generated.
#
# To create a proper hashed password file, run one of:
#
#   mosquitto_passwd -c ${OUTPUT_FILE} ${MQTT_USER}
#
# Or, using Docker:
#
#   docker run --rm -v \$(pwd)/mosquitto:/mosquitto/config eclipse-mosquitto:2.0 \\
#     mosquitto_passwd -b /mosquitto/config/passwd ${MQTT_USER} '<password>'
#
# Then restart the Mosquitto container.
#
# For now, the plaintext credentials are shown below for reference only.
# DO NOT use this file directly with Mosquitto - it requires hashed passwords.
#
# User: ${MQTT_USER}
# Password: ${MQTT_PASSWORD}
EOF

  chmod 600 "$OUTPUT_FILE"
  echo ""
  echo "=== MQTT Credentials Generated (PLACEHOLDER) ==="
  echo ""
  echo "Password file: $OUTPUT_FILE (placeholder - see file for manual steps)"
fi

echo ""
echo "Add these to your .env file:"
echo "------------------------------------------------------------"
echo "MQTT_USER=${MQTT_USER}"
echo "MQTT_PASSWORD=${MQTT_PASSWORD}"
echo "------------------------------------------------------------"
echo ""
echo "Done."
