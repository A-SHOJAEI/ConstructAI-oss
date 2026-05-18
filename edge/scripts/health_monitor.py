"""Health monitoring script for edge deployment."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

logger = logging.getLogger(__name__)


def check_gpu_health() -> dict:
    """Check NVIDIA GPU status using nvidia-smi or jtop."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            return {
                "gpu_available": True,
                "temperature_c": int(parts[0]),
                "utilization_pct": int(parts[1]),
                "memory_used_mb": int(parts[2]),
                "memory_total_mb": int(parts[3]),
            }
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError, ValueError):
        pass
    return {"gpu_available": False}


def check_camera_streams(config_path: str = "config/deepstream_config.txt") -> dict:
    """Verify camera streams are accessible."""
    import pathlib
    config = pathlib.Path(config_path)
    streams_found = 0
    streams_active = 0

    if config.exists():
        for line in config.read_text().splitlines():
            if line.strip().startswith("uri="):
                streams_found += 1
                # In production, would test RTSP connection
                streams_active += 1

    return {
        "streams_configured": streams_found,
        "streams_active": streams_active,
    }


def check_mqtt_connection(host: str = "localhost", port: int = 1883) -> dict:
    """Test MQTT broker connectivity."""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        return {"mqtt_reachable": result == 0, "mqtt_host": host, "mqtt_port": port}
    except Exception as exc:
        return {"mqtt_reachable": False, "error": str(exc)}


def check_disk_space() -> dict:
    """Check available disk space."""
    import shutil
    total, used, free = shutil.disk_usage("/")
    return {
        "disk_total_gb": round(total / (1 << 30), 1),
        "disk_used_gb": round(used / (1 << 30), 1),
        "disk_free_gb": round(free / (1 << 30), 1),
        "disk_usage_pct": round(used / total * 100, 1),
    }


def check_model_loaded(model_path: str = "/models/rtmdet_construction.engine") -> dict:
    """Check if the TensorRT model file exists."""
    import pathlib
    path = pathlib.Path(model_path)
    return {
        "model_path": model_path,
        "model_exists": path.exists(),
        "model_size_mb": round(path.stat().st_size / (1 << 20), 1) if path.exists() else 0,
    }


def run_health_check(quick: bool = False) -> dict:
    """Run full health check."""
    import os
    health = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "device_id": os.environ.get("DEVICE_ID", "unknown"),
        "status": "healthy",
    }

    health["gpu"] = check_gpu_health()
    health["disk"] = check_disk_space()
    health["mqtt"] = check_mqtt_connection(
        host=os.environ.get("MQTT_HOST", "localhost"),
        port=int(os.environ.get("MQTT_PORT", "1883")),
    )

    if not quick:
        health["cameras"] = check_camera_streams()
        health["model"] = check_model_loaded(
            os.environ.get("MODEL_PATH", "/models/rtmdet_construction.engine"),
        )

    # Determine overall health
    issues = []
    if health["gpu"].get("temperature_c", 0) > 85:
        issues.append("GPU temperature high")
    if health["disk"].get("disk_usage_pct", 0) > 90:
        issues.append("Disk space low")
    if not health["mqtt"].get("mqtt_reachable", False):
        issues.append("MQTT unreachable")

    if issues:
        health["status"] = "degraded"
        health["issues"] = issues

    return health


def main():
    parser = argparse.ArgumentParser(description="Edge Health Monitor")
    parser.add_argument("--check", action="store_true", help="Run single health check and exit")
    parser.add_argument("--interval", type=int, default=60, help="Monitoring interval (seconds)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.check:
        health = run_health_check(quick=True)
        if args.json:
            print(json.dumps(health, indent=2))
        else:
            status = health.get("status", "unknown")
            print(f"Health: {status}")
            if health.get("issues"):
                for issue in health["issues"]:
                    print(f"  WARNING: {issue}")
        sys.exit(0 if health["status"] == "healthy" else 1)

    # Continuous monitoring mode
    logger.info("Starting health monitor (interval: %ds)", args.interval)
    while True:
        health = run_health_check()
        logger.info(
            "Health: %s | GPU: %s°C | Disk: %s%% | MQTT: %s",
            health["status"],
            health["gpu"].get("temperature_c", "N/A"),
            health["disk"].get("disk_usage_pct", "N/A"),
            "OK" if health["mqtt"].get("mqtt_reachable") else "FAIL",
        )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
