"""Fetch and cache historical BLS Producer Price Index data for construction materials.

Downloads PPI series from the Bureau of Labor Statistics public API and saves
them as JSON files for offline use by the cost database and price forecaster.

The BLS public API (v2) allows up to 500 requests/day without a key,
and 500 requests/day with a key (up to 50 series per request).

Usage:
    # Fetch all construction material PPI series (last 10 years)
    python -m ml.training.fetch_bls_historical

    # Fetch with API key for higher rate limits
    BLS_API_KEY=your_key python -m ml.training.fetch_bls_historical

    # Fetch specific years
    python -m ml.training.fetch_bls_historical --start-year 2015 --end-year 2025
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BLS PPI Series IDs for Construction Materials
# ---------------------------------------------------------------------------

# Source: https://www.bls.gov/ppi/
# These are Producer Price Index series for key construction inputs.
BLS_CONSTRUCTION_SERIES = {
    # Concrete & cement
    "PCU327310327310": "Ready-mixed concrete",
    "PCU327310327310P": "Ready-mixed concrete (primary)",
    "WPU1321": "Portland cement",

    # Steel & metals
    "PCU33121033121011": "Iron and steel (hot-rolled bars)",
    "WPU1017": "Steel mill products",
    "WPU10250113": "Steel structural shapes",
    "WPU10260141": "Reinforcing bars (rebar)",
    "PCU331111331111": "Iron and steel mills",

    # Lumber & wood
    "WPU0811": "Softwood lumber",
    "WPU0831": "Millwork",
    "WPU0841": "Plywood",
    "PCU321113321113": "Sawmills",

    # Aggregates & asphalt
    "PCU327320327320": "Asphalt paving mixtures",
    "WPU1321": "Cement",
    "PCU212312212312": "Crushed stone",
    "PCU212321212321": "Sand and gravel",

    # Copper & electrical
    "WPU10230101": "Copper wire and cable",
    "PCU335313335313": "Switchgear and switchboard apparatus",

    # Fuel & energy
    "WPU0573": "Diesel fuel",
    "WPU0543": "Gasoline",

    # General construction inputs
    "PCU23232X": "Construction machinery manufacturing",
    "WPUFD4131": "Construction materials, general",
    "WPUIP231100": "Inputs to new nonresidential construction",
    "WPUIP231200": "Inputs to new residential construction",
    "WPUIP232100": "Inputs to maintenance and repair construction",
}


def fetch_bls_series(
    series_ids: list[str],
    start_year: int,
    end_year: int,
    api_key: str | None = None,
) -> dict:
    """Fetch PPI data from BLS API v2.

    Returns dict mapping series_id to list of observations:
    [{year, period, value, footnotes}, ...]
    """
    import requests

    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

    # BLS API allows max 50 series per request
    results: dict[str, list[dict]] = {}

    for i in range(0, len(series_ids), 50):
        batch = series_ids[i : i + 50]

        payload: dict = {
            "seriesid": batch,
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if api_key:
            payload["registrationkey"] = api_key

        logger.info(
            "Fetching BLS data: %d series, years %d-%d (batch %d/%d)",
            len(batch), start_year, end_year,
            i // 50 + 1, (len(series_ids) + 49) // 50,
        )

        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "REQUEST_SUCCEEDED":
            logger.warning("BLS API response: %s", data.get("message", "Unknown error"))
            continue

        for series in data.get("Results", {}).get("series", []):
            sid = series["seriesID"]
            observations = []
            for entry in series.get("data", []):
                try:
                    observations.append({
                        "year": int(entry["year"]),
                        "period": entry["period"],
                        "value": float(entry["value"]),
                        "footnotes": entry.get("footnotes", []),
                    })
                except (ValueError, KeyError) as exc:
                    logger.warning("Skipping bad entry in %s: %s", sid, exc)

            # Sort chronologically (API returns newest first)
            observations.sort(key=lambda o: (o["year"], o["period"]))
            results[sid] = observations
            logger.info("  %s: %d observations", sid, len(observations))

        # Rate limiting: pause between batches
        if i + 50 < len(series_ids):
            time.sleep(2)

    return results


def save_to_json(data: dict, output_path: Path) -> None:
    """Save BLS data to JSON with metadata."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    export = {
        "metadata": {
            "source": "Bureau of Labor Statistics (BLS) Public API v2",
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "series_count": len(data),
            "total_observations": sum(len(v) for v in data.values()),
        },
        "series_descriptions": {
            sid: BLS_CONSTRUCTION_SERIES.get(sid, "Unknown")
            for sid in data
        },
        "data": data,
    }

    with open(output_path, "w") as f:
        json.dump(export, f, indent=2)

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info("Saved %d series to %s (%.1f MB)", len(data), output_path, size_mb)


def main():
    parser = argparse.ArgumentParser(description="Fetch BLS PPI historical data")
    parser.add_argument(
        "--output", type=Path,
        default=Path("./data/bls/construction_ppi_historical.json"),
        help="Output JSON file path",
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    api_key = os.environ.get("BLS_API_KEY")
    if api_key:
        logger.info("Using BLS API key for higher rate limits")
    else:
        logger.info("No BLS_API_KEY found; using public access (500 req/day)")

    series_ids = list(BLS_CONSTRUCTION_SERIES.keys())
    logger.info("Fetching %d PPI series for years %d-%d", len(series_ids), args.start_year, args.end_year)

    data = fetch_bls_series(
        series_ids=series_ids,
        start_year=args.start_year,
        end_year=args.end_year,
        api_key=api_key,
    )

    if data:
        save_to_json(data, args.output)
        logger.info("Done! Fetched %d series with %d total observations.",
                     len(data), sum(len(v) for v in data.values()))
    else:
        logger.error("No data fetched. Check your network connection and BLS API status.")


if __name__ == "__main__":
    main()
