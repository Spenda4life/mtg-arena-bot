from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass

import pyautogui

from decision_engine import ActionPlan, ActionType
from game_state import CardSnapshot, GameSnapshot, GameStateManager
from src.capture.screen import focus_arena

LOGGER = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03

_USER32 = ctypes.windll.user32
_SCREEN_W = _USER32.GetSystemMetrics(0)
_SCREEN_H = _USER32.GetSystemMetrics(1)
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_KEYEVENTF_KEYUP = 0x0002

_VK_MAP = {
    "space": 0x20,
    "f4": 0x73,
    "f6": 0x75,
    "escape": 0x1B,
    "enter": 0x0D,
}

_KEY_MAP = {
    ActionType.PASS_PRIORITY: "space",
    ActionType.KEEP_HAND: "space",
    ActionType.CONFIRM_ATTACKERS: "space",
}


class ExecutionStatus(str):
    SUCCESS = "Success"
    FAILURE = "Failure"


@dataclass
class ExecutionResult:
    status: str
    before: GameSnapshot
    after: GameSnapshot
    action: ActionPlan
    reason: str = ""


class ExecutionHandler:
    """Arena-specific execution layer that resolves semantic actions to clicks."""

    def __init__(
        self,
        state_manager: GameStateManager,
        action_delay: float = 0.8,
        verification_timeout: float = 2.5,
        verification_poll_interval: float = 0.25,
    ):
        self.state_manager = state_manager
        self.action_delay = action_delay
        self.verification_timeout = verification_timeout
        self.verification_poll_interval = verification_poll_interval

    def execute(self, plan: ActionPlan, before: GameSnapshot | None = None) -> ExecutionResult:
        before_snapshot = before or self.state_manager.get_snapshot()

        if not focus_arena():
            LOGGER.warning("Arena is not focused; cannot execute %s", plan.description)
            after_snapshot = self.state_manager.refresh()
            return ExecutionResult(
                status=ExecutionStatus.FAILURE,
                before=before_snapshot,
                after=after_snapshot,
                action=plan,
                reason="Arena not focused",
            )

        LOGGER.info("Executing action: %s", plan.description or plan.action_type.value)
        if not self._dispatch(plan, before_snapshot):
            after_snapshot = self.state_manager.refresh()
            return ExecutionResult(
                status=ExecutionStatus.FAILURE,
                before=before_snapshot,
                after=after_snapshot,
                action=plan,
                reason="Could not resolve semantic action to an Arena input",
            )

        time.sleep(self.action_delay)
        deadline = time.time() + self.verification_timeout
        latest = self.state_manager.refresh()
        while time.time() <= deadline:
            if self.state_manager.verify_expected_change(before_snapshot, latest, plan.expected_state_change):
                LOGGER.info("Verified action success: %s", plan.description or plan.action_type.value)
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    before=before_snapshot,
                    after=latest,
                    action=plan,
                )
            time.sleep(self.verification_poll_interval)
            latest = self.state_manager.refresh()

        LOGGER.warning("Verification failed for action: %s", plan.description or plan.action_type.value)
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            before=before_snapshot,
            after=latest,
            action=plan,
            reason="Expected state change not observed",
        )

    def _dispatch(self, plan: ActionPlan, snapshot: GameSnapshot) -> bool:
        match plan.action_type:
            case action if action in _KEY_MAP:
                self._press(_KEY_MAP[action])
                return True
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL:
                coordinates = self._resolve_ref(plan.subject, snapshot)
                if coordinates is None:
                    return False
                self._double_click(coordinates)
                return True
            case ActionType.SELECT_DISCARD | ActionType.MULLIGAN | ActionType.CONFIRM_DISCARD:
                coordinates = self._resolve_ref(plan.subject, snapshot)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.SELECT_TARGET:
                coordinates = self._resolve_ref(plan.target, snapshot)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_ATTACKER:
                coordinates = self._resolve_ref(plan.subject, snapshot)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_BLOCKER:
                blocker = self._resolve_ref(plan.subject, snapshot)
                attacker = self._resolve_ref(plan.target, snapshot)
                if blocker is None or attacker is None:
                    return False
                self._click(blocker)
                time.sleep(0.25)
                self._click(attacker)
                return True
            case ActionType.CANCEL:
                self._press("escape")
                return True
            case _:
                LOGGER.warning("Unhandled action type: %s", plan.action_type.value)
                return False

    def _resolve_ref(
        self,
        ref: dict[str, object] | None,
        snapshot: GameSnapshot,
    ) -> tuple[int, int] | None:
        if ref is None:
            return None

        kind = ref.get("kind")
        if kind == "button":
            return self._resolve_button(str(ref.get("name", "")), snapshot)
        if kind == "player":
            if ref.get("who") == "opponent":
                return snapshot.opponent_player_pos
            return None
        if kind == "card":
            return self._resolve_card(ref, snapshot)
        return None

    def _resolve_button(self, name: str, snapshot: GameSnapshot) -> tuple[int, int] | None:
        mapping = {
            "mulligan": snapshot.mulligan_button_pos,
            "discard_submit": snapshot.discard_submit_pos,
        }
        return mapping.get(name)

    def _resolve_card(
        self,
        ref: dict[str, object],
        snapshot: GameSnapshot,
    ) -> tuple[int, int] | None:
        zone = ref.get("zone")
        controller = ref.get("controller", "self")
        instance_id = ref.get("instance_id")
        name = ref.get("name")

        cards: list[CardSnapshot]
        if zone == "HAND":
            cards = snapshot.we.hand
        elif zone == "BATTLEFIELD" and controller == "opponent":
            cards = snapshot.opponent.battlefield
        elif zone == "BATTLEFIELD":
            cards = snapshot.we.battlefield
        else:
            return None

        match = None
        if instance_id is not None:
            match = next((card for card in cards if card.instance_id == instance_id), None)
        if match is None and name is not None:
            match = next((card for card in cards if card.name == name), None)
        if match is None:
            return None

        if zone == "HAND":
            return self._resolve_hand_card_position(match, snapshot)
        if match.screen_x is None or match.screen_y is None:
            return None
        return match.screen_x, match.screen_y

    @staticmethod
    def _resolve_hand_card_position(card: CardSnapshot, snapshot: GameSnapshot) -> tuple[int, int] | None:
        if card.screen_x is None or card.screen_y is None:
            return None
        playable = snapshot.playable_hand_positions
        if not playable:
            return card.screen_x, card.screen_y
        distances = [(abs(px - card.screen_x), px, py) for px, py in playable]
        min_distance, px, py = min(distances)
        if min_distance < 100:
            return px, py
        return card.screen_x, card.screen_y

    def _press(self, key: str) -> None:
        vk = _VK_MAP.get(key)
        if vk is None:
            LOGGER.warning("No virtual key mapping for %s", key)
            return
        time.sleep(0.15)
        _USER32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        _USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)

    def _click(self, coordinates: tuple[int, int] | None) -> None:
        if coordinates is None:
            LOGGER.warning("Click requested without coordinates")
            return
        x, y = coordinates
        nx = int(x * 65535 / _SCREEN_W)
        ny = int(y * 65535 / _SCREEN_H)
        _USER32.SetCursorPos(x, y)
        time.sleep(0.05)
        _USER32.mouse_event(_MOUSEEVENTF_LEFTDOWN | _MOUSEEVENTF_ABSOLUTE, nx, ny, 0, 0)
        time.sleep(0.05)
        _USER32.mouse_event(_MOUSEEVENTF_LEFTUP | _MOUSEEVENTF_ABSOLUTE, nx, ny, 0, 0)

    def _double_click(self, coordinates: tuple[int, int] | None) -> None:
        self._click(coordinates)
        time.sleep(0.08)
        self._click(coordinates)
