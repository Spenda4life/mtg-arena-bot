from __future__ import annotations
import ctypes
import time
import pyautogui
from loguru import logger
from src.engine.actions import Action, ActionType, KEYBOARD_ACTIONS
from src.capture.screen import focus_arena

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03

_user32 = ctypes.windll.user32
_SCREEN_W: int = _user32.GetSystemMetrics(0)
_SCREEN_H: int = _user32.GetSystemMetrics(1)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# Hand region starts at ~78% of screen height (matches detector.py's y0 = 0.78 * h)
_HAND_Y_THRESHOLD = int(0.78 * _SCREEN_H)
_SAFE_X = _SCREEN_W // 2
_SAFE_Y = _SCREEN_H // 3


def park_cursor_if_over_hand() -> None:
    """Move cursor to a neutral position if it's hovering over the hand region.

    Called before each screen grab so hover effects don't obscure hand cards
    in the captured frame. Only moves the cursor when necessary.
    """
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    if pt.y >= _HAND_Y_THRESHOLD:
        _user32.SetCursorPos(_SAFE_X, _SAFE_Y)

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP   = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_KEYEVENTF_KEYUP      = 0x0002

# Virtual key codes for Arena keyboard shortcuts
_VK_MAP = {
    "space":  0x20,
    "f4":     0x73,
    "f6":     0x75,
    "escape": 0x1B,
    "enter":  0x0D,
}


def _arena_click(x: int, y: int) -> None:
    """Send a mouse click to Arena via mouse_event (pyautogui.click is blocked by Unity)."""
    nx = int(x * 65535 / _SCREEN_W)
    ny = int(y * 65535 / _SCREEN_H)
    _user32.SetCursorPos(x, y)
    time.sleep(0.05)
    _user32.mouse_event(_MOUSEEVENTF_LEFTDOWN | _MOUSEEVENTF_ABSOLUTE, nx, ny, 0, 0)
    time.sleep(0.05)
    _user32.mouse_event(_MOUSEEVENTF_LEFTUP   | _MOUSEEVENTF_ABSOLUTE, nx, ny, 0, 0)


def _arena_key(key: str) -> None:
    """Send a keypress to Arena via keybd_event (pyautogui.press is blocked by Unity)."""
    vk = _VK_MAP.get(key)
    if vk is None:
        logger.warning(f"No VK code for key '{key}'")
        return
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


# Arena keyboard shortcuts
_KEY_MAP = {
    ActionType.KEY_SPACE:        "space",
    ActionType.KEY_F4:           "f4",
    ActionType.KEY_F6:           "f6",
    ActionType.KEY_ESCAPE:       "escape",
    ActionType.KEY_ENTER:        "enter",
    ActionType.KEEP_HAND:        "space",
    ActionType.CONFIRM_ATTACKERS:"space",
    ActionType.CONFIRM_BLOCKERS: "space",
}


class InputController:
    def __init__(self, action_delay: float = 0.6, click_duration: float = 0.12):
        self.action_delay = action_delay
        self.click_duration = click_duration

    def execute(self, action: Action) -> None:
        if not focus_arena():
            logger.warning(f"Arena not running — skipping action {action.type.name}")
            return
        logger.info(f">> {action}")

        match action.type:
            # --- Pure keyboard ---
            case t if t in _KEY_MAP:
                self._press(_KEY_MAP[t])

            # --- Card plays: double-click to cast/play (single click only selects) ---
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL:
                self._double_click(action.target_x, action.target_y)

            case ActionType.CLICK:
                self._click(action.target_x, action.target_y)

            case ActionType.CLICK_TARGET:
                # Brief pause so Arena registers the spell is waiting for a target
                time.sleep(0.2)
                self._click(action.target_x, action.target_y)

            case ActionType.DECLARE_ATTACKER:
                self._click(action.target_x, action.target_y)

            case ActionType.DECLARE_BLOCKER:
                self._click(action.target_x, action.target_y)
                time.sleep(0.25)
                self._click(action.target2_x, action.target2_y)

            case ActionType.MULLIGAN:
                # Mulligan has no reliable keyboard shortcut — must click the button
                self._click(action.target_x, action.target_y)

            case _:
                logger.warning(f"Unhandled action type: {action.type}")
                return

        time.sleep(self.action_delay)

    def _press(self, key: str) -> None:
        if not focus_arena():
            logger.warning(f"Arena not focused — aborting keypress '{key}'")
            return
        time.sleep(0.15)
        _arena_key(key)
        logger.debug(f"keypress: {key}")

    def _click(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            logger.warning("Click with no coordinates — skipping")
            return
        if not focus_arena():
            logger.warning(f"Arena not focused — aborting click ({x}, {y})")
            return
        _arena_click(x, y)
        logger.debug(f"click: ({x}, {y})")

    def _double_click(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            logger.warning("Double-click with no coordinates — skipping")
            return
        if not focus_arena():
            logger.warning(f"Arena not focused — aborting double-click ({x}, {y})")
            return
        _arena_click(x, y)
        time.sleep(0.08)
        _arena_click(x, y)
        logger.debug(f"double-click: ({x}, {y})")
