"""Concurrent video stream load test: 50 streams."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def simulate_stream(
    base_url: str,
    stream_id: int,
    duration: int,
):
    """Simulate a video stream consumer."""
    try:
        import httpx

        url = f"{base_url}/api/v1/cameras/cam-{stream_id}/stream"
        frames_received = 0
        start = time.monotonic()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
        ) as client:
            while time.monotonic() - start < duration:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        frames_received += 1
                except Exception:
                    pass
                await asyncio.sleep(0.033)  # ~30fps
        return {
            "stream_id": stream_id,
            "frames": frames_received,
            "duration": duration,
            "status": "ok",
        }
    except ImportError:
        return {
            "stream_id": stream_id,
            "status": "skipped",
        }
    except Exception as e:
        return {
            "stream_id": stream_id,
            "status": "error",
            "error": str(e),
        }


async def run_stream_test(
    base_url: str,
    streams: int,
    duration: int,
):
    """Run concurrent video stream test."""
    logger.info(
        "Starting %d video streams for %ds",
        streams,
        duration,
    )
    tasks = [simulate_stream(base_url, i, duration) for i in range(streams)]
    results = await asyncio.gather(*tasks)
    ok = sum(1 for r in results if r["status"] == "ok")
    logger.info(
        "Results: %d ok out of %d streams",
        ok,
        streams,
    )
    return {
        "total": streams,
        "ok": ok,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
    )
    parser.add_argument(
        "--streams",
        type=int,
        default=50,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        run_stream_test(
            args.url,
            args.streams,
            args.duration,
        ),
    )
