#!/usr/bin/env bash
#
# Wrapper invoked by spark-vllm-track-b.service. Avoids systemd's
# brittle quoting of the JSON --speculative-config arg by assembling
# the docker run command in a normal shell.
#
# Reads VLLM_API_KEY from the environment (systemd unit's
# EnvironmentFile=/etc/spark-vllm.env supplies it).
#
# Memory posture matches Track A (--gpu-memory-utilization 0.85,
# --max-model-len 32768) so the gateway sees behaviorally equivalent
# tracks. The Qwen-specific perf flags (FlashInfer + MTP-2 + qwen3
# reasoning + qwen3_xml tool-call parsing) come from the README's
# verified launch line, minus --enable-prefix-caching (crashes
# Qwen3.5 due to DeltaNet hybrid attention per albond troubleshooting).

set -euo pipefail

CONTAINER_NAME=spark-vllm-track-b
IMAGE=vllm-qwen35-v2:latest
MODEL_PATH=/models/qwen35-122b-hybrid-int4fp8
# Host directory containing model weights; mounted at /models in the
# container. Override via env (default: $HOME/models).
HOST_MODELS="${HOST_MODELS:-${HOME}/models}"

if [ -z "${VLLM_API_KEY:-}" ]; then
    echo "ERROR: VLLM_API_KEY not set (expected from EnvironmentFile)" >&2
    exit 1
fi

# Best-effort cleanup of any leftover container with the same name.
/usr/bin/docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

exec /usr/bin/docker run --rm --name "$CONTAINER_NAME" \
    --gpus all --ipc=host --net=host \
    -v "${HOST_MODELS}:/models" \
    "$IMAGE" \
    serve "$MODEL_PATH" \
    --host 0.0.0.0 --port 8000 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.85 \
    --served-model-name constructai-primary \
    --api-key "$VLLM_API_KEY" \
    --reasoning-parser qwen3 \
    --attention-backend FLASHINFER \
    --speculative-config '{"method":"mtp","num_speculative_tokens":2}' \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --enable-chunked-prefill
