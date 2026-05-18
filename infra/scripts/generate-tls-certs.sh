#!/usr/bin/env bash
set -euo pipefail

# Generate self-signed TLS certificates for ConstructAI services.
#
# This script generates:
#   1. A root CA (ca.crt / ca-key.pem)
#   2. Server certificates for the main services (server.crt / server.key)
#   3. MQTT-specific certs in $OUTPUT_DIR/mqtt/ (CN=mosquitto)
#   4. Kafka certs in $OUTPUT_DIR/kafka/ (CN=kafka, plus JKS stores if keytool is available)
#
# Usage:
#   ./generate-tls-certs.sh [options]
#
# Options:
#   --output-dir DIR   Output directory (default: ./certs)
#   --domain DOMAIN    Primary domain / CN for server certs (default: localhost)
#   --days N           Certificate validity in days (default: 365)
#   --force            Regenerate all certificates even if they already exist
#
# Examples:
#   ./generate-tls-certs.sh
#   ./generate-tls-certs.sh --output-dir /etc/constructai/certs --domain constructai.example.com
#   ./generate-tls-certs.sh --force

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
OUTPUT_DIR="./certs"
DOMAIN="localhost"
DAYS=365
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --domain)
      DOMAIN="$2"
      shift 2
      ;;
    --days)
      DAYS="$2"
      shift 2
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      sed -n '3,/^$/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
GENERATED_FILES=()

_need_generate() {
  # Returns 0 (true) when the file does NOT exist or --force was given.
  local file="$1"
  if [[ "$FORCE" == "true" ]]; then
    return 0
  fi
  [[ ! -f "$file" ]]
}

_track() {
  # Record the path for the final summary.
  GENERATED_FILES+=("$1")
}

_san_config() {
  # Write a SAN extension config for openssl.
  local file="$1"
  shift
  cat > "$file" <<SANEOF
[req_ext]
subjectAltName = @alt_names
[alt_names]
SANEOF
  local idx=1
  for name in "$@"; do
    echo "DNS.${idx} = ${name}" >> "$file"
    idx=$((idx + 1))
  done
  # Always include localhost and 127.0.0.1 for development convenience.
  echo "DNS.${idx} = localhost" >> "$file"
  idx=$((idx + 1))
  echo "IP.1 = 127.0.0.1" >> "$file"
}

_generate_signed_cert() {
  # Generate a key + CSR, sign it with the CA, and clean up intermediates.
  #   $1 = output directory
  #   $2 = CN (Common Name)
  #   $3 = extra SAN DNS entries (comma-separated, optional)
  local dir="$1"
  local cn="$2"
  local extra_sans="${3:-}"

  mkdir -p "$dir"

  if _need_generate "$dir/server.crt"; then
    echo "  Generating server key and certificate for CN=${cn} ..."
    openssl genrsa -out "$dir/server.key" 2048 2>/dev/null

    openssl req -new -sha256 \
      -key "$dir/server.key" \
      -out "$dir/server.csr" \
      -subj "/C=US/ST=CA/O=ConstructAI/CN=${cn}"

    # Build SAN list
    local san_args=("$cn")
    if [[ -n "$extra_sans" ]]; then
      IFS=',' read -ra extras <<< "$extra_sans"
      san_args+=("${extras[@]}")
    fi
    _san_config "$dir/san.cnf" "${san_args[@]}"

    openssl x509 -req -sha256 -days "$DAYS" \
      -in "$dir/server.csr" \
      -CA "$OUTPUT_DIR/ca.crt" \
      -CAkey "$OUTPUT_DIR/ca-key.pem" \
      -CAcreateserial \
      -out "$dir/server.crt" \
      -extfile "$dir/san.cnf" \
      -extensions req_ext 2>/dev/null

    # Restrict private key permissions
    chmod 600 "$dir/server.key"
    chmod 644 "$dir/server.crt"

    # Clean up intermediates
    rm -f "$dir/server.csr" "$dir/san.cnf" "$OUTPUT_DIR/ca.srl"

    _track "$dir/server.crt"
    _track "$dir/server.key"
  else
    echo "  Skipping $dir/server.crt (already exists; use --force to regenerate)"
  fi

  # Always copy the CA cert into subdirectories so services can mount a single dir.
  if [[ "$dir" != "$OUTPUT_DIR" ]]; then
    cp -f "$OUTPUT_DIR/ca.crt" "$dir/ca.crt"
    _track "$dir/ca.crt"
  fi
}

# ---------------------------------------------------------------------------
# 1. Root CA
# ---------------------------------------------------------------------------
echo "=== ConstructAI TLS Certificate Generator ==="
echo ""
mkdir -p "$OUTPUT_DIR"

if _need_generate "$OUTPUT_DIR/ca.crt"; then
  echo "[1/4] Generating root CA ..."
  openssl genrsa -out "$OUTPUT_DIR/ca-key.pem" 4096 2>/dev/null
  openssl req -new -x509 -sha256 -days "$DAYS" \
    -key "$OUTPUT_DIR/ca-key.pem" \
    -out "$OUTPUT_DIR/ca.crt" \
    -subj "/C=US/ST=CA/O=ConstructAI/CN=ConstructAI CA"

  chmod 600 "$OUTPUT_DIR/ca-key.pem"
  chmod 644 "$OUTPUT_DIR/ca.crt"

  _track "$OUTPUT_DIR/ca.crt"
  _track "$OUTPUT_DIR/ca-key.pem"
else
  echo "[1/4] Root CA already exists; skipping (use --force to regenerate)"
fi

# ---------------------------------------------------------------------------
# 2. Main server certificates (PostgreSQL, Redis, API, etc.)
# ---------------------------------------------------------------------------
echo "[2/4] Generating main server certificates ..."
_generate_signed_cert "$OUTPUT_DIR" "$DOMAIN" "*.$DOMAIN"

# ---------------------------------------------------------------------------
# 3. MQTT certificates (different CN for Mosquitto)
# ---------------------------------------------------------------------------
echo "[3/4] Generating MQTT certificates ..."
_generate_signed_cert "$OUTPUT_DIR/mqtt" "mosquitto" "mqtt,mqtt.$DOMAIN"

# ---------------------------------------------------------------------------
# 4. Kafka certificates (JKS keystores if keytool is available)
# ---------------------------------------------------------------------------
echo "[4/4] Generating Kafka certificates ..."
_generate_signed_cert "$OUTPUT_DIR/kafka" "kafka" "kafka-1,kafka-2,kafka-3,kafka.$DOMAIN"

# Generate JKS keystore and truststore for Kafka if keytool is available.
KAFKA_DIR="$OUTPUT_DIR/kafka"
STORE_PASS="${KAFKA_SSL_KEYSTORE_PASSWORD:-changeit}"

if command -v keytool &>/dev/null; then
  if _need_generate "$KAFKA_DIR/kafka.keystore.jks"; then
    echo "  Creating Kafka JKS keystore and truststore ..."

    # Create a PKCS12 bundle first, then import into JKS.
    openssl pkcs12 -export \
      -in "$KAFKA_DIR/server.crt" \
      -inkey "$KAFKA_DIR/server.key" \
      -CAfile "$OUTPUT_DIR/ca.crt" \
      -chain \
      -name kafka \
      -password "pass:${STORE_PASS}" \
      -out "$KAFKA_DIR/kafka.p12" 2>/dev/null

    keytool -importkeystore \
      -srckeystore "$KAFKA_DIR/kafka.p12" \
      -srcstoretype PKCS12 \
      -srcstorepass "${STORE_PASS}" \
      -destkeystore "$KAFKA_DIR/kafka.keystore.jks" \
      -deststoretype JKS \
      -deststorepass "${STORE_PASS}" \
      -noprompt 2>/dev/null

    rm -f "$KAFKA_DIR/kafka.p12"
    _track "$KAFKA_DIR/kafka.keystore.jks"

    # Truststore: import the CA cert.
    keytool -importcert \
      -keystore "$KAFKA_DIR/kafka.truststore.jks" \
      -storepass "${STORE_PASS}" \
      -alias CARoot \
      -file "$OUTPUT_DIR/ca.crt" \
      -noprompt 2>/dev/null

    _track "$KAFKA_DIR/kafka.truststore.jks"
  else
    echo "  Skipping Kafka JKS stores (already exist; use --force to regenerate)"
  fi
else
  echo "  WARNING: keytool not found. JKS keystores were NOT generated."
  echo "  Kafka PEM certs are available; convert manually or install a JDK."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "Output directory: $OUTPUT_DIR"
echo ""
if [[ ${#GENERATED_FILES[@]} -gt 0 ]]; then
  echo "Generated / updated files:"
  for f in "${GENERATED_FILES[@]}"; do
    echo "  $f"
  done
else
  echo "No files were generated (all already exist). Use --force to regenerate."
fi
echo ""
echo "Docker-compose mount points expected:"
echo "  PostgreSQL : $OUTPUT_DIR/server.crt, $OUTPUT_DIR/server.key"
echo "  Redis      : $OUTPUT_DIR/server.crt, $OUTPUT_DIR/server.key, $OUTPUT_DIR/ca.crt"
echo "  MQTT       : $OUTPUT_DIR/mqtt/server.crt, $OUTPUT_DIR/mqtt/server.key, $OUTPUT_DIR/mqtt/ca.crt"
echo "  Kafka      : $OUTPUT_DIR/kafka/ (entire directory mounted as /etc/kafka/secrets)"
echo ""
echo "Done."
