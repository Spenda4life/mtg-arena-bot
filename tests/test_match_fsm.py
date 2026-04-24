import pytest
from src.game_state.match import MatchStateMachine, MatchStatus
from src.game_state.state import GameState, Phase


def _state(**kwargs) -> GameState:
    s = GameState()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_starts_idle():
    fsm = MatchStateMachine()
    assert fsm.ctx.status == MatchStatus.IDLE


def test_transitions_to_mulliganing_on_keep_button():
    fsm = MatchStateMachine()
    state = _state(keep_hand_button_visible=True)
    fsm.update(state)
    assert fsm.ctx.status == MatchStatus.MULLIGANING


def test_transitions_to_playing_after_mulligan_phase():
    fsm = MatchStateMachine()
    # Fast-forward to mulliganing
    fsm.ctx.status = MatchStatus.MULLIGANING
    state = _state(
        keep_hand_button_visible=False,
        mulligan_button_visible=False,
        phase=Phase.MAIN1,
        is_our_turn=True,
        turn_number=1,
    )
    fsm.update(state)
    assert fsm.ctx.status == MatchStatus.PLAYING


def test_detects_game_over_on_opponent_death():
    fsm = MatchStateMachine()
    fsm.ctx.status = MatchStatus.PLAYING
    fsm._prev_phase = Phase.MAIN1
    state = _state(phase=Phase.MAIN1)
    state.opponent.life = 0
    fsm.update(state)
    assert fsm.ctx.status == MatchStatus.GAME_OVER
    assert fsm.ctx.wins == 1
    assert fsm.ctx.losses == 0


def test_detects_game_over_on_our_death():
    fsm = MatchStateMachine()
    fsm.ctx.status = MatchStatus.PLAYING
    fsm._prev_phase = Phase.MAIN1
    state = _state(phase=Phase.MAIN1)
    state.we.life = 0
    fsm.update(state)
    assert fsm.ctx.status == MatchStatus.GAME_OVER
    assert fsm.ctx.losses == 1


def test_mulligan_counter():
    fsm = MatchStateMachine()
    fsm.ctx.status = MatchStatus.MULLIGANING
    fsm.record_mulligan()
    fsm.record_mulligan()
    assert fsm.ctx.mulligans_taken == 2
