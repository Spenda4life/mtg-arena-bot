from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pyautogui

from decision_engine import ActionPlan, ActionType
from game_state import CardSnapshot, GameSnapshot, GameStateManager
from src.overlay import Overlay, OverlayData, OverlayMarker
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
    frame: Any | None = None
    window_bounds: dict[str, int] | None = None


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
        pre_click_delay: float = 0.0,
        verification_timeout: float = 2.5,
        verification_poll_interval: float = 0.25,
        overlay: Overlay | None = None,
    ):
        self.state_manager = state_manager
        self.action_delay = action_delay
        self.pre_click_delay = pre_click_delay
        self.verification_timeout = verification_timeout
        self.verification_poll_interval = verification_poll_interval
        self.overlay = overlay
        self.capture = ScreenCapture()
        self.detector = VisionDetector(
            reference_resolution=tuple(config.get("arena", {}).get("reference_resolution", [2560, 1440])),
            threshold=config.get("vision", {}).get("template_threshold", 0.80),
        )
        self.layout = CardPositionMapper.from_config(config)
        hover_scan_cfg = config.get("vision", {}).get("hover_scan", {})
        self.hand_hover_scan_enabled = hover_scan_cfg.get("enabled", True)
        self.hand_hover_scan_delay = float(hover_scan_cfg.get("hover_delay", 0.25))
        self.hand_hover_scan_points_per_card = int(hover_scan_cfg.get("points_per_card", 3))
        self.hand_hover_scan_min_steps = int(hover_scan_cfg.get("min_steps", 12))
        self.hand_hover_scan_max_steps = int(hover_scan_cfg.get("max_steps", 32))
        self.hand_hover_scan_y = float(hover_scan_cfg.get("y", self.layout.cfg.hand_y))
        self.hand_hover_scan_x_min = float(hover_scan_cfg.get("x_min", self.layout.cfg.hand_x_min))
        self.hand_hover_scan_x_max = float(hover_scan_cfg.get("x_max", self.layout.cfg.hand_x_max))
        self.hand_hover_scan_crop_width = float(hover_scan_cfg.get("ocr_crop_width", 0.34))

    def capture_context(self, state: GameSnapshot) -> ExecutionContext:
        if not state.arena_running:
            return ExecutionContext(opponent_player_pos=self.layout.opp_player_position())

        frame = self.capture.grab()
        window_bounds = self.capture.monitor
        self._sync_layout_to_frame(frame)
        buttons = self.detector.detect_buttons(frame)
        discard_visible, discard_submit_pos = self.detector.detect_discard_state(frame)
        ok_vis, ok_pos = buttons["ok"]
        if not ok_vis and state.has_priority and state.phase == "UNKNOWN":
            ok_vis = True

        context = ExecutionContext(
            pass_button_visible=buttons["pass"][0],
            pass_button_pos=self._to_screen_pos(buttons["pass"][1], window_bounds),
            ok_button_visible=ok_vis,
            ok_button_pos=self._to_screen_pos(ok_pos, window_bounds),
            keep_hand_button_visible=buttons["keep_hand"][0],
            keep_hand_button_pos=self._to_screen_pos(buttons["keep_hand"][1], window_bounds),
            mulligan_button_visible=buttons["mulligan"][0],
            mulligan_button_pos=self._to_screen_pos(buttons["mulligan"][1], window_bounds),
            discard_prompt_visible=discard_visible,
            discard_submit_pos=self._to_screen_pos(discard_submit_pos, window_bounds),
            playable_hand_positions=[
                pos
                for pos in (
                    self._to_screen_pos(pos, window_bounds)
                    for pos in self.detector.detect_playable_hand_cards(frame)
                )
                if pos is not None
            ],
            opponent_player_pos=self._to_screen_pos(self.layout.opp_player_position(), window_bounds),
            frame=frame,
            window_bounds=window_bounds,
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
        preview = self._plan_input_preview(plan, state, before_context)
        self._push_overlay("Executing", plan, state, before_context, preview)
        invalid_reason = self._invalid_preview_reason(preview, before_context)
        if invalid_reason:
            self._push_overlay("Invalid target", plan, state, before_context, preview)
            after_snapshot = self.state_manager.refresh()
            LOGGER.warning("Refusing to execute %s: %s", plan.description, invalid_reason)
            return ExecutionResult(
                status=ExecutionStatus.FAILURE,
                before=before_snapshot,
                after=after_snapshot,
                action=plan,
                reason=invalid_reason,
            )

        if self.pre_click_delay > 0:
            time.sleep(self.pre_click_delay)

        if not self._dispatch(plan, state, before_context, preview):
            self._push_overlay("Could not resolve", plan, state, before_context, preview)
            after_snapshot = self.state_manager.refresh()
            return ExecutionResult(
                status=ExecutionStatus.FAILURE,
                before=before_snapshot,
                after=after_snapshot,
                action=plan,
                reason="Could not resolve semantic action to an Arena input",
            )

        time.sleep(self.action_delay)
        verification_timeout = self._verification_timeout_for(plan)
        deadline = time.time() + verification_timeout
        LOGGER.debug(
            "Verification window for %s: %.2fs",
            plan.description or plan.action_type.value,
            verification_timeout,
        )
        latest = self.state_manager.refresh()
        while time.time() <= deadline:
            if self.state_manager.verify_expected_change(before_snapshot, latest, plan.expected_state_change):
                self._push_overlay("Verified", plan, latest, before_context, preview)
                LOGGER.info("Verified action success: %s", plan.description or plan.action_type.value)
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    before=before_snapshot,
                    after=latest,
                    action=plan,
                )
            time.sleep(self.verification_poll_interval)
            latest = self.state_manager.refresh()

        self._push_overlay("Verification failed", plan, latest, before_context, preview)
        LOGGER.warning("Verification failed for action: %s", plan.description or plan.action_type.value)
        return ExecutionResult(
            status=ExecutionStatus.FAILURE,
            before=before_snapshot,
            after=latest,
            action=plan,
            reason="Expected state change not observed",
        )

    def _verification_timeout_for(self, plan: ActionPlan) -> float:
        timeout = self.verification_timeout
        expected = plan.expected_state_change or {}

        if (
            plan.action_type in {
                ActionType.KEEP_HAND,
                ActionType.MULLIGAN,
                ActionType.PLAY_LAND,
                ActionType.CAST_SPELL,
                ActionType.PASS_PRIORITY,
                ActionType.CONFIRM_ATTACKERS,
                ActionType.CONFIRM_DISCARD,
            }
            or "hand_delta" in expected
            or expected.get("phase_changed")
            or "mulligan_pending" in expected
            or "discard_required" in expected
        ):
            timeout = max(timeout, 5.0)

        return timeout

    def _dispatch(
        self,
        plan: ActionPlan,
        state: GameSnapshot,
        context: ExecutionContext,
        preview: "InputPreview | None" = None,
    ) -> bool:
        match plan.action_type:
            case action if action in _KEY_MAP:
                self._press(_KEY_MAP[action])
                return True
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL:
                coordinates = preview.primary_target if preview else self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._double_click(coordinates)
                return True
            case ActionType.SELECT_DISCARD:
                coordinates = preview.primary_target if preview else self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.MULLIGAN:
                coordinates = preview.primary_target if preview else context.mulligan_button_pos
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.CONFIRM_DISCARD:
                coordinates = preview.primary_target if preview else context.discard_submit_pos
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.SELECT_TARGET:
                coordinates = preview.primary_target if preview else self._resolve_ref(plan.target, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_ATTACKER:
                coordinates = preview.primary_target if preview else self._resolve_ref(plan.subject, state, context)
                if coordinates is None:
                    return False
                self._click(coordinates)
                return True
            case ActionType.DECLARE_BLOCKER:
                blocker = preview.primary_target if preview else self._resolve_ref(plan.subject, state, context)
                attacker = preview.secondary_target if preview else self._resolve_ref(plan.target, state, context)
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

    def _plan_input_preview(
        self,
        plan: ActionPlan,
        state: GameSnapshot,
        context: ExecutionContext,
    ) -> "InputPreview":
        match plan.action_type:
            case action if action in _KEY_MAP:
                return InputPreview(input_hint=f"key: {_KEY_MAP[action]}")
            case ActionType.PLAY_LAND | ActionType.CAST_SPELL:
                return InputPreview(
                    input_hint="double-click",
                    primary_target=self._resolve_ref(plan.subject, state, context),
                )
            case ActionType.SELECT_DISCARD | ActionType.SELECT_TARGET | ActionType.DECLARE_ATTACKER:
                return InputPreview(
                    input_hint="click",
                    primary_target=self._resolve_ref(
                        plan.subject if plan.action_type != ActionType.SELECT_TARGET else plan.target,
                        state,
                        context,
                    ),
                )
            case ActionType.MULLIGAN:
                return InputPreview(input_hint="click", primary_target=context.mulligan_button_pos)
            case ActionType.CONFIRM_DISCARD:
                return InputPreview(input_hint="click", primary_target=context.discard_submit_pos)
            case ActionType.DECLARE_BLOCKER:
                return InputPreview(
                    input_hint="click blocker, then attacker",
                    primary_target=self._resolve_ref(plan.subject, state, context),
                    secondary_target=self._resolve_ref(plan.target, state, context),
                )
            case ActionType.CANCEL:
                return InputPreview(input_hint="key: escape")
            case _:
                return InputPreview(input_hint="unknown")

    def _push_overlay(
        self,
        status: str,
        plan: ActionPlan,
        state: GameSnapshot,
        context: ExecutionContext,
        preview: "InputPreview",
    ) -> None:
        if self.overlay is None:
            return

        markers: list[OverlayMarker] = []
        for index, pos in enumerate(context.playable_hand_positions, start=1):
            markers.append(OverlayMarker(label=f"H{index}", position=pos, color="#00c8ff", radius=10))

        if context.pass_button_pos is not None:
            markers.append(OverlayMarker(label="PASS", position=context.pass_button_pos, color="#ffaa00", radius=10))
        if context.ok_button_pos is not None:
            markers.append(OverlayMarker(label="OK", position=context.ok_button_pos, color="#88ff66", radius=10))
        if context.keep_hand_button_pos is not None:
            markers.append(OverlayMarker(label="KEEP", position=context.keep_hand_button_pos, color="#88ff66", radius=10))
        if context.mulligan_button_pos is not None:
            markers.append(OverlayMarker(label="MULL", position=context.mulligan_button_pos, color="#ff8866", radius=10))

        if preview.primary_target is not None:
            markers.append(OverlayMarker(label="TARGET", position=preview.primary_target, color="#ff4d4d", radius=18))
        if preview.secondary_target is not None:
            markers.append(OverlayMarker(label="SECOND", position=preview.secondary_target, color="#ff7f50", radius=16))

        detail = []
        if preview.primary_target is not None:
            detail.append(f"primary={preview.primary_target}")
        if preview.secondary_target is not None:
            detail.append(f"secondary={preview.secondary_target}")
        detail.append(f"playable={len(context.playable_hand_positions)}")

        self.overlay.update(
            OverlayData(
                status=status,
                action=plan.description or plan.action_type.value,
                input_hint=preview.input_hint,
                phase=state.phase,
                turn=state.turn_number,
                has_priority=state.has_priority,
                detail=" | ".join(detail),
                frame_bgr=context.frame,
                markers=markers,
                window_bounds=context.window_bounds,
            )
        )

    @staticmethod
    def _to_screen_pos(
        position: tuple[int, int] | None,
        bounds: dict[str, int] | None,
    ) -> tuple[int, int] | None:
        if position is None or bounds is None:
            return position
        return position[0] + int(bounds.get("left", 0)), position[1] + int(bounds.get("top", 0))

    def _sync_layout_to_frame(self, frame: Any) -> None:
        shape = getattr(frame, "shape", None)
        if not shape or len(shape) < 2:
            return
        self.layout.w = int(shape[1])
        self.layout.h = int(shape[0])

    def _invalid_preview_reason(
        self,
        preview: "InputPreview",
        context: ExecutionContext,
    ) -> str:
        if context.window_bounds is None:
            return ""

        for label, position in (
            ("primary target", preview.primary_target),
            ("secondary target", preview.secondary_target),
        ):
            if position is not None and not self._point_in_bounds(position, context.window_bounds):
                return f"{label} {position} is outside Arena window {context.window_bounds}"
        return ""

    @staticmethod
    def _point_in_bounds(
        position: tuple[int, int],
        bounds: dict[str, int],
        margin: int = 8,
    ) -> bool:
        x, y = position
        left = int(bounds.get("left", 0)) - margin
        top = int(bounds.get("top", 0)) - margin
        right = int(bounds.get("left", 0)) + int(bounds.get("width", 0)) + margin
        bottom = int(bounds.get("top", 0)) + int(bounds.get("height", 0)) + margin
        return left <= x <= right and top <= y <= bottom

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
            coordinates = self._resolve_hand_card_position(match_index, len(cards), context, match_card.name)
            LOGGER.debug("Resolved hand card %s -> %s", name, coordinates)
            return coordinates

        positions = self._battlefield_positions(
            cards,
            is_ours=controller != "opponent",
            bounds=context.window_bounds,
        )
        coordinates = positions.get(self._card_identity(match_card, match_index))
        LOGGER.debug("Resolved battlefield card %s -> %s", name, coordinates)
        return coordinates

    def _resolve_hand_card_position(
        self,
        index: int,
        total: int,
        context: ExecutionContext,
        expected_name: str | None = None,
    ) -> tuple[int, int] | None:
        scanned = self._scan_hand_for_card(expected_name, total, context)
        if scanned is not None:
            return scanned

        estimated_x, estimated_y = self.layout.hand_position(index, total)
        estimated = self._to_screen_pos((estimated_x, estimated_y), context.window_bounds)
        if estimated is None:
            return None

        playable = context.playable_hand_positions
        if not playable:
            return estimated

        distances = [(abs(px - estimated[0]), px, py) for px, py in playable]
        min_distance, px, py = min(distances)
        if min_distance < 100:
            return px, py
        return estimated

    def _scan_hand_for_card(
        self,
        expected_name: str | None,
        total: int,
        context: ExecutionContext,
    ) -> tuple[int, int] | None:
        if (
            not expected_name
            or not getattr(self, "hand_hover_scan_enabled", False)
            or context.window_bounds is None
            or total <= 0
        ):
            return None

        bounds = context.window_bounds
        width = int(bounds.get("width", 0))
        height = int(bounds.get("height", 0))
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        if width <= 0 or height <= 0:
            return None

        x_min = left + int(width * getattr(self, "hand_hover_scan_x_min", 0.175))
        x_max = left + int(width * getattr(self, "hand_hover_scan_x_max", 0.825))
        y = top + int(height * getattr(self, "hand_hover_scan_y", 0.905))
        steps = max(
            int(getattr(self, "hand_hover_scan_min_steps", 12)),
            total * int(getattr(self, "hand_hover_scan_points_per_card", 3)),
        )
        steps = min(int(getattr(self, "hand_hover_scan_max_steps", 32)), steps)
        if steps <= 1 or x_max <= x_min:
            return None

        LOGGER.info("Hover-scanning hand for %s", expected_name)
        for step in range(steps):
            x = round(x_min + (x_max - x_min) * step / (steps - 1))
            screen_pos = (x, y)
            self._move_cursor(screen_pos)
            time.sleep(float(getattr(self, "hand_hover_scan_delay", 0.25)))

            frame = self.capture.grab()
            self._sync_layout_to_frame(frame)
            frame_bounds = self.capture.monitor or bounds
            hover_pos = self._screen_to_frame_pos(screen_pos, frame_bounds)
            if self.detector.frame_contains_card_name(
                frame,
                expected_name,
                hover_position=hover_pos,
                crop_width_fraction=float(getattr(self, "hand_hover_scan_crop_width", 0.34)),
            ):
                LOGGER.info("Hover scan matched %s at %s", expected_name, screen_pos)
                return screen_pos

        LOGGER.debug("Hover scan did not match %s; falling back to hand geometry", expected_name)
        return None

    def _battlefield_positions(
        self,
        cards: list[CardSnapshot],
        *,
        is_ours: bool,
        bounds: dict[str, int] | None = None,
    ) -> dict[int | str, tuple[int, int]]:
        clones = [self._clone_card(card) for card in cards]
        self.layout.assign_battlefield_positions(clones, is_ours=is_ours)
        positions: dict[int | str, tuple[int, int]] = {}
        for index, card in enumerate(clones):
            position = self._to_screen_pos((card.screen_x, card.screen_y), bounds)
            if position is not None:
                positions[self._card_identity(cards[index], index)] = position
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

    @staticmethod
    def _screen_to_frame_pos(
        position: tuple[int, int],
        bounds: dict[str, int] | None,
    ) -> tuple[int, int]:
        if bounds is None:
            return position
        return position[0] - int(bounds.get("left", 0)), position[1] - int(bounds.get("top", 0))

    def _move_cursor(self, position: tuple[int, int]) -> None:
        _USER32.SetCursorPos(position[0], position[1])


@dataclass
class InputPreview:
    input_hint: str
    primary_target: tuple[int, int] | None = None
    secondary_target: tuple[int, int] | None = None
