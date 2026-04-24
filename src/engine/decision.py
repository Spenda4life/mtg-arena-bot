from __future__ import annotations
from loguru import logger

from src.game_state.state import GameState, Phase, CardObject
from src.engine.actions import Action, ActionType


class DecisionEngine:
    """
    Rule-based decision engine for Standard BO1.

    Priority order each turn:
      Opening hand → keep 3+ land hands, mulligan otherwise
      Main1        → play land, cast highest-CMC spell that fits mana
      Combat       → attack with everything, block to trade up
      Main2        → cast remaining spells
      End step     → pass
    """

    def __init__(self, aggression: float = 0.7):
        self.aggression = aggression  # 0 = conservative, 1 = all-in
        self._land_played_this_turn = False
        self._last_phase: Phase = Phase.UNKNOWN

    def decide(self, state: GameState) -> Action | None:
        # Reset land-played tracker on new turn
        if state.phase == Phase.BEGINNING and self._last_phase == Phase.ENDING:
            self._land_played_this_turn = False
        self._last_phase = state.phase

        if not state.has_priority:
            return None

        # --- Opening hand decisions ---
        if state.keep_hand_button_visible:
            return self._decide_opening_hand(state)

        if state.ok_button_visible:
            return Action(ActionType.CLICK_OK,
                          state.ok_button_pos[0], state.ok_button_pos[1],
                          description="confirm ok")

        # --- Phase-gated decisions ---
        match state.phase:
            case Phase.MAIN1 | Phase.MAIN2:
                return self._decide_main_phase(state)
            case Phase.COMBAT_ATTACK:
                return self._decide_attack(state)
            case Phase.COMBAT_BLOCK:
                return self._decide_block(state)
            case _:
                return self._pass(state)

    # ------------------------------------------------------------------
    # Opening hand
    # ------------------------------------------------------------------

    def _decide_opening_hand(self, state: GameState) -> Action:
        hand = state.we.hand
        land_count = sum(1 for c in hand if c.is_land)
        hand_size = len(hand)

        # Keep if 2–5 lands in a 7-card hand; keep if 1–3 lands in smaller hands
        keep_min = max(1, hand_size - 5)
        keep_max = min(hand_size - 1, 5)
        should_keep = keep_min <= land_count <= keep_max

        if should_keep or hand_size <= 4:
            logger.info(f"Keeping hand ({land_count} lands, {hand_size} cards)")
            return Action(ActionType.KEEP_HAND,
                          state.keep_hand_button_pos[0], state.keep_hand_button_pos[1])
        else:
            logger.info(f"Mulligan ({land_count} lands, {hand_size} cards)")
            return Action(ActionType.MULLIGAN,
                          state.mulligan_button_pos[0], state.mulligan_button_pos[1])

    # ------------------------------------------------------------------
    # Main phase
    # ------------------------------------------------------------------

    def _decide_main_phase(self, state: GameState) -> Action:
        mana = state.we.total_mana_available
        hand = state.we.hand

        # 1. Play a land if we haven't this turn
        if not self._land_played_this_turn:
            land = next((c for c in hand if c.is_land and c.screen_x), None)
            if land:
                self._land_played_this_turn = True
                logger.info(f"Playing land at ({land.screen_x}, {land.screen_y})")
                return Action(ActionType.PLAY_LAND, land.screen_x, land.screen_y,
                              description=land.name)

        # 2. Cast the highest-CMC spell we can afford
        castable = [
            c for c in hand
            if not c.is_land and c.cmc <= mana and c.screen_x
        ]
        castable.sort(key=lambda c: c.cmc, reverse=True)
        if castable:
            spell = castable[0]
            logger.info(f"Casting {spell.name} (cmc={spell.cmc}) at ({spell.screen_x},{spell.screen_y})")
            return Action(ActionType.CAST_SPELL, spell.screen_x, spell.screen_y,
                          description=spell.name)

        return self._pass(state)

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def _decide_attack(self, state: GameState) -> Action:
        attackers = state.we.attackers
        if not attackers:
            return self._pass(state)

        # All-out aggression: click each untapped creature to declare as attacker
        for creature in attackers:
            if creature.screen_x:
                logger.info(f"Declaring attacker: {creature.name} at ({creature.screen_x},{creature.screen_y})")
                return Action(ActionType.DECLARE_ATTACKER,
                              creature.screen_x, creature.screen_y,
                              description=creature.name)

        # All attackers declared — confirm
        return Action(ActionType.CONFIRM_ATTACKERS, description="confirm attack")

    def _decide_block(self, state: GameState) -> Action:
        # Conservative blocking: block to trade favorably (our toughness >= their power)
        our_blockers = [
            c for c in state.we.battlefield
            if c.is_creature and not c.is_tapped and c.screen_x
        ]
        opp_attackers = [
            c for c in state.opponent.battlefield
            if c.is_creature and c.screen_x  # Arena marks attacking creatures
        ]

        if not opp_attackers or not our_blockers:
            return self._pass(state)

        # Simple heuristic: block biggest attacker with biggest blocker if we trade up
        opp_attackers.sort(key=lambda c: (c.power or 0), reverse=True)
        our_blockers.sort(key=lambda c: (c.power or 0), reverse=True)

        attacker = opp_attackers[0]
        for blocker in our_blockers:
            if (blocker.toughness or 0) >= (attacker.power or 0):
                logger.info(f"Blocking {attacker.name} with {blocker.name}")
                return Action(ActionType.DECLARE_BLOCKER,
                              blocker.screen_x, blocker.screen_y,
                              attacker.screen_x, attacker.screen_y,
                              description=f"{blocker.name} → {attacker.name}")

        # No favourable block — take the damage
        return self._pass(state)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pass(self, state: GameState) -> Action:
        if state.pass_button_pos:
            return Action(ActionType.PASS_PRIORITY,
                          state.pass_button_pos[0], state.pass_button_pos[1],
                          description=f"pass ({state.phase.name})")
        return Action(ActionType.PASS_PRIORITY, description="pass (no button found)")
