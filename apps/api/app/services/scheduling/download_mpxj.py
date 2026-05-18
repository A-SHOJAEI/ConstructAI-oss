"""Download MPXJ JAR files for schedule parsing.

Run as: python -m app.services.scheduling.download_mpxj

Downloads the MPXJ uber-JAR from Maven Central into the lib/ directory
adjacent to this module.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MPXJ_VERSION = "13.4.0"
MAVEN_BASE = "https://repo1.maven.org/maven2/net/sf/mpxj/mpxj"
JAR_URL = f"{MAVEN_BASE}/{MPXJ_VERSION}/mpxj-{MPXJ_VERSION}.jar"

LIB_DIR = Path(__file__).parent / "lib"


def download_mpxj_jars(target_dir: Path | None = None) -> Path:
    """Download the MPXJ JAR to *target_dir* (default: ``lib/``)."""
    dest = target_dir or LIB_DIR
    dest.mkdir(parents=True, exist_ok=True)

    jar_path = dest / f"mpxj-{MPXJ_VERSION}.jar"
    if jar_path.exists():
        logger.info("MPXJ JAR already present: %s", jar_path)
        return jar_path

    logger.info("Downloading MPXJ %s from Maven Central ...", MPXJ_VERSION)
    if not JAR_URL.startswith("https://repo1.maven.org/"):
        raise ValueError(f"Refusing to download from non-Maven URL: {JAR_URL}")
    # URL scheme validated above; downloading to a known build directory.
    urllib.request.urlretrieve(JAR_URL, str(jar_path))  # nosec B310
    logger.info("Saved to %s (%d bytes)", jar_path, jar_path.stat().st_size)
    return jar_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_mpxj_jars()
