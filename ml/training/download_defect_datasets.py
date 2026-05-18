"""Download and organize defect detection datasets for ViT v1.1 training.

Downloads open datasets to constructai-data/cv-training/:
    - dacl10k: HuggingFace (Voxel51/dacl10k) — 9,920 bridge inspection images
    - BD3: GitHub (Praveenkottari/BD3-Dataset) — 3,965 building defect images
    - S2DS: Google Drive — 743 structural defect images
    - MBDD2025: Nature Scientific Data — 14,471 building defect images

Usage:
    python -m ml.training.download_defect_datasets \
        --output-dir constructai-data/cv-training/ \
        --datasets dacl10k bd3 s2ds

    # List datasets and their status
    python -m ml.training.download_defect_datasets \
        --output-dir constructai-data/cv-training/ --status
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def download_dacl10k(output_dir: Path) -> bool:
    """Download dacl10k from HuggingFace using datasets library.

    dacl10k: 9,920 bridge inspection images with 13 damage classes.
    Source: University of Bundeswehr Munich.
    """
    dest = output_dir / "dacl10k"
    if dest.exists() and any(dest.iterdir()):
        logger.info("dacl10k already exists at %s", dest)
        return True

    dest.mkdir(parents=True, exist_ok=True)

    try:
        # Try HuggingFace datasets library
        logger.info("Downloading dacl10k from HuggingFace (Voxel51/dacl10k)...")
        cache_dir = str(dest / "hf_cache")
        script = (
            "from datasets import load_dataset; "
            f"ds = load_dataset('Voxel51/dacl10k', cache_dir=r'{cache_dir}')"
        )
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            timeout=3600,
        )
        logger.info("dacl10k downloaded successfully to %s", dest)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("dacl10k download failed: %s", e)
        _print_manual_instructions("dacl10k", [
            "pip install datasets",
            f"python -c \"from datasets import load_dataset; "
            f"ds = load_dataset('Voxel51/dacl10k', cache_dir=r'{cache_dir}')\"",
            "",
            "Or download manually from: https://huggingface.co/datasets/Voxel51/dacl10k",
            f"Place files in: {dest}",
        ])
        return False


def download_bd3(output_dir: Path) -> bool:
    """Download BD3 from GitHub.

    BD3: 3,965 building defect images, 7 classes (algae, major_crack, minor_crack,
    peeling, spalling, stain, normal).
    """
    dest = output_dir / "bd3"

    # Check for full dataset (should have >100 images per class)
    class_dirs = ["Algae", "major crack", "minor crack", "peeling", "spalling", "stain", "normal"]
    if dest.exists():
        has_data = False
        for cd in class_dirs:
            d = dest / cd
            if d.exists() and len(list(d.glob("*.jpg"))) > 50:
                has_data = True
                break
        if has_data:
            logger.info("BD3 full dataset already exists at %s", dest)
            return True

    logger.warning("BD3 requires manual download — GitHub repo contains only sample images.")
    _print_manual_instructions("BD3", [
        "The BD3 dataset must be requested from the authors.",
        "Paper: https://dl.acm.org/doi/10.1145/3671127.3698789",
        "GitHub: https://github.com/Praveenkottari/BD3-Dataset",
        "",
        "After downloading, organize the images into class folders:",
        f"  {dest}/Algae/          (624 images)",
        f"  {dest}/major crack/    (620 images)",
        f"  {dest}/minor crack/    (580 images)",
        f"  {dest}/peeling/        (520 images)",
        f"  {dest}/spalling/       (500 images)",
        f"  {dest}/stain/          (521 images)",
        f"  {dest}/normal/         (600 images)",
    ])
    return False


def download_s2ds(output_dir: Path) -> bool:
    """Download S2DS from Google Drive.

    S2DS: 743 images of structural defects, 7 classes.
    """
    dest = output_dir / "s2ds"

    # Check for actual image directories (not just thumbnails)
    if dest.exists():
        for split in ["train", "val", "test"]:
            split_dir = dest / split
            if split_dir.exists() and len(list(split_dir.rglob("*.png"))) > 10:
                logger.info("S2DS dataset already exists at %s", dest)
                return True

    logger.warning("S2DS requires manual download from Google Drive.")
    _print_manual_instructions("S2DS", [
        "Download from Google Drive:",
        "  https://drive.google.com/file/d/1PQ50QKfy2vnDOHSmw5bpBFi33hZsSXuM/view",
        "",
        f"Extract the ZIP to: {dest}/",
        "",
        "Expected structure after extraction:",
        f"  {dest}/train/          (563 images)",
        f"  {dest}/val/            (87 images)",
        f"  {dest}/test/           (93 images)",
        "",
        "Each split contains subdirectories per class:",
        "  crack/, spalling/, corrosion/, efflorescence/, vegetation/,",
        "  control_point/, background/",
    ])
    return False


def download_mbdd2025(output_dir: Path) -> bool:
    """Download MBDD2025 from Nature Scientific Data.

    MBDD2025: 14,471 images, 5 defect types across 6 building types.
    """
    dest = output_dir / "mbdd2025"

    if dest.exists() and any(dest.rglob("*.jpg")):
        logger.info("MBDD2025 dataset already exists at %s", dest)
        return True

    logger.warning("MBDD2025 requires manual download from Nature Scientific Data.")
    _print_manual_instructions("MBDD2025", [
        "Download from the Nature Scientific Data repository.",
        "Search: 'MBDD2025 building defect' on https://www.nature.com/sdata/",
        "",
        f"Extract to: {dest}/",
        "",
        "Expected classes: crack, leakage, corrosion, abscission, bulge",
        "The dataset should contain subfolders per building type with class labels.",
    ])
    return False


def check_status(output_dir: Path) -> None:
    """Print status of all datasets."""
    datasets = {
        "mendeley-cracks": ("Mendeley Concrete Cracks", "~40K images"),
        "codebrim": ("CODEBRIM", "~7K images"),
        "sdnet2018": ("SDNET2018", "~56K images"),
        "dacl10k": ("dacl10k", "~10K images"),
        "bd3": ("BD3", "~4K images"),
        "s2ds": ("S2DS", "743 images"),
        "mbdd2025": ("MBDD2025", "~14K images"),
    }

    print("\n" + "=" * 70)
    print("DATASET STATUS")
    print("=" * 70)

    for dirname, (name, size) in datasets.items():
        path = output_dir / dirname
        if path.exists():
            # Count images
            count = sum(
                len(list(path.rglob(f"*.{ext}")))
                for ext in ["jpg", "jpeg", "png", "bmp"]
            )
            if count > 50:
                status = f"OK ({count:,} images)"
            elif count > 0:
                status = f"PARTIAL ({count} images — may be samples only)"
            else:
                status = "EMPTY (directory exists but no images)"
        else:
            status = "MISSING"

        print(f"  {name:<30} [{size:>12}]  {status}")

    print("=" * 70)
    print()


def _print_manual_instructions(name: str, steps: list[str]) -> None:
    """Print manual download instructions."""
    print(f"\n{'='*60}")
    print(f"  MANUAL DOWNLOAD REQUIRED: {name}")
    print(f"{'='*60}")
    for step in steps:
        print(f"  {step}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Download defect detection datasets")
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Base directory for datasets (e.g., constructai-data/cv-training/)",
    )
    parser.add_argument(
        "--datasets", nargs="*", default=["dacl10k", "bd3", "s2ds", "mbdd2025"],
        choices=["dacl10k", "bd3", "s2ds", "mbdd2025"],
        help="Which datasets to download",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show status of all datasets without downloading",
    )
    args = parser.parse_args()

    if args.status:
        check_status(args.output_dir)
        return

    download_fns = {
        "dacl10k": download_dacl10k,
        "bd3": download_bd3,
        "s2ds": download_s2ds,
        "mbdd2025": download_mbdd2025,
    }

    results = {}
    for ds in args.datasets:
        fn = download_fns[ds]
        results[ds] = fn(args.output_dir)

    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)
    for ds, ok in results.items():
        print(f"  {ds:<15} {'OK' if ok else 'NEEDS MANUAL DOWNLOAD'}")
    print("=" * 60)

    check_status(args.output_dir)


if __name__ == "__main__":
    main()
