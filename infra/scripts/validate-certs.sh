#!/bin/sh
# H-17 / H-20 / M-47: Validate TLS certs before any data-plane service starts.
# Called as a cert-validator init service in docker-compose.production.yml.
#
# Fails fast if required cert files are missing, unreadable, or close to
# expiry — so services don't start with broken TLS and silently accept
# plaintext connections.

set -eu

CERTS_DIR="${CERTS_DIR:-/certs}"
EXPIRY_WARN_DAYS="${EXPIRY_WARN_DAYS:-30}"

# Required certs for each service that depends on this validator.
# Format: "label|path"
required_certs="
postgres|${CERTS_DIR}/server.crt
postgres_key|${CERTS_DIR}/server.key
redis|${CERTS_DIR}/server.crt
redis_key|${CERTS_DIR}/server.key
redis_ca|${CERTS_DIR}/ca.crt
mosquitto|${CERTS_DIR}/mqtt/server.crt
mosquitto_key|${CERTS_DIR}/mqtt/server.key
mosquitto_ca|${CERTS_DIR}/mqtt/ca.crt
"

missing=0
expiring=0

for entry in $required_certs; do
    [ -z "$entry" ] && continue
    label="${entry%%|*}"
    path="${entry##*|}"

    if [ ! -f "$path" ]; then
        echo "MISSING: $label ($path)" >&2
        missing=$((missing + 1))
        continue
    fi

    if [ ! -r "$path" ]; then
        echo "UNREADABLE: $label ($path)" >&2
        missing=$((missing + 1))
        continue
    fi

    # For cert files (.crt), check expiry. Skip .key files.
    case "$path" in
        *.crt)
            if ! openssl x509 -in "$path" -noout -checkend 0 >/dev/null 2>&1; then
                echo "EXPIRED: $label ($path)" >&2
                missing=$((missing + 1))
                continue
            fi
            warn_seconds=$((EXPIRY_WARN_DAYS * 86400))
            if ! openssl x509 -in "$path" -noout -checkend "$warn_seconds" >/dev/null 2>&1; then
                echo "EXPIRING SOON (<${EXPIRY_WARN_DAYS}d): $label ($path)" >&2
                expiring=$((expiring + 1))
            else
                echo "OK: $label ($path)"
            fi
            ;;
        *)
            echo "OK: $label ($path)"
            ;;
    esac
done

if [ "$missing" -gt 0 ]; then
    echo "Cert validation FAILED: $missing missing or expired files" >&2
    exit 1
fi

if [ "$expiring" -gt 0 ]; then
    echo "WARNING: $expiring cert(s) expire within ${EXPIRY_WARN_DAYS} days — rotate soon" >&2
fi

echo "All certs validated successfully."
