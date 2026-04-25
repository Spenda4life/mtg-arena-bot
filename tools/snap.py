"""Take a screenshot of the Arena window and save to captures/snap.png."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
from src.capture.screen import ScreenCapture

Path("captures").mkdir(exist_ok=True)
cap = ScreenCapture()
frame = cap.grab()
out = "captures/snap.png"
cv2.imwrite(out, frame)
print(f"Saved {frame.shape[1]}x{frame.shape[0]} -> {out}")
