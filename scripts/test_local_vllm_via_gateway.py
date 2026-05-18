"""End-to-end smoke test: route a chat completion through the
production LLMGateway. Forces priority-1 (DGX Spark vLLM) by
restricting the fallback list, so this proves the local provider
is reachable via the gateway code path — not just via raw curl.

Usage (from repo root):

    apps/api/.venv/bin/pip install litellm tiktoken openai
    apps/api/.venv/bin/python scripts/test_local_vllm_via_gateway.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make `app` importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.services.reliability.llm_gateway import _load_fallback_models  # noqa: E402
from app.services.reliability.llm_gateway import LLMGateway  # noqa: E402


async def main() -> int:
    chain = _load_fallback_models()
    primary_only = [chain[0]]  # priority-1 (hosted_vllm/...)

    print(f"Primary tier: {primary_only[0]['model']}")
    print(f"  api_base = {primary_only[0].get('api_base')}")
    print(f"  api_key set = {bool(primary_only[0].get('api_key'))}")

    gw = LLMGateway(fallback_models=primary_only)

    # gpt-oss-120b emits reasoning before final content (Harmony format),
    # so leave room for both — 16 tokens is enough for the reasoning to
    # truncate before content is emitted.
    result = await gw.complete(
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        agent_name="scaffold-smoke",
        max_tokens=200,
    )

    print(f"\nresult.model        : {result['model']}")
    print(f"result.input_tokens : {result['input_tokens']}")
    print(f"result.output_tokens: {result['output_tokens']}")
    print(f"result.content      : {result['content']!r}")

    if not result.get("content"):
        print("FAIL: empty content from gateway")
        return 1
    if "hosted_vllm" not in result["model"]:
        print(f"FAIL: gateway returned wrong model tier: {result['model']!r}")
        return 1
    print("\nT6 PASS")
    return 0


if __name__ == "__main__":
    # Ensure the env vars the gateway reads are set. The test depends on
    # /etc/spark-vllm.env being readable for the API key.
    if not os.environ.get("LOCAL_VLLM_API_KEY"):
        try:
            for line in Path("/etc/spark-vllm.env").read_text().splitlines():
                if line.startswith("VLLM_API_KEY="):
                    os.environ["LOCAL_VLLM_API_KEY"] = line.split("=", 1)[1]
        except PermissionError:
            print("error: /etc/spark-vllm.env not readable; export LOCAL_VLLM_API_KEY", file=sys.stderr)
            sys.exit(2)

    sys.exit(asyncio.run(main()))
