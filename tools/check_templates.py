"""
Quick diagnostic: take a screenshot and test whether the button templates match.
Run this while the mulligan screen is visible in Arena.

Usage:
    python tools/check_templates.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
from src.capture.screen import ScreenCapture
from src.vision.detector import VisionDetector, TEMPLATES_DIR, _match_template

THRESHOLDS = [0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60]

def main():
    templates = list(TEMPLATES_DIR.glob("btn_*.png"))
    if not templates:
        print(f"No templates found in {TEMPLATES_DIR.resolve()}")
        sys.exit(1)

    print(f"Found templates: {[t.name for t in templates]}")
    print("Taking screenshot...")
    cap = ScreenCapture()
    frame = cap.grab()
    h, w = frame.shape[:2]
    print(f"Screen size: {w}x{h}")
    print()

    for tpl_path in templates:
        print(f"--- {tpl_path.name} ---")
        tpl = cv2.imread(str(tpl_path))
        if tpl is None:
            print("  FAILED TO LOAD")
            continue
        th, tw = tpl.shape[:2]
        print(f"  Template size: {tw}x{th}")

        result = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2
        print(f"  Best match score: {max_val:.3f}  at ({cx}, {cy})")

        for t in THRESHOLDS:
            hit = "MATCH" if max_val >= t else "no match"
            marker = " <-- current threshold" if t == 0.80 else ""
            print(f"  threshold {t:.2f}: {hit}{marker}")
        print()

    # Save annotated screenshot for manual inspection
    out_path = Path("captures/template_check.png")
    out_path.parent.mkdir(exist_ok=True)
    det = VisionDetector()
    annotated = det.annotate_debug(frame)
    for tpl_path in templates:
        pos = _match_template(frame, tpl_path.name, threshold=0.60)
        if pos:
            cv2.circle(annotated, pos, 20, (0, 0, 255), 3)
            cv2.putText(annotated, tpl_path.stem, (pos[0]+25, pos[1]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(str(out_path), annotated)
    print(f"Annotated screenshot saved to {out_path.resolve()}")
    print("(Red circles = template hits at threshold 0.60)")

if __name__ == "__main__":
    main()
