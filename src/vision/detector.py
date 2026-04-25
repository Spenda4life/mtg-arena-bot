from __future__ import annotations
import os
import re
import cv2
import numpy as np
import pytesseract
from pathlib import Path
from loguru import logger

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Approximate screen regions (fractions of total screen size) calibrated for 2560x1440.
# All coords are (x, y, w, h) as fractions [0,1].
REGIONS = {
    "our_life":       (0.03, 0.82, 0.06, 0.05),
    "opp_life":       (0.03, 0.13, 0.06, 0.05),
    "our_mana":       (0.85, 0.82, 0.12, 0.06),
    "our_hand":       (0.15, 0.88, 0.70, 0.10),
    "our_battlefield":(0.10, 0.55, 0.80, 0.28),
    "opp_battlefield":(0.10, 0.17, 0.80, 0.28),
    "phase_bar":      (0.89, 0.30, 0.10, 0.40),
    "stack":          (0.38, 0.38, 0.24, 0.24),
    "pass_button":    (0.84, 0.48, 0.13, 0.06),
    "ok_button":      (0.84, 0.55, 0.13, 0.06),
    "keep_hand":      (0.35, 0.70, 0.14, 0.07),
    "mulligan":       (0.50, 0.70, 0.14, 0.07),
}

PHASE_LABELS = {
    "UNT": "BEGINNING",
    "UPK": "BEGINNING",
    "DRW": "BEGINNING",
    "M1":  "MAIN1",
    "BEG": "COMBAT_BEGIN",
    "ATT": "COMBAT_ATTACK",
    "BLK": "COMBAT_BLOCK",
    "DMG": "COMBAT_DAMAGE",
    "M2":  "MAIN2",
    "END": "ENDING",
    "EOT": "ENDING",
}


def _abs_region(region_frac: tuple, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    fx, fy, fw, fh = region_frac
    return int(fx * screen_w), int(fy * screen_h), int(fw * screen_w), int(fh * screen_h)


def _crop(frame: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    return frame[y:y+h, x:x+w]


def _ocr_number(roi: np.ndarray) -> int | None:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(
        thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789"
    ).strip()
    return int(text) if text.isdigit() else None


def _ocr_text(roi: np.ndarray, whitelist: str = "") -> str:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    config = "--psm 7"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(gray, config=config).strip().upper()


def _match_template(frame: np.ndarray, template_name: str, threshold: float = 0.80) -> tuple[int, int] | None:
    """Return (cx, cy) of best template match, or None."""
    path = TEMPLATES_DIR / template_name
    if not path.exists():
        return None
    template = cv2.imread(str(path))
    if template is None:
        return None
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        th, tw = template.shape[:2]
        return max_loc[0] + tw // 2, max_loc[1] + th // 2
    return None


def _button_visible(frame: np.ndarray, template_name: str, threshold: float) -> tuple[bool, tuple[int, int] | None]:
    pos = _match_template(frame, template_name, threshold)
    return pos is not None, pos


class VisionDetector:
    def __init__(self, reference_resolution: tuple[int, int] = (2560, 1440), threshold: float = 0.80):
        self.ref_w, self.ref_h = reference_resolution
        self.threshold = threshold

    def _region(self, frame: np.ndarray, name: str) -> np.ndarray:
        h, w = frame.shape[:2]
        x, y, rw, rh = _abs_region(REGIONS[name], w, h)
        return _crop(frame, x, y, rw, rh)

    def _region_origin(self, frame: np.ndarray, name: str) -> tuple[int, int]:
        h, w = frame.shape[:2]
        x, y, _, _ = _abs_region(REGIONS[name], w, h)
        return x, y

    def detect_life(self, frame: np.ndarray) -> tuple[int | None, int | None]:
        our_roi = self._region(frame, "our_life")
        opp_roi = self._region(frame, "opp_life")
        return _ocr_number(our_roi), _ocr_number(opp_roi)

    def detect_phase(self, frame: np.ndarray) -> str:
        roi = self._region(frame, "phase_bar")
        text = _ocr_text(roi, whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZ12")
        for key, phase in PHASE_LABELS.items():
            if key in text:
                return phase
        return "UNKNOWN"

    def detect_mana(self, frame: np.ndarray) -> dict[str, int]:
        roi = self._region(frame, "our_mana")
        # Try template matching for each mana pip symbol
        mana: dict[str, int] = {}
        for color, tpl in [("W", "mana_w.png"), ("U", "mana_u.png"), ("B", "mana_b.png"),
                            ("R", "mana_r.png"), ("G", "mana_g.png"), ("C", "mana_c.png")]:
            path = TEMPLATES_DIR / tpl
            if not path.exists():
                continue
            template = cv2.imread(str(path))
            if template is None:
                continue
            result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= self.threshold)
            mana[color] = len(locations[0])
        return {k: v for k, v in mana.items() if v > 0}

    def detect_hand_count(self, frame: np.ndarray) -> int:
        """Count cards in hand via contour detection in the hand region."""
        roi = self._region(frame, "our_hand")
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 30, 100)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        card_contours = [c for c in contours if 2000 < cv2.contourArea(c) < 50000]
        return len(card_contours)

    def detect_battlefield_cards(self, frame: np.ndarray, region_name: str) -> list[tuple[int, int]]:
        """Return center pixel coords of cards on a battlefield zone."""
        roi = self._region(frame, region_name)
        ox, oy = self._region_origin(frame, region_name)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 30, 100)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        centers = []
        for c in contours:
            area = cv2.contourArea(c)
            if 3000 < area < 80000:
                M = cv2.moments(c)
                if M["m00"]:
                    cx = int(M["m10"] / M["m00"]) + ox
                    cy = int(M["m01"] / M["m00"]) + oy
                    centers.append((cx, cy))
        return centers

    def detect_buttons(self, frame: np.ndarray, threshold: float | None = None) -> dict:
        t = threshold or self.threshold
        pass_vis, pass_pos = _button_visible(frame, "btn_pass.png", t)
        ok_vis, ok_pos = _button_visible(frame, "btn_ok.png", t)
        keep_vis, keep_pos = _button_visible(frame, "btn_keep.png", t)
        mull_vis, mull_pos = _button_visible(frame, "btn_mulligan.png", t)
        return {
            "pass": (pass_vis, pass_pos),
            "ok": (ok_vis, ok_pos),
            "keep_hand": (keep_vis, keep_pos),
            "mulligan": (mull_vis, mull_pos),
        }

    def detect_discard_state(self, frame: np.ndarray) -> tuple[bool, tuple[int, int] | None]:
        """Return (prompt_visible, submit_button_pos).

        Detects the 'Discard a card.' overlay.  The Submit button changes text
        (0→1) after a card is selected, so we use a fixed fractional position
        rather than template-matching the button itself.
        """
        h, w = frame.shape[:2]
        visible, _ = _button_visible(frame, "btn_discard_prompt.png", self.threshold)
        if not visible:
            return False, None
        # Submit button is at a fixed position calibrated for 1920x1080
        submit_pos = (int(1812 * w / 1920), int(951 * h / 1080))
        return True, submit_pos

    def detect_playable_hand_cards(self, frame: np.ndarray) -> list[tuple[int, int]]:
        """Return (x, y) screen centers of hand cards with Arena's blue 'playable' outline.

        Arena highlights eligible-to-play cards with a cyan/teal border.  We isolate
        that hue, lightly dilate to merge the outline fragments into solid blobs, then
        return each blob's center sorted left-to-right (matching hand array order).
        """
        h, w = frame.shape[:2]
        x0, y0 = int(0.08 * w), int(0.78 * h)
        hand_region = frame[y0:h, x0:int(0.92 * w)]

        hsv = cv2.cvtColor(hand_region, cv2.COLOR_BGR2HSV)
        # Cyan/teal hue range that Arena uses for the playable-card highlight
        mask = cv2.inRange(hsv,
                           np.array([80, 120, 120], dtype=np.uint8),
                           np.array([110, 255, 255], dtype=np.uint8))

        # Small dilation connects outline fragments; keeps adjacent-card gaps open
        kernel = np.ones((7, 7), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        centers: list[tuple[int, int]] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw * ch < 2000 or cw < 25 or ch < 20:
                continue
            cx = x + cw // 2 + x0
            cy = y + ch // 2 + y0
            centers.append((cx, cy))

        centers.sort(key=lambda p: p[0])
        logger.debug(f"Playable hand cards detected: {len(centers)} at {centers}")
        return centers

    def detect_nav_buttons(self, frame: np.ndarray, threshold: float | None = None) -> dict:
        t = threshold or self.threshold
        return {
            "nav_play":     _button_visible(frame, "nav_play.png", t),
            "nav_submit":   _button_visible(frame, "nav_submit.png", t),
            "nav_continue": _button_visible(frame, "nav_continue.png", t),
        }

    def annotate_debug(self, frame: np.ndarray) -> np.ndarray:
        """Draw region overlays on a copy of the frame for debugging."""
        out = frame.copy()
        h, w = out.shape[:2]
        for name, frac in REGIONS.items():
            x, y, rw, rh = _abs_region(frac, w, h)
            cv2.rectangle(out, (x, y), (x + rw, y + rh), (0, 255, 0), 2)
            cv2.putText(out, name, (x + 4, y + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        return out
