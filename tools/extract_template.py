"""Extract a template PNG from a screenshot region.

Usage:
  python tools/extract_template.py <name> <x> <y> <w> <h> [screenshot]

  name       - output filename without extension (saved to src/vision/templates/)
  x y w h    - pixel region to extract (top-left x, top-left y, width, height)
  screenshot - source image (default: captures/snap.png)

Example:
  python tools/extract_template.py nav_play 1150 620 180 55
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2

if len(sys.argv) < 6:
    print(__doc__)
    sys.exit(1)

name = sys.argv[1]
x, y, w, h = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
src = sys.argv[6] if len(sys.argv) > 6 else "captures/snap.png"

frame = cv2.imread(src)
if frame is None:
    print(f"Error: cannot read {src}")
    sys.exit(1)

region = frame[y:y+h, x:x+w]
out = Path("src/vision/templates") / f"{name}.png"
cv2.imwrite(str(out), region)
print(f"Extracted {w}x{h} region -> {out}")
