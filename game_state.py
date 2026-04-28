from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.capture.screen import ScreenCapture, is_arena_running
from src.game_state.grp_db import GrpDatabase
from src.game_state.log_parser import ArenaLogParser, LOG_PATH
from src.game_state.state import CardObject, GameState, PlayerState
from src.vision.detector import VisionDetector
from src.vision.layout import CardPositionMapper

LOGGER = logging.getLogger(__name__)


@dataclass
class CardSnapshot:
    name: str
    zone: str
    instance_id: int | None = None
    cmc: int = 0
    power: int | None = None
    toughness: int | None = None
    is_tapped: bool = False
    is_summoning_sick: bool = False
    produces_mana: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    card_type: str = ""
    color: str = ""
    is_playable: bool = False
    screen_x: int | None = None
    screen_y: int | None = None

    @property
    def is_land(self) -> bool:
        return "land" in self.card_type.lower()

    @property
    def is_creature(self) -> bool:
        return "creature" in self.card_type.lower()

    @property
    def can_attack(self) -> bool:
        return self.is_creature and not self.is_tapped and not self.is_summoning_sick


@dataclass
class PlayerSnapshot:
    life: int = 20
    mana_available: dict[str, int] = field(default_factory=dict)
    mana_total: dict[str, int] = field(default_factory=dict)
    hand: list[CardSnapshot] = field(default_factory=list)
    battlefield: list[CardSnapshot] = field(default_factory=list)
    graveyard: list[CardSnapshot] = field(default_factory=list)
    library_count: int = 60
    is_active: bool = False

    @property
    def total_mana_available(self) -> int:
        return sum(self.mana_available.values())

    @property
    def attackers(self) -> list[CardSnapshot]:
        return [card for card in self.battlefield if card.can_attack]


@dataclass
class GameSnapshot:
    phase: str = "UNKNOWN"
    turn_number: int = 0
    is_our_turn: bool = False
    has_priority: bool = False
    we: PlayerSnapshot = field(default_factory=PlayerSnapshot)
    opponent: PlayerSnapshot = field(default_factory=PlayerSnapshot)
    stack: list[str] = field(default_factory=list)
    pass_button_visible: bool = False
    ok_button_visible: bool = False
    keep_hand_button_visible: bool = False
    mulligan_button_visible: bool = False
    pass_button_pos: tuple[int, int] | None = None
    ok_button_pos: tuple[int, int] | None = None
    keep_hand_button_pos: tuple[int, int] | None = None
    mulligan_button_pos: tuple[int, int] | None = None
    available_action_types: list[str] = field(default_factory=list)
    opponent_player_pos: tuple[int, int] | None = None
    playable_hand_positions: list[tuple[int, int]] = field(default_factory=list)
    discard_prompt_visible: bool = False
    discard_submit_pos: tuple[int, int] | None = None
    arena_running: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GameStateManager:
    """Maintains an internal game model from Arena's log and screen state."""

    def __init__(self, config: dict, log_path: Path | None = None):
        arena_cfg = config.get("arena", {})
        vision_cfg = config.get("vision", {})

        ref_res = tuple(arena_cfg.get("reference_resolution", [2560, 1440]))
        grp_db = GrpDatabase()
        self.capture = ScreenCapture()
        self.detector = VisionDetector(
            reference_resolution=ref_res,
            threshold=vision_cfg.get("template_threshold", 0.80),
        )
        layout = CardPositionMapper.from_config(config)
        self.log_parser = ArenaLogParser(
            grp_db=grp_db,
            log_path=log_path or LOG_PATH,
            layout=layout,
        )
        self._state = GameState()
        self._snapshot = GameSnapshot()
        self._last_log_update = 0.0

    def refresh(self) -> GameSnapshot:
        """Poll the log, enrich the state with vision, and return a fresh snapshot."""
        updated = self.log_parser.poll()
        if updated is not None:
            self._state = updated
            self._last_log_update = time.time()
            LOGGER.debug("Log update applied to internal game state")

        if is_arena_running():
            self._apply_visual_state()
        else:
            self._clear_visual_state()

        self._snapshot = self._to_snapshot(self._state, arena_running=is_arena_running())
        return self._snapshot

    def get_snapshot(self) -> GameSnapshot:
        return self._snapshot

    def verify_expected_change(
        self,
        before: GameSnapshot,
        after: GameSnapshot,
        expected_state_change: dict[str, Any] | None,
    ) -> bool:
        if not expected_state_change:
            return True

        hand_delta = expected_state_change.get("hand_delta")
        if hand_delta is not None:
            actual_delta = len(after.we.hand) - len(before.we.hand)
            if actual_delta != hand_delta:
                return False

        opponent_life_delta_max = expected_state_change.get("opponent_life_delta_max")
        if opponent_life_delta_max is not None:
            delta = after.opponent.life - before.opponent.life
            if delta > opponent_life_delta_max:
                return False

        stack_contains = expected_state_change.get("stack_contains")
        if stack_contains and not any(stack_contains in item.lower() for item in after.stack):
            return False

        stack_absent = expected_state_change.get("stack_absent")
        if stack_absent and any(stack_absent in item.lower() for item in after.stack):
            return False

        if expected_state_change.get("phase_changed") and after.phase == before.phase:
            return False

        priority = expected_state_change.get("priority")
        if priority is not None and after.has_priority != priority:
            return False

        any_of = expected_state_change.get("any_of", [])
        if any_of:
            return any(self.verify_expected_change(before, after, option) for option in any_of)

        buttons_hidden = expected_state_change.get("buttons_hidden", [])
        for button in buttons_hidden:
            if getattr(after, f"{button}_visible", False):
                return False

        buttons_visible = expected_state_change.get("buttons_visible", [])
        for button in buttons_visible:
            if not getattr(after, f"{button}_visible", False):
                return False

        return True

    def _apply_visual_state(self) -> None:
        frame = self.capture.grab()
        buttons = self.detector.detect_buttons(frame)
        self._state.pass_button_visible, self._state.pass_button_pos = buttons["pass"]
        self._state.keep_hand_button_visible, self._state.keep_hand_button_pos = buttons["keep_hand"]
        self._state.mulligan_button_visible, self._state.mulligan_button_pos = buttons["mulligan"]
        self._state.playable_hand_positions = self.detector.detect_playable_hand_cards(frame)
        self._mark_playable_hand_cards()
        self._state.discard_prompt_visible, self._state.discard_submit_pos = (
            self.detector.detect_discard_state(frame)
        )

        ok_vis, ok_pos = buttons["ok"]
        if not ok_vis and self._state.has_priority and self._state.phase.name == "UNKNOWN":
            ok_vis = True
        self._state.ok_button_visible = ok_vis
        self._state.ok_button_pos = ok_pos

    def _clear_visual_state(self) -> None:
        self._state.pass_button_visible = False
        self._state.pass_button_pos = None
        self._state.ok_button_visible = False
        self._state.ok_button_pos = None
        self._state.keep_hand_button_visible = False
        self._state.keep_hand_button_pos = None
        self._state.mulligan_button_visible = False
        self._state.mulligan_button_pos = None
        self._state.playable_hand_positions = []
        for card in self._state.we.hand:
            card.is_playable = False
        self._state.discard_prompt_visible = False
        self._state.discard_submit_pos = None

    def _mark_playable_hand_cards(self) -> None:
        playable = self._state.playable_hand_positions
        for card in self._state.we.hand:
            if not playable or card.screen_x is None:
                card.is_playable = False
                continue
            card.is_playable = min(abs(px - card.screen_x) for px, _ in playable) < 100

    def _to_snapshot(self, state: GameState, arena_running: bool) -> GameSnapshot:
        return GameSnapshot(
            phase=state.phase.name,
            turn_number=state.turn_number,
            is_our_turn=state.is_our_turn,
            has_priority=state.has_priority,
            we=self._player_to_snapshot(state.we),
            opponent=self._player_to_snapshot(state.opponent),
            stack=list(state.stack),
            pass_button_visible=state.pass_button_visible,
            ok_button_visible=state.ok_button_visible,
            keep_hand_button_visible=state.keep_hand_button_visible,
            mulligan_button_visible=state.mulligan_button_visible,
            pass_button_pos=state.pass_button_pos,
            ok_button_pos=state.ok_button_pos,
            keep_hand_button_pos=state.keep_hand_button_pos,
            mulligan_button_pos=state.mulligan_button_pos,
            available_action_types=list(state.available_action_types),
            opponent_player_pos=state.opponent_player_pos,
            playable_hand_positions=list(state.playable_hand_positions),
            discard_prompt_visible=state.discard_prompt_visible,
            discard_submit_pos=state.discard_submit_pos,
            arena_running=arena_running,
            timestamp=time.time(),
        )

    def _player_to_snapshot(self, player: PlayerState) -> PlayerSnapshot:
        return PlayerSnapshot(
            life=player.life,
            mana_available=dict(player.mana_available),
            mana_total=dict(player.mana_total),
            hand=[self._card_to_snapshot(card) for card in player.hand],
            battlefield=[self._card_to_snapshot(card) for card in player.battlefield],
            graveyard=[self._card_to_snapshot(card) for card in player.graveyard],
            library_count=player.library_count,
            is_active=player.is_active,
        )

    @staticmethod
    def _card_to_snapshot(card: CardObject) -> CardSnapshot:
        return CardSnapshot(
            name=card.name,
            zone=card.zone.name,
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
            is_playable=False,
            screen_x=card.screen_x,
            screen_y=card.screen_y,
        )
