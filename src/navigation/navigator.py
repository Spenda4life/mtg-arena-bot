from __future__ import annotations
import ctypes
import ctypes.wintypes
import time
from loguru import logger

from src.capture.screen import ScreenCapture, focus_arena
from src.vision.detector import VisionDetector
from src.input.controller import _arena_click

_shell32 = ctypes.windll.shell32
_user32  = ctypes.windll.user32


class _AppBarData(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('hWnd', ctypes.wintypes.HWND),
                ('uCallbackMessage', ctypes.c_uint), ('uEdge', ctypes.c_uint),
                ('rc', ctypes.wintypes.RECT), ('lParam', ctypes.c_long)]

_ABM_GETSTATE = 4
_ABM_SETSTATE = 10
_ABS_AUTOHIDE    = 1
_ABS_ALWAYSONTOP = 2


def _taskbar_autohide(enable: bool) -> int:
    """Toggle taskbar auto-hide. Returns previous state."""
    abd = _AppBarData()
    abd.cbSize = ctypes.sizeof(abd)
    prev = _shell32.SHAppBarMessage(_ABM_GETSTATE, ctypes.byref(abd))
    abd.lParam = _ABS_AUTOHIDE if enable else _ABS_ALWAYSONTOP
    _shell32.SHAppBarMessage(_ABM_SETSTATE, ctypes.byref(abd))
    return prev


def _safe_click(x: int, y: int) -> None:
    """Click a screen position, auto-hiding the taskbar if needed."""
    focus_arena()
    screen_h = _user32.GetSystemMetrics(1)
    taskbar_top = screen_h - 48
    prev_state = None
    if y > taskbar_top:
        prev_state = _taskbar_autohide(True)
        _user32.SetCursorPos(x, screen_h // 2)
        time.sleep(1.0)
    _arena_click(x, y)
    if prev_state is not None:
        time.sleep(0.2)
        _taskbar_autohide(prev_state == _ABS_AUTOHIDE)


# Fixed screen position of the "Recently Played" tab in the right panel.
# Calibrated for 1920x1080; scales proportionally for other resolutions.
_RECENTLY_PLAYED_TAB_FX = 1877 / 1920
_RECENTLY_PLAYED_TAB_FY = 105  / 1080


class NavigationEngine:
    """Navigates Arena menus to start a Bot Match and handles post-game flow."""

    def __init__(
        self,
        detector: VisionDetector,
        capture: ScreenCapture,
        config: dict,
        log_parser=None,
    ):
        self.detector = detector
        self.capture = capture
        self._log_parser = log_parser
        self.initial_state = None
        nav_cfg = config.get("navigation", {})
        self.click_delay = nav_cfg.get("nav_click_delay", 1.5)
        self.max_retries = nav_cfg.get("max_retries", 3)
        self.game_start_timeout = nav_cfg.get("game_start_timeout", 60)

    def navigate_to_game(self) -> bool:
        """Navigate from the home screen to a Bot Match game.

        Flow: Home Play → Recently Played tab → Play (Bot Match)

        Returns True when the mulligan screen is detected (game started).
        Returns False if navigation fails after retries.
        """
        # Fast path: already in a game
        frame = self.capture.grab()
        btns = self.detector.detect_buttons(frame)
        if btns.get("keep_hand", (False,))[0] or btns.get("mulligan", (False,))[0]:
            logger.info("[Nav] Already in a game (mulligan screen) — skipping navigation")
            return True
        if self._log_parser is not None:
            state = self._log_parser.poll()
            if state is not None and state.phase.name != "UNKNOWN":
                # Sanity check: if the home Play button is visible we're NOT in a game
                # (log state can be stale from a finished game)
                check_frame = self.capture.grab()
                home_visible = self.detector.detect_nav_buttons(check_frame).get(
                    "nav_play", (False, None)
                )[0]
                if not home_visible:
                    logger.info(f"[Nav] Already in a game (phase={state.phase.name}) — skipping navigation")
                    self.initial_state = state
                    return True
                logger.info("[Nav] Log shows in-game but home screen detected — navigating")

        logger.info("[Nav] Navigating to Bot Match via Recently Played")

        # Step 1: click the home Play button
        if not self._click_template("nav_play", "Home Play button"):
            return False
        time.sleep(self.click_delay)

        # Step 2: click the Recently Played tab (fixed position — always select it
        # to guarantee Bot Match is shown in the right panel)
        frame = self.capture.grab()
        h, w = frame.shape[:2]
        tab_x = int(w * _RECENTLY_PLAYED_TAB_FX)
        tab_y = int(h * _RECENTLY_PLAYED_TAB_FY)
        logger.info(f"[Nav] Clicking Recently Played tab at ({tab_x}, {tab_y})")
        _safe_click(tab_x, tab_y)
        time.sleep(self.click_delay)

        # Step 3: click Play under Bot Match in the right panel
        if not self._click_template("nav_submit", "Bot Match Play button"):
            return False

        logger.info("[Nav] Waiting for game to start...")
        return self._wait_for_game_start()

    def handle_game_over(self) -> None:
        """Dismiss the end-of-game result screen (always a 'click to continue')."""
        logger.info("[Nav] Game over — clicking to continue")
        time.sleep(3.0)
        frame = self.capture.grab()
        h, w = frame.shape[:2]
        # Click the center of the screen to dismiss the result overlay
        _safe_click(w // 2, h // 2)
        time.sleep(self.click_delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _click_template(self, template_key: str, label: str, required: bool = True) -> bool:
        """Locate a nav button by template match and click it. Retries up to max_retries."""
        for attempt in range(1, self.max_retries + 1):
            frame = self.capture.grab()
            buttons = self.detector.detect_nav_buttons(frame)
            visible, pos = buttons.get(template_key, (False, None))
            if visible and pos:
                px, py = pos
                logger.info(f"[Nav] {label} found at ({px}, {py}) — clicking")
                _safe_click(px, py)
                return True
            logger.debug(f"[Nav] {label} not found (attempt {attempt}/{self.max_retries})")
            time.sleep(1.0)
        if required:
            logger.error(f"[Nav] {label} not found after {self.max_retries} retries")
        return False

    def _wait_for_game_start(self) -> bool:
        """Poll until mulligan/keep screen appears or timeout."""
        deadline = time.time() + self.game_start_timeout
        while time.time() < deadline:
            frame = self.capture.grab()
            buttons = self.detector.detect_buttons(frame)
            if buttons.get("keep_hand", (False,))[0] or buttons.get("mulligan", (False,))[0]:
                logger.info("[Nav] Mulligan screen detected — game started")
                return True
            time.sleep(0.5)
        logger.error(f"[Nav] Game did not start within {self.game_start_timeout}s")
        return False
