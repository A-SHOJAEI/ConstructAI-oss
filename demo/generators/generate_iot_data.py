"""
Generate synthetic IoT sensor readings for demo purposes.

Simulates sensors:
  - Temperature/humidity sensors (4 locations)
  - Vibration sensor (foundation)
  - Noise level sensor (perimeter)
  - Dust (PM2.5) sensor (site entrance)

Usage:
    python -m demo.generators.generate_iot_data [output_path]
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)


def generate_iot_data(output_path: Path, days: int = 7) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    sensors = [
        ("TEMP-N01", "temperature", "north_gate", "celsius", 2, 18, 35.0, 5.0),
        ("TEMP-S01", "temperature", "south_gate", "celsius", 2, 18, 34.0, 6.0),
        ("HUM-N01", "humidity", "north_gate", "percent", 40, 85, 60.0, 15.0),
        ("VIB-F01", "vibration", "foundation", "mm_per_sec", 0.01, 0.5, 0.15, 0.08),
        ("NOISE-P01", "noise_level", "perimeter", "dBA", 55, 95, 72.0, 10.0),
        ("DUST-E01", "pm25", "entrance", "ug_per_m3", 5, 50, 18.0, 8.0),
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "sensor_id", "sensor_type", "location", "value", "unit", "quality"])

        current = start
        interval = timedelta(minutes=15)

        while current <= now:
            # Only generate during work hours (6 AM - 7 PM)
            hour = current.hour
            if 6 <= hour <= 19:
                for sid, stype, loc, unit, vmin, vmax, mean, std in sensors:
                    value = max(vmin, min(vmax, round(random.gauss(mean, std), 2)))
                    quality = "good" if vmin * 0.8 < value < vmax * 1.2 else "warning"
                    writer.writerow([
                        current.isoformat(),
                        sid, stype, loc,
                        value, unit, quality,
                    ])
            current += interval

    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/iot_data.csv")
    p = generate_iot_data(out)
    print(f"Generated: {p}")
