"""Run YOLO safety + ViT defect training sequentially.

Designed to run unattended on the RTX 4090 system.
YOLO runs first (~11-16 hours), then ViT (~30-50 min).

Usage:
    cd H:/ConstructAI/constructai
    python -m ml.training.run_all_training 2>&1 | tee training_log.txt
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "constructai-data" / "cv-training"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_command(description: str, cmd: list[str]) -> int:
    logger.info("=" * 70)
    logger.info("STARTING: %s", description)
    logger.info("Command: %s", " ".join(cmd))
    logger.info("=" * 70)

    start = time.time()
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    elapsed = time.time() - start

    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    logger.info(
        "FINISHED: %s — exit code %d — took %dh %dm",
        description, result.returncode, hours, minutes,
    )
    return result.returncode


def main():
    logger.info("Training pipeline starting")
    logger.info("Data root: %s", DATA_ROOT)
    logger.info("Repo root: %s", REPO_ROOT)

    overall_start = time.time()

    # ---------------------------------------------------------------
    # Step 1: YOLO Safety Training
    # ---------------------------------------------------------------
    yolo_rc = run_command(
        "YOLO Safety Detection Training (YOLOv8-L, 1280px, ~15K images)",
        [
            sys.executable, "-m", "ml.training.train_safety_yolo",
            "--soda-dir", str(DATA_ROOT / "soda"),
            "--roboflow-dir", str(DATA_ROOT / "roboflow-safety"),
            "--output-dir", str(REPO_ROOT / "constructai_safety"),
            "--skip-prepare",  # Data already prepared
            "--batch", "8",
            "--device", "0",
            "--epochs", "100",
            "--patience", "15",
            "--registry-dir", str(REPO_ROOT / "models" / "safety_yolo_v1.0"),
        ],
    )

    if yolo_rc != 0:
        logger.error("YOLO training failed (exit %d). Continuing to ViT anyway.", yolo_rc)

    # ---------------------------------------------------------------
    # Step 2: ViT Defect Classification Training (v1.1)
    # ---------------------------------------------------------------
    vit_cmd = [
        sys.executable, "-m", "ml.training.train_defect_vit",
        "--mendeley-dir", str(DATA_ROOT / "mendeley-cracks"),
        "--codebrim-dir", str(DATA_ROOT / "codebrim"),
        "--sdnet-dir", str(DATA_ROOT / "sdnet2018"),
        "--output-dir", str(REPO_ROOT / "models" / "defect_vit_v1.1"),
        "--batch", "32",
        "--device", "cuda:0",
        "--epochs", "50",
        "--patience", "10",
    ]

    # Add optional datasets if present
    dacl10k_dir = DATA_ROOT / "dacl10k"
    bd3_dir = DATA_ROOT / "bd3"
    s2ds_dir = DATA_ROOT / "s2ds"
    mbdd_dir = DATA_ROOT / "mbdd2025"

    if dacl10k_dir.exists():
        vit_cmd.extend(["--dacl10k-dir", str(dacl10k_dir)])
    if bd3_dir.exists():
        vit_cmd.extend(["--bd3-dir", str(bd3_dir)])
    if s2ds_dir.exists():
        vit_cmd.extend(["--s2ds-dir", str(s2ds_dir)])
    if mbdd_dir.exists():
        vit_cmd.extend(["--mbdd-dir", str(mbdd_dir)])

    vit_rc = run_command(
        "ViT Defect Classification Training v1.1 (ViT-B/16, 224px, 8 classes)",
        vit_cmd,
    )

    if vit_rc != 0:
        logger.error("ViT training failed (exit %d).", vit_rc)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    total = time.time() - overall_start
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)

    logger.info("=" * 70)
    logger.info("ALL TRAINING COMPLETE — total time: %dh %dm", hours, minutes)
    logger.info("  YOLO: %s (exit %d)", "OK" if yolo_rc == 0 else "FAILED", yolo_rc)
    logger.info("  ViT:  %s (exit %d)", "OK" if vit_rc == 0 else "FAILED", vit_rc)
    logger.info("=" * 70)
    logger.info("Results:")
    logger.info("  YOLO registry: %s", REPO_ROOT / "models" / "safety_yolo_v1.0")
    logger.info("  ViT registry:  %s", REPO_ROOT / "models" / "defect_vit_v1.1")


if __name__ == "__main__":
    main()
