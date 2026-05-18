"""
Generate a synthetic safety video with overlays for demo purposes.

Creates a simple video with colored rectangles and text overlays
simulating a construction site camera feed with detection boxes.

Requires: opencv-python (cv2)

Usage:
    python -m demo.assets.video.generate_test_video [output_path]
"""
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def generate_test_video(output_path: Path, duration_seconds: int = 10, fps: int = 15) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not HAS_CV2:
        # Create a text placeholder
        placeholder = output_path.with_suffix(".txt")
        placeholder.write_text(
            "Video Placeholder\n"
            "=================\n"
            "Install opencv-python to generate test video:\n"
            "  pip install opencv-python\n\n"
            "Video specification:\n"
            f"  - Duration: {duration_seconds}s\n"
            f"  - FPS: {fps}\n"
            "  - Resolution: 1920x1080\n"
            "  - Content: Simulated construction site with detection overlays\n"
        )
        print(f"Generated placeholder: {placeholder}")
        return placeholder

    width, height = 1920, 1080
    total_frames = duration_seconds * fps

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    np.random.seed(42)

    for frame_idx in range(total_frames):
        # Create a construction-site-like background
        frame = np.full((height, width, 3), (140, 130, 110), dtype=np.uint8)

        # Add some "structure" rectangles
        cv2.rectangle(frame, (200, 300), (800, 900), (100, 100, 100), -1)  # Building
        cv2.rectangle(frame, (200, 300), (800, 900), (80, 80, 80), 3)
        cv2.rectangle(frame, (900, 400), (1400, 850), (120, 110, 100), -1)  # Crane base
        cv2.line(frame, (1150, 100), (1150, 400), (60, 60, 60), 8)  # Crane mast

        # Simulated worker (moving dot)
        worker_x = 600 + int(200 * np.sin(frame_idx / 20))
        worker_y = 700 + int(50 * np.cos(frame_idx / 15))
        cv2.circle(frame, (worker_x, worker_y), 15, (50, 50, 200), -1)

        # Detection bounding box
        box_x1 = worker_x - 30
        box_y1 = worker_y - 50
        box_x2 = worker_x + 30
        box_y2 = worker_y + 20
        cv2.rectangle(frame, (box_x1, box_y1), (box_x2, box_y2), (0, 255, 0), 2)

        # Label
        cv2.putText(frame, "Person 0.94", (box_x1, box_y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        # Safety zone overlay (semi-transparent)
        overlay = frame.copy()
        pts = np.array([[850, 350], [1450, 350], [1450, 900], [850, 900]])
        cv2.fillPoly(overlay, [pts], (0, 0, 200))
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        cv2.polylines(frame, [pts], True, (0, 0, 255), 2)
        cv2.putText(frame, "CRANE EXCLUSION ZONE", (880, 380),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        # Camera info overlay
        timestamp = f"2026-02-23 14:{frame_idx // fps:02d}:{frame_idx % fps * 4:02d}"
        cv2.putText(frame, f"CAM-C01 | {timestamp}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(frame, "ConstructAI Safety Monitor", (20, height - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        writer.write(frame)

    writer.release()
    print(f"Generated video: {output_path} ({total_frames} frames)")
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/safety_demo.mp4")
    generate_test_video(out)
