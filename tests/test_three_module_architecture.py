from __future__ import annotations

from decision_engine import ActionType, DecisionEngine
from game_state import CardSnapshot, GameSnapshot, GameStateManager, PlayerSnapshot


def _card(name: str, card_type: str, cmc: int = 0, x: int | None = None, y: int | None = None) -> CardSnapshot:
    return CardSnapshot(
        name=name,
        zone="HAND",
        card_type=card_type,
        cmc=cmc,
        screen_x=x,
        screen_y=y,
    )


def test_snapshot_is_serializable():
    snapshot = GameSnapshot(
        phase="MAIN1",
        we=PlayerSnapshot(hand=[_card("Mountain", "land", x=100, y=100)]),
    )
    data = snapshot.to_dict()
    assert data["phase"] == "MAIN1"
    assert data["we"]["hand"][0]["name"] == "Mountain"


def test_decision_engine_plays_land_first():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        playable_hand_positions=[(100, 100)],
        we=PlayerSnapshot(
            hand=[
                _card("Mountain", "land", x=100, y=100),
                _card("Shock", "instant", cmc=1, x=200, y=100),
            ],
            mana_available={"R": 1},
        ),
    )

    plan = engine.decide(snapshot)
    assert plan is not None
    assert plan.action_type == ActionType.PLAY_LAND
    assert plan.subject == {
        "kind": "card",
        "instance_id": None,
        "name": "Mountain",
        "zone": "HAND",
        "controller": "self",
    }
    assert plan.expected_state_change["hand_delta"] == -1


def test_decision_engine_creates_target_plan_after_successful_targeted_cast():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        playable_hand_positions=[(100, 100)],
        we=PlayerSnapshot(
            hand=[_card("Lightning Strike", "instant", cmc=2, x=100, y=100)],
            mana_available={"R": 2},
        ),
        opponent=PlayerSnapshot(
            battlefield=[
                CardSnapshot(
                    name="Enemy Creature",
                    zone="BATTLEFIELD",
                    card_type="creature",
                    power=3,
                    toughness=3,
                    screen_x=400,
                    screen_y=400,
                )
            ]
        ),
        stack=["lightning strike"],
        opponent_player_pos=(800, 200),
    )

    cast_plan = engine.decide(snapshot)
    assert cast_plan is not None
    assert cast_plan.action_type == ActionType.CAST_SPELL
    engine.record_result(cast_plan, True)

    target_snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        opponent=snapshot.opponent,
        stack=["lightning strike"],
        opponent_player_pos=(800, 200),
    )
    target_plan = engine.decide(target_snapshot)
    assert target_plan is not None
    assert target_plan.action_type == ActionType.SELECT_TARGET
    assert target_plan.target == {"kind": "player", "who": "opponent"}


def test_decision_engine_uses_generic_playability_not_coordinates():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        we=PlayerSnapshot(
            hand=[_card("Shock", "instant", cmc=1)],
            mana_available={},
        ),
    )
    snapshot.we.hand[0].is_playable = True

    plan = engine.decide(snapshot)
    assert plan is not None
    assert plan.action_type == ActionType.CAST_SPELL
    assert "coordinates" not in plan.__dict__


def test_state_manager_verifies_hand_delta():
    manager = GameStateManager.__new__(GameStateManager)
    before = GameSnapshot(we=PlayerSnapshot(hand=[_card("A", "land"), _card("B", "land")]))
    after = GameSnapshot(we=PlayerSnapshot(hand=[_card("A", "land")]))
    assert manager.verify_expected_change(before, after, {"hand_delta": -1}) is True
    assert manager.verify_expected_change(before, after, {"hand_delta": -2}) is False
