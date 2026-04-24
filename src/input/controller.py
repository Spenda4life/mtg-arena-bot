from __future__ import annotations
import time
import pyautogui
from loguru import logger
from src.engine.actions import Action, ActionType

# Safety: pyautogui will raise an exception if the mouse hits a screen corner
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05  # small base pause between pyautogui calls


class InputController:
    def __init__(self, action_delay: float = 0.8):
        self.action_delay = action_delay

    def execute(self, action: Action) -> None:
        logger.info(f"Executing: {action}")
        match action.type:
            case ActionType.PASS_PRIORITY | ActionType.CLICK_OK | \
                 ActionType.KEEP_HAND | ActionType.MULLIGAN | \
                 ActionType.CONFIRM_ATTACKERS | ActionType.CONFIRM_BLOCKERS | \
                 ActionType.CLICK_END_STEP:
                self._click(action.target_x, action.target_y)

            case ActionType.PLAY_LAND | ActionType.CAST_SPELL | \
                 ActionType.CLICK_CARD | ActionType.DECLARE_ATTACKER:
                self._click(action.target_x, action.target_y)

            case ActionType.DECLARE_BLOCKER:
                # Click blocker, then click attacker to assign block
                self._click(action.target_x, action.target_y)
                time.sleep(0.3)
                self._click(action.target2_x, action.target2_y)

        time.sleep(self.action_delay)

    def _click(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            logger.warning("Click called with no coordinates — skipping")
            return
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click()
