# Track B systemd unit (Qwen3.5-122B-A10B SOTA)

Source of truth for the Track B service. The deployed copies live at:

| Source in repo | Deployed path | Permissions |
|---|---|---|
| `start-track-b.sh` | `/usr/local/bin/start-track-b.sh` | root:root 755 |
| `spark-vllm-track-b.service` | `/etc/systemd/system/spark-vllm-track-b.service` | root:root 644 |

After editing source files in this directory, redeploy:

```bash
sudo cp infra/spark-vllm-track-b/start-track-b.sh /usr/local/bin/start-track-b.sh
sudo chmod 755 /usr/local/bin/start-track-b.sh
sudo cp infra/spark-vllm-track-b/spark-vllm-track-b.service /etc/systemd/system/spark-vllm-track-b.service
sudo systemctl daemon-reload
```

Track B is **disabled by default** — operator opts in with `bin/switch-track.sh b`. `Conflicts=spark-vllm.service` enforces mutual exclusion with Track A.

## Building the image and fetching weights

Track B requires:
1. A vLLM container image tagged `vllm-qwen35-v2:latest` (Qwen3.5-Next 80B/122B-A10B hybrid). Build from the upstream `vllm-project/vllm` repo on an `aarch64+CUDA` base; pin FlashInfer and the qwen3 reasoning parser.
2. The model weights at `${HOST_MODELS}/qwen35-122b-hybrid-int4fp8/` in INT4+FP8 hybrid quantization. Pull from the model registry of your choice (HuggingFace, model server, etc.) and pre-quantize with the layout vLLM expects.

The `HOST_MODELS` env var controls the host-side mount point (default: `${HOME}/models`).
