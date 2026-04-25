from __future__ import annotations
import re
import subprocess
import numpy as np
import mss
import mss.tools
from PIL import Image
from loguru import logger

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
    import subprocess

    # Ask PowerShell to return the window's screen rectangle via Win32 GetWindowRect.
    ps_script = """
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinUtil {
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hwnd, out RECT r);
    public struct RECT { public int left, top, right, bottom; }
}
"@
$proc = Get-Process | Where-Object {$_.MainWindowTitle -match 'MTGA|Magic.*Arena'} | Select-Object -First 1
if ($proc -and $proc.MainWindowHandle -ne 0) {
    $r = New-Object WinUtil+RECT
    [WinUtil]::GetWindowRect($proc.MainWindowHandle, [ref]$r) | Out-Null
    "$($r.left),$($r.top),$($r.right),$($r.bottom)"
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )
        coords = result.stdout.strip()
        if coords:
            left, top, right, bottom = map(int, coords.split(","))
            width, height = right - left, bottom - top
            if width > 0 and height > 0:
                logger.debug(f"Arena window: {width}x{height} at ({left},{top})")
                return {"left": left, "top": top, "width": width, "height": height}
    except Exception as e:
        logger.warning(f"Could not get Arena window bounds: {e}")

    # Fall back to primary monitor
    logger.debug("Falling back to primary monitor capture")
    with mss.mss() as sct:
        return sct.monitors[1]


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
