"""WebSocket connection load test: 1000 concurrent connections."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def connect_websocket(
    url: str,
    client_id: int,
    duration: int,
):
    """Connect a single WebSocket client."""
    try:
        import websockets

        async with websockets.connect(
            f"{url}?client_id={client_id}",
        ) as ws:
            start = time.monotonic()
            messages_received = 0
            while time.monotonic() - start < duration:
                try:
                    await asyncio.wait_for(
                        ws.recv(),
                        timeout=5.0,
                    )
                    messages_received += 1
                except TimeoutError:
                    # Send keepalive
                    await ws.ping()
            return {
                "client_id": client_id,
                "messages": messages_received,
                "duration": duration,
                "status": "ok",
            }
    except ImportError:
        logger.warning("websockets package not installed")
        return {
            "client_id": client_id,
            "status": "skipped",
        }
    except Exception as e:
        return {
            "client_id": client_id,
            "status": "error",
            "error": str(e),
        }


async def run_load_test(
    url: str,
    connections: int,
    duration: int,
):
    """Run WebSocket load test with N connections."""
    logger.info(
        "Starting %d WebSocket connections to %s for %ds",
        connections,
        url,
        duration,
    )
    tasks = [connect_websocket(url, i, duration) for i in range(connections)]
    results = await asyncio.gather(*tasks)
    ok = sum(1 for r in results if r["status"] == "ok")
    errors = sum(1 for r in results if r["status"] == "error")
    logger.info(
        "Results: %d ok, %d errors out of %d",
        ok,
        errors,
        connections,
    )
    return {
        "total": connections,
        "ok": ok,
        "errors": errors,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/ws/alerts",
    )
    parser.add_argument(
        "--connections",
        type=int,
        default=1000,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        run_load_test(
            args.url,
            args.connections,
            args.duration,
        ),
    )
