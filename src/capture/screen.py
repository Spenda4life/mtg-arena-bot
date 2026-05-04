from __future__ import annotations
import logging
import numpy as np
import mss
import mss.tools
from PIL import Image

logger = logging.getLogger(__name__)

_arena_hwnd: int | None = None  # cached window handle


def _find_arena_hwnd() -> int | None:
    """Find the Arena window handle via EnumWindows."""
    import ctypes
    import ctypes.wintypes as wt

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if "MTGA" in title or "Magic" in title and "Arena" in title:
                found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def get_arena_window_bounds() -> dict | None:
    """Return Arena's screen-space window bounds via Win32, or None if not found."""
    global _arena_hwnd
    import ctypes
    import ctypes.wintypes as wt

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wt.LONG),
            ("top", wt.LONG),
            ("right", wt.LONG),
            ("bottom", wt.LONG),
        ]

    if _arena_hwnd is not None and not ctypes.windll.user32.IsWindow(_arena_hwnd):
        _arena_hwnd = None

    if _arena_hwnd is None:
        _arena_hwnd = _find_arena_hwnd()
    if _arena_hwnd is None:
        return None

    rect = RECT()
    if not ctypes.windll.user32.GetWindowRect(_arena_hwnd, ctypes.byref(rect)):
        _arena_hwnd = None
        return None

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "width": int(width),
        "height": int(height),
    }


def is_arena_running() -> bool:
    """Return True if the Arena window exists without changing focus."""
    global _arena_hwnd
    import ctypes

    if _arena_hwnd is not None and ctypes.windll.user32.IsWindow(_arena_hwnd):
        return True
    _arena_hwnd = _find_arena_hwnd()
    return _arena_hwnd is not None


def focus_arena() -> bool:
    """Bring the Arena window to the foreground. Returns False if Arena is not running."""
    global _arena_hwnd
    import ctypes

    # Re-validate cached handle — Arena may have been closed since last call
    if _arena_hwnd is not None and not ctypes.windll.user32.IsWindow(_arena_hwnd):
        _arena_hwnd = None

    if _arena_hwnd is None:
        _arena_hwnd = _find_arena_hwnd()
    if _arena_hwnd is None:
        return False

    try:
        ctypes.windll.user32.ShowWindow(_arena_hwnd, 9)   # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(_arena_hwnd)
        return True
    except Exception:
        _arena_hwnd = None  # invalidate cache on error
        return False


def find_arena_window() -> dict | None:
    """Return the bounding box of the MTG Arena window, or None if not found."""
    bounds = get_arena_window_bounds()
    if bounds is not None:
        logger.debug(
            f"Arena window: {bounds['width']}x{bounds['height']} "
            f"at ({bounds['left']},{bounds['top']})"
        )
        return bounds

    # Fall back to primary monitor
    logger.debug("Falling back to primary monitor capture")
    with mss.mss() as sct:
        return sct.monitors[1]


class ScreenCapture:
    def __init__(self, monitor: dict | None = None):
        self._fixed_monitor = monitor is not None
        self._monitor = monitor or find_arena_window()

    @property
    def monitor(self) -> dict:
        return self._monitor

    def refresh_monitor(self) -> dict:
        if not self._fixed_monitor:
            self._monitor = find_arena_window()
        return self._monitor

    def grab(self) -> np.ndarray:
        """Capture the current screen region as a BGR numpy array."""
        self.refresh_monitor()
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
