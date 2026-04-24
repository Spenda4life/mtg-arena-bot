from __future__ import annotations
import re
import numpy as np
import mss
import mss.tools
from PIL import Image
from loguru import logger


def find_arena_window() -> dict | None:
    """Return the bounding box of the MTG Arena window, or None if not found."""
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process | Where-Object {$_.MainWindowTitle -match 'MTGA|Magic.*Arena'} "
             "| Select-Object -First 1 -ExpandProperty MainWindowTitle"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            logger.debug(f"Found Arena window: {result.stdout.strip()}")
    except Exception as e:
        logger.warning(f"Could not query window title: {e}")

    # Fall back to primary monitor if window detection fails
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        return monitor


class ScreenCapture:
    def __init__(self, monitor: dict | None = None):
        self._monitor = monitor or find_arena_window()

    def grab(self) -> np.ndarray:
        """Capture the current screen region as a BGR numpy array."""
        with mss.mss() as sct:
            raw = sct.grab(self._monitor)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
            return np.array(img)[:, :, ::-1]  # RGB → BGR for OpenCV

    def grab_region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        region = {"left": x, "top": y, "width": w, "height": h}
        with mss.mss() as sct:
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
            return np.array(img)[:, :, ::-1]

    def save_debug(self, frame: np.ndarray, path: str) -> None:
        import cv2
        cv2.imwrite(path, frame)
        logger.debug(f"Saved debug screenshot: {path}")
