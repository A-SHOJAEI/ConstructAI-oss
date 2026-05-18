#!/usr/bin/env bash
set -euo pipefail

PHOTOS_DIR="$(cd "$(dirname "$0")" && pwd)/samples"
mkdir -p "${PHOTOS_DIR}"

echo "=== Sample Construction Photos ==="
echo "Target: ${PHOTOS_DIR}"
echo ""
echo "Note: This script downloads sample images from public datasets."
echo "For demo purposes, you can also use any construction site photos."
echo ""

# Generate simple placeholder images using Python (no external download needed)
python3 -c "
from PIL import Image, ImageDraw, ImageFont
import os

photos_dir = '${PHOTOS_DIR}'
samples = [
    ('site_overview.jpg', 'Site Overview - North Gate', (1920, 1080)),
    ('foundation_pour.jpg', 'Foundation Concrete Pour', (1920, 1080)),
    ('steel_erection.jpg', 'Steel Erection Level 2', (1920, 1080)),
    ('safety_observation.jpg', 'Safety Observation', (1920, 1080)),
    ('equipment_crane.jpg', 'Tower Crane Operation', (1920, 1080)),
]

for filename, label, size in samples:
    img = Image.new('RGB', size, color=(100, 130, 160))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, size[0]-50, size[1]-50], outline='white', width=3)
    draw.text((size[0]//2 - 100, size[1]//2 - 20), label, fill='white')
    draw.text((size[0]//2 - 80, size[1]//2 + 20), 'ConstructAI Demo', fill='yellow')
    img.save(os.path.join(photos_dir, filename), 'JPEG', quality=85)
    print(f'  Created: {filename}')
" 2>/dev/null || echo "Pillow not installed. Skipping photo generation."

echo ""
echo "=== Photo generation complete ==="
