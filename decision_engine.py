from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from game_state import CardSnapshot, GameSnapshot

LOGGER = logging.getLogger(__name__)

_TARGETED_SPELLS = {
    "shock", "play with fire", "burst lightning", "lightning helix", "fire magic",
    "lightning strike", "strangle", "skullcrack", "wizard's lightning",
    "cut down", "go for the throat", "murder", "infernal grasp",
    "fateful absence", "borrowed time", "destroy evil", "turn into a pumpkin",
    "negate", "make disappear",
}

_ANY_TARGET_SPELLS = {
    "shock", "play with fire", "burst lightning", "lightning helix", "fire magic",
    "lightning strike", "strangle", "skullcrack", "wizard's lightning",
}

_CREATURE_TARGET_SPELLS = {
    "cut down", "go for the throat", "murder", "infernal grasp",
    "fateful absence", "borrowed time", "destroy evil", "turn into a pumpkin",
    "negate", "make disappear",
}

_SPELL_DAMAGE = {
    "shock": 2,
    "play with fire": 2,
    "burst lightning": 2,
    "lightning helix": 3,
    "fire magic": 1,
    "lightning strike": 3,
    "strangle": 3,
    "skullcrack": 3,
    "wizard's lightning": 3,
}


class ActionType(str, Enum):
    PASS_PRIORITY = "PASS_PRIORITY"
    KEEP_HAND = "KEEP_HAND"
    MULLIGAN = "MULLIGAN"
    PLAY_LAND = "PLAY_LAND"
    CAST_SPELL = "CAST_SPELL"
    SELECT_TARGET = "SELECT_TARGET"
    SELECT_DISCARD = "SELECT_DISCARD"
    CONFIRM_DISCARD = "CONFIRM_DISCARD"
    DECLARE_ATTACKER = "DECLARE_ATTACKER"
    CONFIRM_ATTACKERS = "CONFIRM_ATTACKERS"
    DECLARE_BLOCKER = "DECLARE_BLOCKER"
    CANCEL = "CANCEL"


@dataclass
class ActionPlan:
    action_type: ActionType
    subject: dict[str, object] | None = None
    target: dict[str, object] | None = None
    expected_state_change: dict[str, object] = field(default_factory=dict)
    description: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


class DecisionEngine:
    """Arena-agnostic decision layer using semantic game actions only."""

    def __init__(self, aggression: float = 0.7):
        self.aggression = aggression
        self._land_played_this_turn = False
        self._attackers_declared: set[int | str] = set()
        self._last_phase = "UNKNOWN"
        self._discard_selected = False
        self._pending_target_spell: str | None = None

    def decide(self, snapshot: GameSnapshot) -> ActionPlan | None:
        self._roll_turn_state(snapshot)

        if self._pending_target_spell:
            return self._resolve_target(snapshot, self._pending_target_spell)

        if snapshot.keep_hand_button_visible or snapshot.mulligan_button_visible:
            return self._decide_opening_hand(snapshot)

        if snapshot.discard_prompt_visible:
            return self._decide_discard(snapshot)

        if not snapshot.has_priority:
            return None

        if snapshot.ok_button_visible:
            return ActionPlan(
                action_type=ActionType.PASS_PRIORITY,
                description="ok/confirm",
                expected_state_change={
                    "any_of": [
                        {"priority": False},
                        {"phase_changed": True},
                        {"buttons_hidden": ["ok_button"]},
                    ]
                },
            )

        if snapshot.phase in {"MAIN1", "MAIN2"}:
            return self._decide_main_phase(snapshot)
        if snapshot.phase == "COMBAT_ATTACK":
            return self._decide_attack(snapshot)
        if snapshot.phase == "COMBAT_BLOCK":
            return self._decide_block(snapshot)

        return ActionPlan(
            action_type=ActionType.PASS_PRIORITY,
            description=f"pass {snapshot.phase.lower()}",
            expected_state_change={"any_of": [{"priority": False}, {"phase_changed": True}]},
        )

    def record_result(self, plan: ActionPlan, success: bool) -> None:
        if not success:
            if plan.action_type == ActionType.PLAY_LAND:
                self._land_played_this_turn = False
            if plan.action_type == ActionType.SELECT_DISCARD:
                self._discard_selected = False
            LOGGER.debug("Execution failed for %s; local state rolled back where needed", plan.description)
            return

        if plan.action_type == ActionType.CAST_SPELL and plan.metadata.get("needs_target"):
            self._pending_target_spell = str(plan.metadata["spell_name"])
        elif plan.action_type == ActionType.SELECT_TARGET:
            self._pending_target_spell = None
        elif plan.action_type == ActionType.CONFIRM_ATTACKERS:
            self._attackers_declared.clear()
        elif plan.action_type == ActionType.CONFIRM_DISCARD:
            self._discard_selected = False

    def _roll_turn_state(self, snapshot: GameSnapshot) -> None:
        if snapshot.phase == "BEGINNING" and self._last_phase == "ENDING":
            self._land_played_this_turn = False
            self._attackers_declared.clear()
        self._last_phase = snapshot.phase

    def _decide_opening_hand(self, snapshot: GameSnapshot) -> ActionPlan | None:
        total = len(snapshot.we.hand)
        if total == 0:
            return None

        land_count = sum(1 for card in snapshot.we.hand if card.is_land)
        keep_min = max(1, total - 5)
        keep_max = min(total - 1, 5)
        should_keep = (keep_min <= land_count <= keep_max) or total <= 4

        if should_keep:
            return ActionPlan(
                action_type=ActionType.KEEP_HAND,
                description=f"keep {land_count}L/{total}",
                expected_state_change={"buttons_hidden": ["keep_hand_button", "mulligan_button"]},
            )

        return ActionPlan(
            action_type=ActionType.MULLIGAN,
            subject={"kind": "button", "name": "mulligan"},
            description=f"mulligan {land_count}L/{total}",
            expected_state_change={"buttons_hidden": ["keep_hand_button", "mulligan_button"]},
        )

    def _decide_discard(self, snapshot: GameSnapshot) -> ActionPlan:
        if self._discard_selected:
            return ActionPlan(
                action_type=ActionType.CONFIRM_DISCARD,
                subject={"kind": "button", "name": "discard_submit"},
                description="submit discard",
                expected_state_change={"buttons_hidden": ["discard_prompt"]},
            )

        candidates = [card for card in snapshot.we.hand if self._has_card_identity(card)]
        if not candidates:
            return ActionPlan(
                action_type=ActionType.PASS_PRIORITY,
                description="discard fallback",
                expected_state_change={"any_of": [{"phase_changed": True}, {"priority": False}]},
            )

        non_lands = [card for card in candidates if not card.is_land]
        pool = non_lands if non_lands else candidates
        discard_card = max(pool, key=lambda card: card.cmc)
        self._discard_selected = True
        return ActionPlan(
            action_type=ActionType.SELECT_DISCARD,
            subject=self._card_ref(discard_card, zone="HAND"),
            description=f"discard select {discard_card.name}",
        )

    def _decide_main_phase(self, snapshot: GameSnapshot) -> ActionPlan:
        if not self._land_played_this_turn:
            land = next((card for card in snapshot.we.hand if card.is_land and self._is_playable(snapshot, card)), None)
            if land:
                self._land_played_this_turn = True
                return ActionPlan(
                    action_type=ActionType.PLAY_LAND,
                    subject=self._card_ref(land, zone="HAND"),
                    description=f"play land {land.name}",
                    expected_state_change={"hand_delta": -1},
                    metadata={"card_name": land.name},
                )

        non_targeted = sorted(
            [
                card for card in snapshot.we.hand
                if not card.is_land and card.name.lower() not in _TARGETED_SPELLS and self._is_playable(snapshot, card)
            ],
            key=lambda card: card.cmc,
            reverse=True,
        )
        if non_targeted:
            spell = non_targeted[0]
            return ActionPlan(
                action_type=ActionType.CAST_SPELL,
                subject=self._card_ref(spell, zone="HAND"),
                description=f"cast {spell.name}",
                expected_state_change={"hand_delta": -1},
                metadata={"spell_name": spell.name, "needs_target": False},
            )

        targeted = sorted(
            [
                card for card in snapshot.we.hand
                if not card.is_land and card.name.lower() in _TARGETED_SPELLS and self._is_playable(snapshot, card)
            ],
            key=lambda card: card.cmc,
            reverse=True,
        )
        if targeted:
            spell = targeted[0]
            return ActionPlan(
                action_type=ActionType.CAST_SPELL,
                subject=self._card_ref(spell, zone="HAND"),
                description=f"cast targeted {spell.name}",
                expected_state_change={"hand_delta": -1},
                metadata={"spell_name": spell.name, "needs_target": True},
            )

        return ActionPlan(
            action_type=ActionType.PASS_PRIORITY,
            description="pass priority",
            expected_state_change={"any_of": [{"priority": False}, {"phase_changed": True}]},
        )

    def _resolve_target(self, snapshot: GameSnapshot, spell_name: str) -> ActionPlan:
        name_lower = spell_name.lower()
        opp_creatures = [card for card in snapshot.opponent.battlefield if card.is_creature]

        if name_lower in _CREATURE_TARGET_SPELLS and opp_creatures:
            target = max(opp_creatures, key=lambda card: card.power or 0)
            return ActionPlan(
                action_type=ActionType.SELECT_TARGET,
                target=self._card_ref(target, zone="BATTLEFIELD", controller="opponent"),
                description=f"target {target.name}",
                expected_state_change={"stack_absent": name_lower},
                metadata={"spell_name": spell_name},
            )

        if name_lower in _ANY_TARGET_SPELLS:
            damage = _SPELL_DAMAGE.get(name_lower, 2)
            threatening = [
                card for card in opp_creatures
                if any(keyword in (card.keywords or []) for keyword in ("haste", "trample", "flying", "deathtouch"))
            ]
            if snapshot.opponent.life <= damage:
                return ActionPlan(
                    action_type=ActionType.SELECT_TARGET,
                    target={"kind": "player", "who": "opponent"},
                    description=f"lethal {spell_name} face",
                    expected_state_change={"stack_absent": name_lower},
                    metadata={"spell_name": spell_name},
                )
            if threatening:
                target = max(threatening, key=lambda card: card.power or 0)
                return ActionPlan(
                    action_type=ActionType.SELECT_TARGET,
                    target=self._card_ref(target, zone="BATTLEFIELD", controller="opponent"),
                    description=f"target threat {target.name}",
                    expected_state_change={"stack_absent": name_lower},
                    metadata={"spell_name": spell_name},
                )
            return ActionPlan(
                action_type=ActionType.SELECT_TARGET,
                target={"kind": "player", "who": "opponent"},
                description=f"burn face with {spell_name}",
                expected_state_change={"stack_absent": name_lower},
                metadata={"spell_name": spell_name},
            )

        self._pending_target_spell = None
        return ActionPlan(
            action_type=ActionType.CANCEL,
            description=f"cancel untargetable {spell_name}",
            expected_state_change={"stack_absent": name_lower},
        )

    def _decide_attack(self, snapshot: GameSnapshot) -> ActionPlan:
        available = [
            card for card in snapshot.we.attackers
            if self._card_key(card) not in self._attackers_declared and self._has_card_identity(card)
        ]
        if available:
            creature = available[0]
            self._attackers_declared.add(self._card_key(creature))
            return ActionPlan(
                action_type=ActionType.DECLARE_ATTACKER,
                subject=self._card_ref(creature, zone="BATTLEFIELD"),
                description=f"attack with {creature.name}",
            )

        if self._attackers_declared:
            return ActionPlan(
                action_type=ActionType.CONFIRM_ATTACKERS,
                description="confirm attackers",
                expected_state_change={"any_of": [{"phase_changed": True}, {"priority": False}]},
            )

        return ActionPlan(
            action_type=ActionType.PASS_PRIORITY,
            description="no attackers",
            expected_state_change={"any_of": [{"phase_changed": True}, {"priority": False}]},
        )

    def _decide_block(self, snapshot: GameSnapshot) -> ActionPlan:
        our_blockers = [card for card in snapshot.we.battlefield if card.is_creature and not card.is_tapped]
        opp_attackers = [card for card in snapshot.opponent.battlefield if card.is_creature]
        if not opp_attackers:
            return ActionPlan(
                action_type=ActionType.PASS_PRIORITY,
                description="no attackers to block",
                expected_state_change={"any_of": [{"phase_changed": True}, {"priority": False}]},
            )

        used: set[int | str] = set()
        for attacker in sorted(opp_attackers, key=lambda card: card.power or 0, reverse=True):
            for blocker in our_blockers:
                blocker_key = self._card_key(blocker)
                if blocker_key in used:
                    continue
                we_kill = (blocker.power or 0) >= (attacker.toughness or 1)
                we_survive = (blocker.toughness or 0) > (attacker.power or 0)
                if we_kill or we_survive:
                    used.add(blocker_key)
                    return ActionPlan(
                        action_type=ActionType.DECLARE_BLOCKER,
                        subject=self._card_ref(blocker, zone="BATTLEFIELD"),
                        target=self._card_ref(attacker, zone="BATTLEFIELD", controller="opponent"),
                        description=f"block {attacker.name} with {blocker.name}",
                    )

        return ActionPlan(
            action_type=ActionType.PASS_PRIORITY,
            description="take damage",
            expected_state_change={"any_of": [{"phase_changed": True}, {"priority": False}]},
        )

    def _is_playable(self, snapshot: GameSnapshot, card: CardSnapshot) -> bool:
        if card.is_playable:
            return True
        if card.is_land:
            return True
        return card.cmc <= snapshot.we.total_mana_available

    @staticmethod
    def _card_ref(
        card: CardSnapshot,
        *,
        zone: str,
        controller: str = "self",
    ) -> dict[str, object]:
        return {
            "kind": "card",
            "instance_id": card.instance_id,
            "name": card.name,
            "zone": zone,
            "controller": controller,
        }

    @staticmethod
    def _card_key(card: CardSnapshot) -> int | str:
        if card.instance_id is not None:
            return card.instance_id
        return f"{card.name}:{card.zone}"

    @staticmethod
    def _has_card_identity(card: CardSnapshot) -> bool:
        return card.instance_id is not None or bool(card.name)
