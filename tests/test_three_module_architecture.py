from __future__ import annotations

from clicker_agent import ExecutionContext, ExecutionHandler
from decision_engine import ActionPlan, ActionType, DecisionEngine
from game_state import CardSnapshot, GameSnapshot, GameStateManager, PlayerSnapshot


def _card(name: str, card_type: str, cmc: int = 0, instance_id: int | None = None) -> CardSnapshot:
    return CardSnapshot(
        name=name,
        zone="HAND",
        card_type=card_type,
        cmc=cmc,
        instance_id=instance_id,
    )


def test_snapshot_is_serializable():
    snapshot = GameSnapshot(
        phase="MAIN1",
        we=PlayerSnapshot(hand=[_card("Mountain", "land", instance_id=1)]),
    )
    data = snapshot.to_dict()
    assert data["phase"] == "MAIN1"
    assert data["we"]["hand"][0]["name"] == "Mountain"
    assert "mulligan_pending" in data


def test_decision_engine_plays_land_first_from_core_state_only():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        we=PlayerSnapshot(
            hand=[
                _card("Mountain", "land", instance_id=10),
                _card("Shock", "instant", cmc=1, instance_id=11),
            ],
            mana_available={"R": 1},
        ),
    )

    plan = engine.decide(snapshot)
    assert plan is not None
    assert plan.action_type == ActionType.PLAY_LAND
    assert plan.subject == {
        "kind": "card",
        "instance_id": 10,
        "name": "Mountain",
        "zone": "HAND",
        "controller": "self",
    }
    assert plan.expected_state_change["hand_delta"] == -1


def test_decision_engine_uses_generic_mulligan_flag():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="UNKNOWN",
        mulligan_pending=True,
        we=PlayerSnapshot(hand=[_card("Mountain", "land"), _card("Shock", "instant", cmc=1)]),
    )

    plan = engine.decide(snapshot)
    assert plan is not None
    assert plan.action_type == ActionType.KEEP_HAND


def test_decision_engine_creates_target_plan_after_successful_targeted_cast():
    engine = DecisionEngine()
    snapshot = GameSnapshot(
        phase="MAIN1",
        has_priority=True,
        we=PlayerSnapshot(
            hand=[_card("Lightning Strike", "instant", cmc=2, instance_id=100)],
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
                    instance_id=200,
                )
            ]
        ),
        stack=["lightning strike"],
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
    )
    target_plan = engine.decide(target_snapshot)
    assert target_plan is not None
    assert target_plan.action_type == ActionType.SELECT_TARGET
    assert target_plan.target == {"kind": "player", "who": "opponent"}


def test_state_manager_verifies_hand_delta():
    manager = GameStateManager.__new__(GameStateManager)
    before = GameSnapshot(we=PlayerSnapshot(hand=[_card("A", "land"), _card("B", "land")]))
    after = GameSnapshot(we=PlayerSnapshot(hand=[_card("A", "land")]))
    assert manager.verify_expected_change(before, after, {"hand_delta": -1}) is True
    assert manager.verify_expected_change(before, after, {"hand_delta": -2}) is False


def test_clicker_resolves_semantic_hand_card_to_coordinates():
    handler = ExecutionHandler.__new__(ExecutionHandler)

    class _Layout:
        @staticmethod
        def hand_position(index: int, total: int) -> tuple[int, int]:
            return 100 + index * 50, 700 + total

    handler.layout = _Layout()
    state = GameSnapshot(
        we=PlayerSnapshot(
            hand=[_card("Shock", "instant", instance_id=7), _card("Mountain", "land", instance_id=8)]
        )
    )
    context = ExecutionContext(playable_hand_positions=[])
    coords = handler._resolve_card(
        {"kind": "card", "instance_id": 8, "name": "Mountain", "zone": "HAND", "controller": "self"},
        state,
        context,
    )
    assert coords == (150, 702)


def test_clicker_resolves_opponent_face_from_execution_context():
    handler = ExecutionHandler.__new__(ExecutionHandler)
    coords = handler._resolve_ref(
        {"kind": "player", "who": "opponent"},
        GameSnapshot(),
        ExecutionContext(opponent_player_pos=(900, 120)),
    )
    assert coords == (900, 120)


def test_action_plan_has_no_coordinates_field():
    plan = ActionPlan(action_type=ActionType.PASS_PRIORITY, description="pass")
    assert not hasattr(plan, "coordinates")
