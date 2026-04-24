from __future__ import annotations
import time
import pyautogui
from loguru import logger
from src.engine.actions import Action, ActionType, KEYBOARD_ACTIONS

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03

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
        logger.info(f">> {action}")

        match action.type:
            # --- Pure keyboard ---
            case t if t in _KEY_MAP:
                self._press(_KEY_MAP[t])

            # --- Targeted clicks ---
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL | ActionType.CLICK:
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
        pyautogui.press(key)
        logger.debug(f"keypress: {key}")

    def _click(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            logger.warning("Click with no coordinates — skipping")
            return
        pyautogui.moveTo(x, y, duration=self.click_duration)
        pyautogui.click()
        logger.debug(f"click: ({x}, {y})")
