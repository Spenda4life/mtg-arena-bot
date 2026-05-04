from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.capture.screen import is_arena_running
from src.game_state.grp_db import GrpDatabase
from src.game_state.log_parser import ArenaLogParser, LOG_PATH
from src.game_state.state import CardObject, GameState, PlayerState

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
    mulligan_pending: bool = False
    discard_required: bool = False
    we: PlayerSnapshot = field(default_factory=PlayerSnapshot)
    opponent: PlayerSnapshot = field(default_factory=PlayerSnapshot)
    stack: list[str] = field(default_factory=list)
    available_action_types: list[str] = field(default_factory=list)
    arena_running: bool = False
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GameStateManager:
    """Maintains a pure, log-derived game model for the decision engine."""

    def __init__(self, config: dict, log_path: Path | None = None):
        grp_db = GrpDatabase()
        self.log_parser = ArenaLogParser(
            grp_db=grp_db,
            log_path=log_path or LOG_PATH,
        )
        self._state = GameState()
        self._snapshot = GameSnapshot()
        self._last_log_update = 0.0

    def refresh(self) -> GameSnapshot:
        previous = self._snapshot
        updated = self.log_parser.poll()
        if updated is not None:
            self._state = updated
            self._last_log_update = time.time()
            LOGGER.debug("Log update applied to core game state")

        self._snapshot = self._to_snapshot(self._state, arena_running=is_arena_running())
        self._log_snapshot_changes(previous, self._snapshot)
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
        if hand_delta is not None and len(after.we.hand) - len(before.we.hand) != hand_delta:
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

        mulligan_pending = expected_state_change.get("mulligan_pending")
        if mulligan_pending is not None and after.mulligan_pending != mulligan_pending:
            return False

        discard_required = expected_state_change.get("discard_required")
        if discard_required is not None and after.discard_required != discard_required:
            return False

        any_of = expected_state_change.get("any_of", [])
        if any_of:
            return any(self.verify_expected_change(before, after, option) for option in any_of)

        return True

    def _to_snapshot(self, state: GameState, arena_running: bool) -> GameSnapshot:
        return GameSnapshot(
            phase=state.phase.name,
            turn_number=state.turn_number,
            is_our_turn=state.is_our_turn,
            has_priority=state.has_priority,
            mulligan_pending=self._is_mulligan_pending(state),
            discard_required=self._is_discard_required(state),
            we=self._player_to_snapshot(state.we),
            opponent=self._player_to_snapshot(state.opponent),
            stack=list(state.stack),
            available_action_types=list(state.available_action_types),
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
        )

    @staticmethod
    def _is_mulligan_pending(state: GameState) -> bool:
        return (
            state.phase.name == "UNKNOWN"
            and bool(state.we.hand)
            and not state.we.battlefield
            and not state.opponent.battlefield
            and state.turn_number == 0
        )

    @staticmethod
    def _is_discard_required(state: GameState) -> bool:
        return state.has_priority and len(state.we.hand) > 7 and state.phase.name == "ENDING"

    @staticmethod
    def _log_snapshot_changes(before: GameSnapshot, after: GameSnapshot) -> None:
        if before.phase != after.phase:
            LOGGER.info("Phase changed: %s -> %s", before.phase, after.phase)
        if before.has_priority != after.has_priority:
            LOGGER.debug("Priority changed: %s -> %s", before.has_priority, after.has_priority)
        if before.mulligan_pending != after.mulligan_pending:
            LOGGER.info("Mulligan pending changed: %s -> %s", before.mulligan_pending, after.mulligan_pending)
        if before.discard_required != after.discard_required:
            LOGGER.info("Discard required changed: %s -> %s", before.discard_required, after.discard_required)
        if len(before.we.hand) != len(after.we.hand):
            LOGGER.debug("Hand size changed: %s -> %s", len(before.we.hand), len(after.we.hand))
        if before.stack != after.stack:
            LOGGER.debug("Stack changed: %s -> %s", before.stack, after.stack)
