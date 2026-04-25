from __future__ import annotations
from loguru import logger

from src.game_state.state import GameState, Phase, CardObject
from src.game_state.match import MatchContext, MatchStatus
from src.engine.actions import Action, ActionType

# Spells that need a target immediately after being cast
_TARGETED_SPELLS = {
    "lightning strike", "shock", "play with fire", "strangle",
    "turn into a pumpkin", "destroy evil", "negate", "make disappear",
    "cut down", "go for the throat", "murder", "infernal grasp",
    "fateful absence", "borrowed time",
}

# Spells that hit "any target" (creature or player)
_ANY_TARGET_SPELLS = {
    "lightning strike", "shock", "play with fire", "strangle",
}

# Spells that only target creatures
_CREATURE_TARGET_SPELLS = {
    "cut down", "go for the throat", "murder", "infernal grasp",
    "destroy evil", "turn into a pumpkin",
}


class DecisionEngine:
    """
    Rule-based decision engine for Standard BO1.

    Keyboard-first: pass/ok/confirm all use spacebar or F-keys.
    Clicks are reserved for card selection and targeting.

    Priority each turn:
      Mulligan  → keep 2-5 land hands, mull otherwise
      Main1/2   → play land, then cast highest-CMC spell that fits mana
      Combat    → attack with all eligible creatures; block to trade up
      All other phases → pass with spacebar
    """

    def __init__(self, aggression: float = 0.7):
        self.aggression = aggression
        self._land_played_this_turn = False
        self._attackers_declared: set[int] = set()  # tracks which creatures clicked
        self._last_phase = Phase.UNKNOWN
        self._pending_target: str | None = None  # spell waiting for a target
        self._discard_selected: bool = False  # card was clicked for discard, Space next

    def decide(self, state: GameState, ctx: MatchContext | None = None) -> Action | None:
        # Reset per-turn trackers when a new turn begins
        if state.phase == Phase.BEGINNING and self._last_phase == Phase.ENDING:
            self._land_played_this_turn = False
            self._attackers_declared.clear()
        self._last_phase = state.phase

        # --- Mulligan: decided from visible buttons, not the priority system ---
        if state.keep_hand_button_visible or state.mulligan_button_visible:
            return self._decide_opening_hand(state)

        # --- Discard-to-hand-size: triggered by visual prompt, not hand count ---
        if state.discard_prompt_visible:
            return self._decide_discard(state)

        if not state.has_priority:
            return None

        # --- Targeting prompt: a spell was cast last tick and needs a target ---
        if self._pending_target:
            return self._resolve_target(state)

        # --- OK button present: something needs confirming ---
        if state.ok_button_visible:
            return Action(ActionType.KEY_SPACE, description="ok/confirm")

        match state.phase:
            case Phase.MAIN1 | Phase.MAIN2:
                return self._decide_main_phase(state)
            case Phase.COMBAT_ATTACK:
                return self._decide_attack(state)
            case Phase.COMBAT_BLOCK:
                return self._decide_block(state)
            case Phase.COMBAT_BEGIN | Phase.COMBAT_DAMAGE | Phase.COMBAT_END:
                # Let combat auto-resolve
                return Action(ActionType.KEY_SPACE, description=f"pass {state.phase.name}")
            case Phase.BEGINNING:
                return Action(ActionType.KEY_SPACE, description="pass beginning")
            case Phase.ENDING:
                return Action(ActionType.KEY_SPACE, description="pass turn")
            case _:
                return Action(ActionType.KEY_SPACE, description=f"pass {state.phase.name}")

    # ------------------------------------------------------------------
    # Discard to hand size
    # ------------------------------------------------------------------

    def _decide_discard(self, state: GameState) -> Action | None:
        if self._discard_selected:
            # Card was clicked last tick — now click the Submit button to confirm
            self._discard_selected = False
            sx, sy = state.discard_submit_pos or (None, None)
            if sx is None:
                return Action(ActionType.KEY_SPACE, description="confirm discard fallback")
            logger.info(f"Submitting discard at ({sx},{sy})")
            return Action(ActionType.CLICK, sx, sy, description="submit discard")

        # Use blue-outline positions to find actual card locations; fall back to
        # CardPositionMapper estimates if vision data is unavailable.
        playable = state.playable_hand_positions
        hand = list(state.we.hand)

        candidates = []
        for card in hand:
            if card.screen_x is None:
                continue
            if playable:
                dists = [(abs(px - card.screen_x), px, py) for px, py in playable]
                dist, px, py = min(dists)
                cx, cy = (px, py) if dist < 100 else (card.screen_x, card.screen_y)
            else:
                cx, cy = card.screen_x, card.screen_y
            candidates.append((card, cx, cy))

        if not candidates:
            return Action(ActionType.KEY_SPACE, description="discard: no positions yet")

        # Discard highest-CMC non-land; if all lands, discard highest-CMC land
        non_lands = [(c, cx, cy) for c, cx, cy in candidates if not c.is_land]
        pool = non_lands if non_lands else candidates
        worst, cx, cy = max(pool, key=lambda t: t[0].cmc)

        self._discard_selected = True
        logger.info(f"Selecting {worst.name} (cmc={worst.cmc}) for discard at ({cx},{cy})")
        return Action(ActionType.CLICK, cx, cy, description=f"discard select {worst.name}")

    # ------------------------------------------------------------------
    # Opening hand
    # ------------------------------------------------------------------

    def _decide_opening_hand(self, state: GameState) -> Action | None:
        hand = state.we.hand
        total = len(hand)

        # Log hasn't delivered the opening hand yet — wait another tick
        if total == 0:
            return None

        land_count = sum(1 for c in hand if c.is_land)

        # Keep range scales with hand size (tighter on mulligans)
        keep_min = max(1, total - 5)
        keep_max = min(total - 1, 5)
        should_keep = (keep_min <= land_count <= keep_max) or total <= 4

        if should_keep:
            logger.info(f"Keeping hand: {land_count} lands in {total} cards")
            return Action(ActionType.KEEP_HAND, description=f"{land_count}L/{total}")
        else:
            logger.info(f"Mulligan: {land_count} lands in {total} cards")
            return Action(ActionType.MULLIGAN,
                          state.mulligan_button_pos[0] if state.mulligan_button_pos else None,
                          state.mulligan_button_pos[1] if state.mulligan_button_pos else None,
                          description=f"{land_count}L/{total}")

    # ------------------------------------------------------------------
    # Main phase
    # ------------------------------------------------------------------

    def _decide_main_phase(self, state: GameState) -> Action:
        hand = state.we.hand
        playable = state.playable_hand_positions  # blue-outlined card centers, left-to-right

        def click_pos(card) -> tuple[int | None, int | None]:
            """Return the best click position for a card.

            Prefer the vision-detected blue-outline center (exact screen position).
            Fall back to the CardPositionMapper estimate if vision has no data.
            """
            if not playable or card.screen_x is None:
                return card.screen_x, card.screen_y
            dists = [(abs(px - card.screen_x), px, py) for px, py in playable]
            min_dist, px, py = min(dists)
            # Within 100px → treat the detected position as this card's center
            return (px, py) if min_dist < 100 else (card.screen_x, card.screen_y)

        def is_playable(card) -> bool:
            """True if Arena shows a blue outline on this card."""
            if not playable or card.screen_x is None:
                # No vision data — fall back to mana check
                return not card.is_land and card.cmc <= state.we.total_mana_available
            dists = [abs(px - card.screen_x) for px, _ in playable]
            return min(dists) < 100

        # 1. Play a land (once per turn, first blue-outlined land found)
        if not self._land_played_this_turn:
            land = next((c for c in hand if c.is_land and is_playable(c)), None)
            if land:
                self._land_played_this_turn = True
                cx, cy = click_pos(land)
                logger.info(f"Playing land: {land.name} at ({cx},{cy})")
                return Action(ActionType.PLAY_LAND, cx, cy, description=land.name)

        # 2. Cast highest-CMC blue-outlined spell
        castable = sorted(
            (c for c in hand if not c.is_land and is_playable(c)),
            key=lambda c: c.cmc, reverse=True,
        )
        if castable:
            spell = castable[0]
            name_lower = spell.name.lower()
            cx, cy = click_pos(spell)
            logger.info(f"Casting {spell.name} (cmc={spell.cmc}) at ({cx},{cy})")
            if name_lower in _TARGETED_SPELLS:
                self._pending_target = spell.name
                logger.info(f"  -> will need to target next")
            return Action(ActionType.CAST_SPELL, cx, cy, description=spell.name)

        # 3. Nothing to do — pass priority
        return Action(ActionType.KEY_SPACE, description="pass priority")

    # ------------------------------------------------------------------
    # Targeting
    # ------------------------------------------------------------------

    def _resolve_target(self, state: GameState) -> Action:
        spell_name = self._pending_target or ""
        name_lower = spell_name.lower()
        self._pending_target = None

        opp_creatures = [
            c for c in state.opponent.battlefield if c.is_creature and c.screen_x
        ]

        # Prefer targeting the opponent's biggest threat
        if name_lower in _CREATURE_TARGET_SPELLS and opp_creatures:
            target = max(opp_creatures, key=lambda c: (c.power or 0))
            logger.info(f"  Targeting {target.name} with {spell_name}")
            return Action(ActionType.CLICK_TARGET, target.screen_x, target.screen_y,
                          description=f"→ {target.name}")

        if name_lower in _ANY_TARGET_SPELLS:
            # Prefer killing a creature; if none, go face
            if opp_creatures:
                target = max(opp_creatures, key=lambda c: (c.power or 0))
                logger.info(f"  Targeting creature {target.name} with {spell_name}")
                return Action(ActionType.CLICK_TARGET, target.screen_x, target.screen_y,
                              description=f"→ {target.name}")
            else:
                # Target opponent player
                opp_x, opp_y = state.opponent_player_pos or (None, None)
                if opp_x:
                    logger.info(f"  Targeting opponent face with {spell_name}")
                    return Action(ActionType.CLICK_TARGET, opp_x, opp_y,
                                  description="→ opponent face")

        # Fallback: can't resolve target, escape the prompt
        logger.warning(f"Could not resolve target for {spell_name} — pressing Escape")
        return Action(ActionType.KEY_ESCAPE, description="escape untargetable spell")

    # ------------------------------------------------------------------
    # Combat — attack
    # ------------------------------------------------------------------

    def _decide_attack(self, state: GameState) -> Action:
        available = [
            c for c in state.we.attackers
            if id(c) not in self._attackers_declared and c.screen_x
        ]

        if available:
            creature = available[0]
            self._attackers_declared.add(id(creature))
            logger.info(f"Declaring attacker: {creature.name}")
            return Action(ActionType.DECLARE_ATTACKER,
                          creature.screen_x, creature.screen_y,
                          description=creature.name)

        # All attackers declared — confirm with space
        if self._attackers_declared:
            self._attackers_declared.clear()
            return Action(ActionType.CONFIRM_ATTACKERS, description="confirm attack")

        # No attackers at all
        return Action(ActionType.KEY_SPACE, description="no attackers, pass")

    # ------------------------------------------------------------------
    # Combat — block
    # ------------------------------------------------------------------

    def _decide_block(self, state: GameState) -> Action:
        our_blockers = [
            c for c in state.we.battlefield
            if c.is_creature and not c.is_tapped and c.screen_x
        ]
        opp_attackers = [
            c for c in state.opponent.battlefield
            if c.is_creature and c.screen_x
        ]

        if not opp_attackers:
            return Action(ActionType.KEY_SPACE, description="no attackers to block")

        # Find best trade: our blocker survives or kills theirs
        opp_attackers_sorted = sorted(opp_attackers, key=lambda c: (c.power or 0), reverse=True)
        used_blockers: set[int] = set()

        for attacker in opp_attackers_sorted:
            for blocker in our_blockers:
                if id(blocker) in used_blockers:
                    continue
                # Block if we kill theirs or survive (don't trade down)
                we_kill = (blocker.power or 0) >= (attacker.toughness or 1)
                we_survive = (blocker.toughness or 0) > (attacker.power or 0)
                if we_kill or we_survive:
                    used_blockers.add(id(blocker))
                    logger.info(f"Blocking {attacker.name} with {blocker.name}")
                    return Action(ActionType.DECLARE_BLOCKER,
                                  blocker.screen_x, blocker.screen_y,
                                  attacker.screen_x, attacker.screen_y,
                                  description=f"{blocker.name} → {attacker.name}")

        # No favorable blocks — take the damage
        return Action(ActionType.KEY_SPACE, description="no favorable blocks, take damage")
