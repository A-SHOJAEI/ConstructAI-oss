"""Fine-tune RTMDet or YOLO on construction safety dataset."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def prepare_dataset(
    data_dir: str,
    split_ratio: tuple[float, float, float] = (0.7, 0.2, 0.1),
) -> dict:
    """Prepare dataset splits from annotated images.

    Parameters
    ----------
    data_dir:
        Root directory containing 'images/' and 'annotations/' subdirectories.
    split_ratio:
        Train/val/test split ratios.

    Returns
    -------
    Dict with paths to train/val/test annotation files.
    """
    data_path = Path(data_dir)
    images_dir = data_path / "images"
    annotations_dir = data_path / "annotations"

    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")
    if not annotations_dir.exists():
        raise FileNotFoundError(f"Annotations directory not found: {annotations_dir}")

    # Collect all annotation files
    ann_files = sorted(annotations_dir.glob("*.json"))
    total = len(ann_files)

    if total == 0:
        raise ValueError("No annotation files found")

    train_end = int(total * split_ratio[0])
    val_end = train_end + int(total * split_ratio[1])

    splits = {
        "train": ann_files[:train_end],
        "val": ann_files[train_end:val_end],
        "test": ann_files[val_end:],
    }

    logger.info(
        "Dataset splits: train=%d, val=%d, test=%d",
        len(splits["train"]),
        len(splits["val"]),
        len(splits["test"]),
    )
    return splits


def fine_tune_rtmdet(
    data_dir: str,
    config_path: str,
    output_dir: str,
    epochs: int = 50,
    batch_size: int = 8,
    learning_rate: float = 0.001,
    resume_from: str | None = None,
) -> dict:
    """Fine-tune RTMDet using MMDetection.

    Parameters
    ----------
    data_dir:
        Path to the prepared dataset.
    config_path:
        Path to MMDetection config file.
    output_dir:
        Directory for saving checkpoints and logs.
    epochs:
        Number of training epochs.
    batch_size:
        Training batch size.
    learning_rate:
        Initial learning rate.
    resume_from:
        Path to checkpoint to resume training from.

    Returns
    -------
    Training results summary dict.
    """
    try:
        from mmdet.apis import train_detector
        from mmdet.models import build_detector
        from mmengine.config import Config
        from mmengine.runner import Runner
    except ImportError:
        logger.error("MMDetection not installed. Install with: pip install mmdet mmengine")
        return {"status": "failed", "error": "mmdet not available"}

    try:
        cfg = Config.fromfile(config_path)

        # Override with our parameters
        cfg.train_dataloader.batch_size = batch_size
        cfg.optim_wrapper.optimizer.lr = learning_rate
        cfg.train_cfg.max_epochs = epochs
        cfg.work_dir = output_dir

        if resume_from:
            cfg.resume = True
            cfg.load_from = resume_from

        os.makedirs(output_dir, exist_ok=True)

        runner = Runner.from_cfg(cfg)
        runner.train()

        return {
            "status": "completed",
            "output_dir": output_dir,
            "epochs": epochs,
            "config": config_path,
        }

    except Exception as exc:
        logger.error("RTMDet training failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def fine_tune_yolo(
    data_dir: str,
    config_path: str,
    output_dir: str,
    epochs: int = 50,
    batch_size: int = 16,
    image_size: int = 640,
    model_size: str = "yolo11m",
) -> dict:
    """Fine-tune YOLO on construction safety dataset.

    WARNING: YOLO is AGPL-3.0 licensed. Use for development/research only.
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("Ultralytics not installed. Install with: pip install ultralytics")
        return {"status": "failed", "error": "ultralytics not available"}

    try:
        model = YOLO(f"{model_size}.pt")

        results = model.train(
            data=config_path,
            epochs=epochs,
            batch=batch_size,
            imgsz=image_size,
            project=output_dir,
            name="construction_safety",
            exist_ok=True,
            patience=10,
            save=True,
            plots=True,
        )

        return {
            "status": "completed",
            "output_dir": output_dir,
            "epochs": epochs,
            "model_size": model_size,
            "results": str(results),
        }

    except Exception as exc:
        logger.error("YOLO training failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Fine-tune detection model")
    parser.add_argument("--framework", choices=["rtmdet", "yolo"], required=True)
    parser.add_argument("--data-dir", required=True, help="Dataset directory")
    parser.add_argument("--config", required=True, help="Model config file")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--resume", default=None, help="Resume from checkpoint")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.framework == "rtmdet":
        result = fine_tune_rtmdet(
            data_dir=args.data_dir,
            config_path=args.config,
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.lr,
            resume_from=args.resume,
        )
    else:
        result = fine_tune_yolo(
            data_dir=args.data_dir,
            config_path=args.config,
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
        )

    logger.info("Training result: %s", result)


if __name__ == "__main__":
    main()
