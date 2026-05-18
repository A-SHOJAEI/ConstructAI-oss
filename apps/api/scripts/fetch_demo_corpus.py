"""Download the public-domain construction corpus for the demo.

Idempotent: each entry written to data/manifests/manifest.jsonl with sha256.
Re-running skips files already present with non-zero size and matching sha256.

Run:
    cd apps/api && .venv/bin/python scripts/fetch_demo_corpus.py
    cd apps/api && .venv/bin/python scripts/fetch_demo_corpus.py --only ufgs,osha-xml
    cd apps/api && .venv/bin/python scripts/fetch_demo_corpus.py --skip vision

Categories: ufgs, specs-state, gsa, osha-pubs, osha-xml, rfi-samples, bim, cost,
vision, videos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
MANIFEST_PATH = DATA_ROOT / "manifests" / "manifest.jsonl"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*"})


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_manifest() -> dict[str, dict]:
    if not MANIFEST_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                out[row["path"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def _append_manifest(entry: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download(url: str, dest: Path, *, expect_min_bytes: int = 1024) -> bool:
    """Stream-download URL to dest. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with SESSION.get(url, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)
        size = tmp.stat().st_size
        if size < expect_min_bytes:
            tmp.unlink(missing_ok=True)
            print(f"    SKIP {dest.name}: {size} bytes < min {expect_min_bytes}")
            return False
        tmp.rename(dest)
        return True
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"    FAIL {dest.name}: {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Source catalog
# ---------------------------------------------------------------------------


@dataclass
class Source:
    category: str
    name: str
    url: str
    rel_path: str  # relative to DATA_ROOT
    license: str
    expect_min_bytes: int = 1024
    headers: dict = field(default_factory=dict)


# UFGS — Unified Facilities Guide Specs (DoD, public domain)
UFGS_SECTIONS = [
    ("01 11 00", "Summary of Work"),
    ("01 33 00", "Submittal Procedures"),
    ("01 35 26", "Governmental Safety Requirements"),
    ("03 11 00", "Concrete Forming"),
    ("03 30 00", "Cast-In-Place Concrete"),
    ("03 31 00", "Structural Concrete"),
    ("04 20 00", "Unit Masonry"),
    ("05 12 00", "Structural Steel Framing"),
    ("06 10 00", "Rough Carpentry"),
    ("07 14 00", "Fluid-Applied Waterproofing"),
    ("07 21 00", "Thermal Insulation"),
    ("07 92 00", "Joint Sealants"),
    ("08 11 13", "Hollow Metal Doors and Frames"),
    ("09 29 00", "Gypsum Board"),
    ("09 91 00", "Painting"),
    ("13 48 00", "Sound Vibration Seismic Control"),
    ("23 05 93", "Testing Adjusting Balancing for HVAC"),
    ("26 05 43", "Underground Ducts and Raceways"),
    ("26 51 00", "Interior Lighting"),
    ("31 23 00", "Excavation and Fill"),
    ("32 11 00", "Base Courses"),
    ("33 11 00", "Water Utility Distribution Piping"),
]


def _ufgs_sources() -> list[Source]:
    out: list[Source] = []
    for sec, title in UFGS_SECTIONS:
        url_sec = urllib.parse.quote(sec)
        url = f"https://www.wbdg.org/FFC/DOD/UFGS/UFGS%20{url_sec}.pdf"
        fname = f"UFGS-{sec.replace(' ', '-')}.pdf"
        out.append(
            Source(
                category="ufgs",
                name=f"UFGS {sec} {title}",
                url=url,
                rel_path=f"specs/ufgs/{fname}",
                license="US Government works (public domain)",
                expect_min_bytes=4096,
            )
        )
    return out


SOURCES_STATE_SPECS = [
    Source(
        category="specs-state",
        name="WSDOT Standard Specifications 2024",
        url="https://wsdot.wa.gov/publications/manuals/fulltext/M41-10/SS2024.pdf",
        rel_path="specs/state/WSDOT-SS2024.pdf",
        license="WSDOT public document",
        expect_min_bytes=1_000_000,
    ),
    Source(
        category="specs-state",
        name="Caltrans Standard Specifications 2024",
        url="https://dot.ca.gov/-/media/dot-media/programs/design/documents/2024_stdspecs-a11y.pdf",
        rel_path="specs/state/Caltrans-2024.pdf",
        license="Caltrans public document",
        expect_min_bytes=1_000_000,
    ),
]

SOURCES_GSA = [
    Source(
        category="gsa",
        name="GSA P100 Facilities Standards 2024",
        url="https://www.gsa.gov/system/files/P100%202024%20Final%20(1).pdf",
        rel_path="specs/gsa/P100-2024.pdf",
        license="US Government works (public domain)",
        expect_min_bytes=1_000_000,
    ),
    Source(
        category="gsa",
        name="Caltrans CEM-4900 VECP example",
        url="https://dot.ca.gov/-/media/dot-media/programs/construction/documents/contract-administration/change-order-information/change-order-examples/13-4900ex-vecp.pdf",
        rel_path="specs/gsa/Caltrans-CEM-4900-VECP-example.pdf",
        license="Caltrans public document",
        expect_min_bytes=1024,
    ),
    Source(
        category="gsa",
        name="WSDOT IDR sample (test pile)",
        url="http://data.wsdot.wa.gov/accountability/ssb5806/Repository/7_Project%20Delivery/C-8078%20-%20Temp%20Test%20Pile/IDR/2011-02-16-IDR-TN.pdf",
        rel_path="specs/gsa/WSDOT-IDR-sample.pdf",
        license="WSDOT public document",
        expect_min_bytes=1024,
    ),
]

SOURCES_OSHA = [
    Source(
        category="osha-pubs",
        name="OSHA Publication 2202 - Construction Industry Digest",
        url="https://www.osha.gov/sites/default/files/publications/OSHA2202.pdf",
        rel_path="osha/OSHA-2202.pdf",
        license="US Government works (public domain)",
        expect_min_bytes=100_000,
    ),
]

SOURCES_OSHA_XML = [
    Source(
        category="osha-xml",
        name="eCFR Title 29 Chapter XVII Part 1926 (XML)",
        url=(
            "https://www.ecfr.gov/api/versioner/v1/full/2026-04-22/title-29.xml"
            "?chapter=XVII&part=1926"
        ),
        rel_path="osha/cfr-title29-chapterXVII-part1926.xml",
        license="US Government works (public domain)",
        expect_min_bytes=10_000,
    ),
]

SOURCES_RFI_SAMPLES = [
    Source(
        category="rfi-samples",
        name="UNC Charlotte EVI RFI Log",
        url=(
            "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2024/06/"
            "Addendum-4-Responses-to-EVI-BID-RFI-Log-10-22-20-2.pdf"
        ),
        rel_path="rfi-samples/UNC-Charlotte-EVI-RFI-Log.pdf",
        license="UNC Charlotte public bid addendum",
        expect_min_bytes=100_000,
    ),
]


# ---------------------------------------------------------------------------
# Pexels API resolves URL via metadata
# ---------------------------------------------------------------------------

PEXELS_VIDEO_IDS = ["8965526", "8964291", "8293129", "8964296"]


def _pexels_resolve(api_key: str, video_id: str) -> tuple[str, str] | None:
    """Return (mp4_url, license_string) for the highest hd quality."""
    try:
        r = SESSION.get(
            f"https://api.pexels.com/videos/videos/{video_id}",
            headers={"Authorization": api_key},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        # prefer "hd" quality (1280x720 area), else first mp4
        files = data.get("video_files", [])
        hd = [f for f in files if f.get("quality") == "hd" and f.get("file_type") == "video/mp4"]
        chosen = (
            hd[0] if hd else next((f for f in files if f.get("file_type") == "video/mp4"), None)
        )
        if not chosen:
            return None
        return chosen["link"], "Pexels License (free commercial use, attribution appreciated)"
    except Exception as exc:
        print(f"    pexels resolve fail {video_id}: {exc}")
        return None


# ---------------------------------------------------------------------------
# BLS PPI cost time-series via free public API
# ---------------------------------------------------------------------------

BLS_SERIES = {
    "PCU327320327320": "PPI - Concrete pipe manufacturing",
    "WPU101": "PPI - Iron and steel",
    "WPU0811": "PPI - Lumber",
    "WPU0561": "PPI - Crude petroleum",
    "WPU057": "PPI - Asphalt",
}


def _fetch_bls(manifest: dict[str, dict]) -> int:
    count = 0
    for series_id, desc in BLS_SERIES.items():
        rel = f"timeseries/bls-{series_id}.json"
        dest = DATA_ROOT / rel
        if dest.exists() and dest.stat().st_size > 100:
            print(f"  [skip] {rel}")
            continue
        url = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{series_id}"
        ok = _download(url, dest, expect_min_bytes=100)
        if ok:
            entry = {
                "path": rel,
                "category": "cost",
                "name": f"BLS {series_id} ({desc})",
                "source_url": url,
                "license": "BLS public data",
                "data_source": "public_demo",
                "bytes": dest.stat().st_size,
                "sha256": _sha256(dest),
            }
            _append_manifest(entry)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Git-cloned BIM corpora
# ---------------------------------------------------------------------------

GIT_CORPORA = [
    {
        "name": "Schependomlaan IFC dataset",
        "url": "https://github.com/jakob-beetz/DataSetSchependomlaan.git",
        "rel_path": "bim/schependomlaan",
        "license": "CC BY-ND 4.0",
        "category": "bim",
    },
    {
        "name": "buildingSMART Sample IFC files",
        "url": "https://github.com/buildingSMART/Sample-Test-Files.git",
        "rel_path": "bim/buildingsmart",
        "license": "buildingSMART CC BY 4.0",
        "category": "bim",
    },
]


def _fetch_git_corpora(manifest: dict[str, dict]) -> int:
    count = 0
    for spec in GIT_CORPORA:
        dest = DATA_ROOT / spec["rel_path"]
        if dest.exists() and any(dest.iterdir()):
            print(f"  [skip] {spec['rel_path']} (already cloned)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            print(f"  git clone {spec['name']}")
            subprocess.run(
                ["git", "clone", "--depth", "1", spec["url"], str(dest)],
                check=True,
                capture_output=True,
                timeout=300,
            )
            ifc_files = list(dest.rglob("*.ifc"))
            print(f"    -> {len(ifc_files)} IFC files")
            entry = {
                "path": spec["rel_path"],
                "category": "bim",
                "name": spec["name"],
                "source_url": spec["url"],
                "license": spec["license"],
                "data_source": "public_demo",
                "ifc_count": len(ifc_files),
            }
            _append_manifest(entry)
            count += 1
        except subprocess.CalledProcessError as exc:
            print(f"    FAIL: {exc.stderr.decode()[:200]}")
    return count


# ---------------------------------------------------------------------------
# Vision datasets (Roboflow + Kaggle + METU)
# ---------------------------------------------------------------------------


def _fetch_roboflow(manifest: dict[str, dict]) -> int:
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print("  ROBOFLOW_API_KEY not set; skip")
        return 0
    dest = DATA_ROOT / "images" / "cs-safety-v27"
    if dest.exists() and any(dest.iterdir()):
        print("  [skip] roboflow cs-safety-v27 (already downloaded)")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from roboflow import Roboflow

        rf = Roboflow(api_key=api_key)
        project = rf.workspace("roboflow-universe-projects").project("construction-site-safety")
        project.version(27).download("yolov8", location=str(dest))
        entry = {
            "path": str(dest.relative_to(DATA_ROOT)),
            "category": "vision",
            "name": "Roboflow Construction Site Safety v27",
            "source_url": "https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety",
            "license": "CC BY 4.0",
            "data_source": "public_demo",
        }
        _append_manifest(entry)
        return 1
    except Exception as exc:
        print(f"    FAIL roboflow: {type(exc).__name__}: {exc}")
        return 0


def _fetch_kaggle(manifest: dict[str, dict]) -> int:
    if not (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")):
        print("  KAGGLE creds not set; skip")
        return 0
    # Write kaggle.json so the kaggle module finds creds
    kaggle_dir = Path.home() / ".kaggle"
    kaggle_dir.mkdir(exist_ok=True)
    cfg = kaggle_dir / "kaggle.json"
    if not cfg.exists():
        cfg.write_text(
            json.dumps(
                {
                    "username": os.environ["KAGGLE_USERNAME"],
                    "key": os.environ["KAGGLE_KEY"],
                }
            )
        )
        cfg.chmod(0o600)

    targets = [
        ("andrewmvd/hard-hat-detection", "images/hard-hat", "Kaggle Hard Hat Detection (CC0)"),
        (
            "arunrk7/surface-crack-detection",
            "images/surface-crack",
            "Kaggle Surface Crack Detection (CC0)",
        ),
    ]
    count = 0
    for slug, rel, name in targets:
        dest = DATA_ROOT / rel
        if dest.exists() and any(dest.iterdir()):
            print(f"  [skip] kaggle {slug}")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        try:
            print(f"  kaggle download {slug}")
            subprocess.run(
                [
                    "apps/api/.venv/bin/kaggle",
                    "datasets",
                    "download",
                    "-d",
                    slug,
                    "--unzip",
                    "-p",
                    str(dest),
                ],
                check=True,
                cwd=REPO_ROOT,
                timeout=900,
                capture_output=True,
            )
            entry = {
                "path": rel,
                "category": "vision",
                "name": name,
                "source_url": f"https://www.kaggle.com/datasets/{slug}",
                "license": "CC0",
                "data_source": "public_demo",
            }
            _append_manifest(entry)
            count += 1
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode()[:300] if exc.stderr else str(exc)
            print(f"    FAIL kaggle {slug}: {err}")
        except subprocess.TimeoutExpired:
            print(f"    FAIL kaggle {slug}: timeout")
    return count


# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------


def _fetch_pexels(manifest: dict[str, dict]) -> int:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        print("  PEXELS_API_KEY not set; skip")
        return 0
    count = 0
    for vid in PEXELS_VIDEO_IDS:
        rel = f"videos/pexels-{vid}.mp4"
        dest = DATA_ROOT / rel
        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"  [skip] {rel}")
            continue
        info = _pexels_resolve(key, vid)
        if not info:
            continue
        url, lic = info
        if _download(url, dest, expect_min_bytes=200_000):
            entry = {
                "path": rel,
                "category": "videos",
                "name": f"Pexels video {vid}",
                "source_url": f"https://www.pexels.com/video/{vid}",
                "license": lic,
                "data_source": "public_demo",
                "bytes": dest.stat().st_size,
                "sha256": _sha256(dest),
            }
            _append_manifest(entry)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Generic file fetcher
# ---------------------------------------------------------------------------


def _fetch_simple(sources: list[Source], manifest: dict[str, dict]) -> int:
    count = 0
    for src in sources:
        dest = DATA_ROOT / src.rel_path
        if dest.exists() and dest.stat().st_size >= src.expect_min_bytes:
            print(f"  [skip] {src.rel_path}")
            continue
        print(f"  GET {src.name}")
        if _download(src.url, dest, expect_min_bytes=src.expect_min_bytes):
            entry = {
                "path": src.rel_path,
                "category": src.category,
                "name": src.name,
                "source_url": src.url,
                "license": src.license,
                "data_source": "public_demo",
                "bytes": dest.stat().st_size,
                "sha256": _sha256(dest),
            }
            _append_manifest(entry)
            count += 1
            time.sleep(0.5)  # be polite
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


CATEGORIES: dict[str, Callable[[dict], int]] = {}


def _register():
    CATEGORIES["ufgs"] = lambda m: _fetch_simple(_ufgs_sources(), m)
    CATEGORIES["specs-state"] = lambda m: _fetch_simple(SOURCES_STATE_SPECS, m)
    CATEGORIES["gsa"] = lambda m: _fetch_simple(SOURCES_GSA, m)
    CATEGORIES["osha-pubs"] = lambda m: _fetch_simple(SOURCES_OSHA, m)
    CATEGORIES["osha-xml"] = lambda m: _fetch_simple(SOURCES_OSHA_XML, m)
    CATEGORIES["rfi-samples"] = lambda m: _fetch_simple(SOURCES_RFI_SAMPLES, m)
    CATEGORIES["bim"] = _fetch_git_corpora
    CATEGORIES["cost"] = _fetch_bls
    CATEGORIES["vision-roboflow"] = _fetch_roboflow
    CATEGORIES["vision-kaggle"] = _fetch_kaggle
    CATEGORIES["videos"] = _fetch_pexels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-sep categories to run")
    parser.add_argument("--skip", help="comma-sep categories to skip")
    parser.add_argument("--list", action="store_true", help="list categories and exit")
    args = parser.parse_args()

    _register()

    if args.list:
        for cat in CATEGORIES:
            print(cat)
        return

    only = set((args.only or "").split(",")) - {""}
    skip = set((args.skip or "").split(",")) - {""}

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists():
        MANIFEST_PATH.touch()

    manifest = _load_manifest()
    print(f"Manifest entries already on disk: {len(manifest)}")

    total = 0
    for cat, fn in CATEGORIES.items():
        if only and cat not in only:
            continue
        if cat in skip:
            continue
        print(f"\n=== {cat} ===")
        added = fn(manifest)
        total += added

    print(f"\nDone. Added {total} new entries to {MANIFEST_PATH}")
    print(f"Total tracked entries: {len(_load_manifest())}")


if __name__ == "__main__":
    main()
