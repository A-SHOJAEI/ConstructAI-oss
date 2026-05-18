#!/usr/bin/env bash
# Between-session demo reset for one tenant. Idempotent. Targets ~30-60 sec.
#
# Usage:
#   bin/reset-demo.sh 01     # reset demo_session_01
#   bin/reset-demo.sh 02
#   ...
#
# Operator workflow: between two demo runs for the same tenant slot, run this
# to clear cached LLM responses, transient session rows, and pre-warm both
# LLMs so the first demo question doesn't pay cold-load latency.

set -euo pipefail

if [[ $# -ne 1 ]] || ! [[ "$1" =~ ^0[1-6]$ ]]; then
  echo "usage: $0 <01..06>"
  exit 1
fi
N="$1"
TENANT="demo_session_${N}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
set -a; source .env; set +a

echo "[1/7] drop FS cache (Spark 2)"
# Best-effort: passwordless sudo or quiet failure. Cache-drop is a polish, not
# a correctness requirement.
sudo -n sync 2>/dev/null && echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1 \
  && echo "  - Spark 2 cache dropped" \
  || echo "  - Spark 2 cache drop skipped (no passwordless sudo; not fatal)"
# Drop FS cache on the primary inference node. Configure via env vars:
#   PRIMARY_LLM_HOST — SSH alias for the primary node (default: spark1)
#   PRIMARY_LLM_USER — SSH user (default: root)
PRIMARY_LLM_HOST="${PRIMARY_LLM_HOST:-spark1}"
PRIMARY_LLM_USER="${PRIMARY_LLM_USER:-root}"
ssh -o ConnectTimeout=2 -o BatchMode=yes "${PRIMARY_LLM_USER}@${PRIMARY_LLM_HOST}" \
    "sync && echo 3 > /proc/sys/vm/drop_caches" 2>/dev/null \
  && echo "  - Primary LLM node cache dropped" \
  || echo "  - Primary LLM node cache drop skipped (no SSH or unreachable; not fatal)"

echo "[2/7] clear semantic cache for ${TENANT}"
sg docker -c "docker compose -f infra/docker-compose.yml -f infra/docker-compose.demo.yml exec -T redis \
  redis-cli -a '${REDIS_PASSWORD}' --scan --pattern 'semantic_cache:tenant:${TENANT}:*'" \
  | xargs -r -n 100 -I {} sg docker -c "docker compose -f infra/docker-compose.yml -f infra/docker-compose.demo.yml exec -T redis redis-cli -a '${REDIS_PASSWORD}' del {}" \
  || echo "  - cache clear had no entries (clean state)"

echo "[3/7] reset transient session state for ${TENANT} (RAG index preserved)"
cd apps/api && .venv/bin/python scripts/reset_demo_session.py --tenant "${TENANT}" && cd "$REPO_ROOT"

echo "[4/7] (skipped) Celery worker restart — Celery not running in this scaffold"

echo "[5/7] pre-warm Ollama gpt-oss:20b"
curl -fsS -m 15 http://localhost:11434/v1/chat/completions \
  -H "Authorization: Bearer ollama" -H "Content-Type: application/json" \
  -d '{"model":"gpt-oss:20b","messages":[{"role":"user","content":"warmup"}],"max_tokens":8}' \
  >/dev/null && echo "  - Ollama warm" || echo "  - Ollama warm-up failed (recoverable)"

echo "[6/7] pre-warm Spark 1 vLLM gpt-oss-120b"
for q in \
  "What is the cure time for a 4-inch concrete slab?" \
  "Summarize OSHA 1926 fall protection." \
  "Draft a brief RFI response."; do
  curl -fsS -m 30 "${LOCAL_VLLM_BASE_URL}/chat/completions" \
    -H "Authorization: Bearer ${LOCAL_VLLM_API_KEY}" -H "Content-Type: application/json" \
    -d "$(jq -nc --arg q "$q" --arg m "${LOCAL_VLLM_MODEL_NAME}" \
      '{model:$m,messages:[{role:"user",content:$q}],max_tokens:48}')" \
    >/dev/null && echo "  - warm: $q" \
    || echo "  - warm failed: $q (recoverable)"
done

echo "[7/7] health checks"
curl -fsS http://localhost:8000/api/v1/health >/dev/null \
  && echo "  - api OK" || { echo "  - API NOT HEALTHY"; exit 1; }
curl -fsS http://localhost:11434/v1/models >/dev/null \
  && echo "  - ollama OK" || echo "  - ollama NOT REACHABLE (recoverable)"
curl -fsS "${LOCAL_VLLM_BASE_URL}/models" \
  -H "Authorization: Bearer ${LOCAL_VLLM_API_KEY}" >/dev/null \
  && echo "  - vllm OK" || echo "  - vLLM NOT REACHABLE (recoverable)"

echo ""
echo "READY for ${TENANT}"
echo ""
echo "Open http://localhost:3000 — log in as:"
echo "  email:    demo.pm@${TENANT}.test"
echo "  password: demo-password-${TENANT}"
