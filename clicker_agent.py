from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field

import pyautogui

from decision_engine import ActionPlan, ActionType
from game_state import CardSnapshot, GameSnapshot, GameStateManager
from src.capture.screen import ScreenCapture, focus_arena
from src.game_state.state import CardObject, Zone
from src.vision.detector import VisionDetector
from src.vision.layout import CardPositionMapper

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
class ExecutionContext:
    pass_button_visible: bool = False
    pass_button_pos: tuple[int, int] | None = None
    ok_button_visible: bool = False
    ok_button_pos: tuple[int, int] | None = None
    keep_hand_button_visible: bool = False
    keep_hand_button_pos: tuple[int, int] | None = None
    mulligan_button_visible: bool = False
    mulligan_button_pos: tuple[int, int] | None = None
    discard_prompt_visible: bool = False
    discard_submit_pos: tuple[int, int] | None = None
    playable_hand_positions: list[tuple[int, int]] = field(default_factory=list)
    opponent_player_pos: tuple[int, int] | None = None


@dataclass
class ExecutionResult:
    status: str
    before: GameSnapshot
    after: GameSnapshot
    action: ActionPlan
    reason: str = ""


class ExecutionHandler:
    """Arena-specific execution layer with its own vision and coordinate mapping."""

    def __init__(
        self,
        config: dict,
        state_manager: GameStateManager,
        action_delay: float = 0.8,
        verification_timeout: float = 2.5,
        verification_poll_interval: float = 0.25,
    ):
        self.state_manager = state_manager
        self.action_delay = action_delay
        self.verification_timeout = verification_timeout
        self.verification_poll_interval = verification_poll_interval
        self.capture = ScreenCapture()
        self.detector = VisionDetector(
            reference_resolution=tuple(config.get("arena", {}).get("reference_resolution", [2560, 1440])),
            threshold=config.get("vision", {}).get("template_threshold", 0.80),
        )
        self.layout = CardPositionMapper.from_config(config)

    def capture_context(self, state: GameSnapshot) -> ExecutionContext:
        if not state.arena_running:
            return ExecutionContext(opponent_player_pos=self.layout.opp_player_position())

        frame = self.capture.grab()
        buttons = self.detector.detect_buttons(frame)
        discard_visible, discard_submit_pos = self.detector.detect_discard_state(frame)
        ok_vis, ok_pos = buttons["ok"]
        if not ok_vis and state.has_priority and state.phase == "UNKNOWN":
            ok_vis = True

        context = ExecutionContext(
            pass_button_visible=buttons["pass"][0],
            pass_button_pos=buttons["pass"][1],
            ok_button_visible=ok_vis,
            ok_button_pos=ok_pos,
            keep_hand_button_visible=buttons["keep_hand"][0],
            keep_hand_button_pos=buttons["keep_hand"][1],
            mulligan_button_visible=buttons["mulligan"][0],
            mulligan_button_pos=buttons["mulligan"][1],
            discard_prompt_visible=discard_visible,
            discard_submit_pos=discard_submit_pos,
            playable_hand_positions=self.detector.detect_playable_hand_cards(frame),
            opponent_player_pos=self.layout.opp_player_position(),
        )
        LOGGER.debug(
            "Execution context: keep=%s mulligan=%s discard=%s playable=%s",
            context.keep_hand_button_visible,
            context.mulligan_button_visible,
            context.discard_prompt_visible,
            len(context.playable_hand_positions),
        )
        return context

    def execute(
        self,
        plan: ActionPlan,
        state: GameSnapshot,
        context: ExecutionContext | None = None,
    ) -> ExecutionResult:
        before_snapshot = state
        before_context = context or self.capture_context(state)

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
        if not self._dispatch(plan, state, before_context):
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

    def _dispatch(self, plan: ActionPlan, state: GameSnapshot, context: ExecutionContext) -> bool:
        match plan.action_type:
            case action if action in _KEY_MAP:
                self._press(_KEY_MAP[action])
                return True
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL:
                coordinates = self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._double_click(coordinates)
                return True
            case ActionType.SELECT_DISCARD:
                coordinates = self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.MULLIGAN:
                if context.mulligan_button_pos is None:
                    return False
                self._click(context.mulligan_button_pos)
                return True
            case ActionType.CONFIRM_DISCARD:
                if context.discard_submit_pos is None:
                    return False
                self._click(context.discard_submit_pos)
                return True
            case ActionType.SELECT_TARGET:
                coordinates = self._resolve_ref(plan.target, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_ATTACKER:
                coordinates = self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_BLOCKER:
                blocker = self._resolve_ref(plan.subject, state, context)
                attacker = self._resolve_ref(plan.target, state, context)
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
        state: GameSnapshot,
        context: ExecutionContext,
    ) -> tuple[int, int] | None:
        if ref is None:
            return None

        kind = ref.get("kind")
        if kind == "player":
            if ref.get("who") == "opponent":
                LOGGER.debug("Resolved player target: opponent -> %s", context.opponent_player_pos)
                return context.opponent_player_pos
            return None
        if kind == "card":
            return self._resolve_card(ref, state, context)
        return None

    def _resolve_card(
        self,
        ref: dict[str, object],
        state: GameSnapshot,
        context: ExecutionContext,
    ) -> tuple[int, int] | None:
        zone = ref.get("zone")
        controller = ref.get("controller", "self")
        instance_id = ref.get("instance_id")
        name = ref.get("name")

        cards: list[CardSnapshot]
        if zone == "HAND":
            cards = state.we.hand
        elif zone == "BATTLEFIELD" and controller == "opponent":
            cards = state.opponent.battlefield
        elif zone == "BATTLEFIELD":
            cards = state.we.battlefield
        else:
            return None

        match_index, match_card = self._find_card(cards, instance_id, name)
        if match_card is None:
            return None

        if zone == "HAND":
            coordinates = self._resolve_hand_card_position(match_index, len(cards), context)
            LOGGER.debug("Resolved hand card %s -> %s", name, coordinates)
            return coordinates

        positions = self._battlefield_positions(cards, is_ours=controller != "opponent")
        coordinates = positions.get(self._card_identity(match_card, match_index))
        LOGGER.debug("Resolved battlefield card %s -> %s", name, coordinates)
        return coordinates

    def _resolve_hand_card_position(
        self,
        index: int,
        total: int,
        context: ExecutionContext,
    ) -> tuple[int, int] | None:
        estimated_x, estimated_y = self.layout.hand_position(index, total)
        playable = context.playable_hand_positions
        if not playable:
            return estimated_x, estimated_y

        distances = [(abs(px - estimated_x), px, py) for px, py in playable]
        min_distance, px, py = min(distances)
        if min_distance < 100:
            return px, py
        return estimated_x, estimated_y

    def _battlefield_positions(
        self,
        cards: list[CardSnapshot],
        *,
        is_ours: bool,
    ) -> dict[int | str, tuple[int, int]]:
        clones = [self._clone_card(card) for card in cards]
        self.layout.assign_battlefield_positions(clones, is_ours=is_ours)
        positions: dict[int | str, tuple[int, int]] = {}
        for index, card in enumerate(clones):
            positions[self._card_identity(cards[index], index)] = (card.screen_x, card.screen_y)
        return positions

    @staticmethod
    def _find_card(
        cards: list[CardSnapshot],
        instance_id: object,
        name: object,
    ) -> tuple[int, CardSnapshot | None]:
        if instance_id is not None:
            for index, card in enumerate(cards):
                if card.instance_id == instance_id:
                    return index, card
        if isinstance(name, str):
            for index, card in enumerate(cards):
                if card.name == name:
                    return index, card
        return -1, None

    @staticmethod
    def _clone_card(card: CardSnapshot) -> CardObject:
        return CardObject(
            name=card.name,
            zone=Zone[card.zone],
            instance_id=card.instance_id,
            cmc=card.cmc,
            power=card.power,
            toughness=card.toughness,
            is_tapped=card.is_tapped,
            is_summoning_sick=card.is_summoning_sick,
            produces_mana=list(card.produces_mana),
            keywords=list(card.keywords),
            card_type=card.card_type,
            color=card.color,
        )

    @staticmethod
    def _card_identity(card: CardSnapshot, index: int) -> int | str:
        if card.instance_id is not None:
            return card.instance_id
        return f"{card.name}:{card.zone}:{index}"

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
