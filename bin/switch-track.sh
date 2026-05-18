#!/usr/bin/env bash
# switch-track.sh — A/B swap between Spark 1 LLM tracks for testing.
#
# Track A = gpt-oss-120b (default, proven, ~35-58 tok/s)
# Track B = Qwen3.5-122B-A10B INT4+FP8 hybrid (SOTA, ~51 tok/s, qwen3 reasoning parser)
#
# Both tracks serve the same model name `constructai-primary` on Spark 1's
# port 8000, so the gateway on Spark 2 needs no change. Only the systemd unit
# behind that port swaps. systemd `Conflicts=` enforces that exactly one
# track is active at a time.
#
# Usage:
#   bin/switch-track.sh a            # switch Spark 1 to Track A
#   bin/switch-track.sh b            # switch Spark 1 to Track B (see infra/spark-vllm-track-b/README.md for provisioning)
#   bin/switch-track.sh status       # which track is currently active
#
# Requires:
#   - SSH access from Spark 2 to Spark 1 (passwordless, ~/.ssh/config alias `spark1`)
#   - LOCAL_VLLM_API_KEY in environment (for the readiness curl)
#
# First-time Track B start can take 5-15 min while FlashInfer JIT compiles
# against compute_120f. Subsequent restarts within the same kernel cache are
# 2-3 min. NEVER run this mid-demo — model swap = ~3 min downtime.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO_ROOT/.env" ] && set -a && source "$REPO_ROOT/.env" && set +a

VLLM_API_KEY="${LOCAL_VLLM_API_KEY:-${VLLM_API_KEY:-}}"
if [ -z "$VLLM_API_KEY" ]; then
  echo "warn: LOCAL_VLLM_API_KEY not set; readiness check will fail until you set it" >&2
fi

VLLM_HOST="${LOCAL_VLLM_BASE_URL:-http://spark1:8000/v1}"
VLLM_HOST="${VLLM_HOST%/v1}"

SPARK1_HOST="${SPARK1_SSH_HOST:-spark1}"
TRACK_A_UNIT="${SPARK1_TRACK_A_UNIT:-spark-vllm.service}"
TRACK_B_UNIT="${SPARK1_TRACK_B_UNIT:-spark-vllm-track-b.service}"

action="${1:-status}"

active_track() {
  # Returns "a", "b", or "none". Inspects which Spark 1 unit is active.
  local a b
  a=$(ssh -o ConnectTimeout=5 "$SPARK1_HOST" "systemctl is-active $TRACK_A_UNIT" 2>/dev/null || echo "inactive")
  b=$(ssh -o ConnectTimeout=5 "$SPARK1_HOST" "systemctl is-active $TRACK_B_UNIT" 2>/dev/null || echo "inactive")
  if [ "$a" = "active" ]; then echo "a"
  elif [ "$b" = "active" ]; then echo "b"
  else echo "none"; fi
}

served_model_id() {
  curl -fsS -m 10 -H "Authorization: Bearer $VLLM_API_KEY" \
    "${VLLM_HOST}/v1/models" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null \
    || echo "(unreachable)"
}

wait_for_ready() {
  local label=$1 max_iters=${2:-90}  # 90 iters * 10s = 15 min for first cold start
  echo "Waiting for ${label} to come up... (up to $((max_iters * 10 / 60)) min)"
  for i in $(seq 1 "$max_iters"); do
    if curl -fsS -m 5 -H "Authorization: Bearer $VLLM_API_KEY" \
        "${VLLM_HOST}/v1/models" >/dev/null 2>&1; then
      echo "[${label}] READY (${i}0 s)"
      return 0
    fi
    sleep 10
    if [ $((i % 6)) -eq 0 ]; then
      echo "  still loading... ($((i * 10)) s elapsed)"
    fi
  done
  echo "[${label}] WARN: did not come up within deadline. Check journalctl on Spark 1." >&2
  return 1
}

case "$action" in
  status|"")
    cur=$(active_track)
    served=$(served_model_id)
    echo "active track unit: $cur"
    echo "served-model-name: $served"
    if [ "$cur" = "a" ]; then
      echo "  Track A = gpt-oss-120b"
    elif [ "$cur" = "b" ]; then
      echo "  Track B = Qwen3.5-122B-A10B INT4+FP8 hybrid"
    fi
    ;;

  a|A)
    cur=$(active_track)
    if [ "$cur" = "a" ]; then
      echo "already on Track A; nothing to do"
      exit 0
    fi
    echo "switching Spark 1 from Track ${cur^^} -> Track A (gpt-oss-120b)"
    ssh "$SPARK1_HOST" "sudo systemctl stop $TRACK_B_UNIT 2>/dev/null || true; sudo systemctl start $TRACK_A_UNIT"
    wait_for_ready "Track A" 30  # warm restart, 5 min cap
    served=$(served_model_id)
    echo "now serving: $served"
    ;;

  b|B)
    cur=$(active_track)
    if [ "$cur" = "b" ]; then
      echo "already on Track B; nothing to do"
      exit 0
    fi
    # Verify Track B is provisioned before stopping Track A.
    exists=$(ssh "$SPARK1_HOST" "systemctl list-unit-files $TRACK_B_UNIT 2>/dev/null | grep -c $TRACK_B_UNIT" || true)
    if [ "${exists:-0}" -eq 0 ]; then
      echo "ERROR: $TRACK_B_UNIT not installed on Spark 1." >&2
      echo "Provision Track B first (see infra/spark-vllm-track-b/README.md;" >&2
      echo "image build + weight pull is ~90 min unattended)." >&2
      exit 1
    fi
    echo "switching Spark 1 from Track ${cur^^} -> Track B (Qwen3.5-122B-A10B SOTA)"
    echo "FIRST start can take 5-15 min for FlashInfer JIT; subsequent ~3 min."
    ssh "$SPARK1_HOST" "sudo systemctl stop $TRACK_A_UNIT 2>/dev/null || true; sudo systemctl start $TRACK_B_UNIT"
    wait_for_ready "Track B" 90  # 15 min cap for first cold start
    served=$(served_model_id)
    echo "now serving: $served"
    ;;

  *)
    cat >&2 <<USAGE
usage: $0 {a|b|status}

  a       switch Spark 1 to Track A (gpt-oss-120b, default demo path)
  b       switch Spark 1 to Track B (Qwen3.5-122B-A10B SOTA, opt-in)
  status  print which track is currently serving Spark 1's port 8000

env (override defaults via these):
  SPARK1_SSH_HOST        SSH alias for Spark 1 (default: spark1)
  SPARK1_TRACK_A_UNIT    systemd unit for Track A (default: spark-vllm.service)
  SPARK1_TRACK_B_UNIT    systemd unit for Track B (default: spark-vllm-track-b.service)
  LOCAL_VLLM_API_KEY     vLLM bearer token for /v1/models check
  LOCAL_VLLM_BASE_URL    e.g. http://spark1:8000/v1 (the script strips /v1 for /v1/models)
USAGE
    exit 1
    ;;
esac
