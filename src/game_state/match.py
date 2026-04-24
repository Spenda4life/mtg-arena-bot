from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass, field
from loguru import logger

from src.game_state.state import GameState, Phase


class MatchStatus(Enum):
    IDLE = auto()           # Not in a game
    SEARCHING = auto()      # Queuing / waiting for opponent
    MULLIGANING = auto()    # Opening hand decisions
    PLAYING = auto()        # Active game
    GAME_OVER = auto()      # Game ended, result pending
    BETWEEN_GAMES = auto()  # BO3 between games (unused in BO1 but reserved)


@dataclass
class MatchContext:
    status: MatchStatus = MatchStatus.IDLE
    game_number: int = 1
    wins: int = 0
    losses: int = 0
    mulligans_taken: int = 0
    on_the_play: bool | None = None   # True if we go first
    opponent_name: str = ""
    history: list[str] = field(default_factory=list)  # event log for debugging

    def record(self, event: str) -> None:
        logger.info(f"[Match] {event}")
        self.history.append(event)


class MatchStateMachine:
    """
    Tracks the high-level match lifecycle so the decision engine
    knows which mode it's operating in.

    Transitions are driven by GameState observations rather than
    explicit server events, making this robust to missed messages.
    """

    def __init__(self):
        self.ctx = MatchContext()
        self._prev_has_priority = False
        self._prev_phase = Phase.UNKNOWN
        self._prev_life = 20

    def update(self, state: GameState) -> MatchContext:
        match self.ctx.status:
            case MatchStatus.IDLE | MatchStatus.SEARCHING:
                self._check_game_start(state)
            case MatchStatus.MULLIGANING:
                self._check_mulligan_done(state)
            case MatchStatus.PLAYING:
                self._check_game_over(state)
            case MatchStatus.GAME_OVER:
                self._check_next_game(state)

        self._prev_has_priority = state.has_priority
        self._prev_phase = state.phase
        self._prev_life = state.we.life
        return self.ctx

    def _check_game_start(self, state: GameState) -> None:
        # A game has started when we have cards and the mulligan button is visible
        # or the phase is no longer UNKNOWN
        in_game = (
            state.keep_hand_button_visible
            or state.mulligan_button_visible
            or state.phase != Phase.UNKNOWN
        )
        if in_game:
            self.ctx.status = MatchStatus.MULLIGANING
            self.ctx.mulligans_taken = 0
            self.ctx.record(f"Game {self.ctx.game_number} started — mulliganing")

    def _check_mulligan_done(self, state: GameState) -> None:
        # Mulligan phase is over once neither button is visible and we're in a real phase
        if (
            not state.keep_hand_button_visible
            and not state.mulligan_button_visible
            and state.phase not in (Phase.UNKNOWN, Phase.BEGINNING)
        ):
            going_first = state.is_our_turn and state.turn_number == 1
            self.ctx.on_the_play = going_first
            self.ctx.status = MatchStatus.PLAYING
            self.ctx.record(
                f"Playing ({'on the play' if going_first else 'on the draw'}), "
                f"{self.ctx.mulligans_taken} mulligan(s)"
            )

    def _check_game_over(self, state: GameState) -> None:
        # Heuristic: life hits 0, or phase goes UNKNOWN after being active
        we_died = state.we.life <= 0
        they_died = state.opponent.life <= 0
        phase_reset = (
            self._prev_phase != Phase.UNKNOWN
            and state.phase == Phase.UNKNOWN
            and not state.has_priority
        )

        if we_died or they_died or phase_reset:
            result = "WIN" if they_died else "LOSS"
            self.ctx.status = MatchStatus.GAME_OVER
            if they_died:
                self.ctx.wins += 1
            elif we_died:
                self.ctx.losses += 1
            self.ctx.record(f"Game {self.ctx.game_number} ended — {result}")

    def _check_next_game(self, state: GameState) -> None:
        # New game detected when phase becomes non-UNKNOWN again
        if state.phase != Phase.UNKNOWN or state.keep_hand_button_visible:
            self.ctx.game_number += 1
            self.ctx.status = MatchStatus.MULLIGANING
            self.ctx.mulligans_taken = 0
            self.ctx.record(f"Game {self.ctx.game_number} starting")

    def record_mulligan(self) -> None:
        self.ctx.mulligans_taken += 1
        self.ctx.record(f"Mulligan #{self.ctx.mulligans_taken}")
