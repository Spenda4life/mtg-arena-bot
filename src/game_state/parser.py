from __future__ import annotations
import numpy as np
import yaml
from pathlib import Path
from loguru import logger

from src.vision.detector import VisionDetector
from src.game_state.state import GameState, PlayerState, CardObject, Phase, Zone

PHASE_MAP = {
    "BEGINNING":     Phase.BEGINNING,
    "MAIN1":         Phase.MAIN1,
    "COMBAT_BEGIN":  Phase.COMBAT_BEGIN,
    "COMBAT_ATTACK": Phase.COMBAT_ATTACK,
    "COMBAT_BLOCK":  Phase.COMBAT_BLOCK,
    "COMBAT_DAMAGE": Phase.COMBAT_DAMAGE,
    "MAIN2":         Phase.MAIN2,
    "ENDING":        Phase.ENDING,
    "UNKNOWN":       Phase.UNKNOWN,
}


class GameStateParser:
    """
    Translates raw vision detections into a structured GameState.

    The parser intentionally keeps vision and game logic separated:
    the detector sees pixels, the parser interprets them as game objects.
    """

    def __init__(self, deck_path: str, detector: VisionDetector):
        self.detector = detector
        self._deck = self._load_deck(deck_path)
        # Build a fast lookup from card name → card metadata
        self._deck_index: dict[str, dict] = {
            c["name"].lower(): c for c in self._deck.get("mainboard", [])
        }

    def _load_deck(self, path: str) -> dict:
        p = Path(path)
        if not p.exists():
            logger.warning(f"Deck file not found: {path}")
            return {}
        with open(p) as f:
            return yaml.safe_load(f)

    def _make_card(self, name: str, zone: Zone, screen_pos: tuple[int, int] | None = None) -> CardObject:
        meta = self._deck_index.get(name.lower(), {})
        c = CardObject(
            name=name,
            zone=zone,
            cmc=meta.get("cmc", 0),
            card_type=meta.get("type", ""),
            color=meta.get("color", ""),
            keywords=meta.get("keywords", []),
        )
        if meta.get("type", "") == "land":
            c.produces_mana = meta.get("produces", [])
        if screen_pos:
            c.screen_x, c.screen_y = screen_pos
        return c

    def parse(self, frame: np.ndarray, prev_state: GameState | None = None) -> GameState:
        state = GameState()

        # --- Life totals ---
        our_life, opp_life = self.detector.detect_life(frame)
        state.we.life = our_life if our_life is not None else (prev_state.we.life if prev_state else 20)
        state.opponent.life = opp_life if opp_life is not None else (prev_state.opponent.life if prev_state else 20)

        # --- Phase ---
        phase_str = self.detector.detect_phase(frame)
        state.phase = PHASE_MAP.get(phase_str, Phase.UNKNOWN)

        # --- Mana ---
        state.we.mana_available = self.detector.detect_mana(frame)

        # --- Battlefield card positions ---
        our_positions = self.detector.detect_battlefield_cards(frame, "our_battlefield")
        opp_positions = self.detector.detect_battlefield_cards(frame, "opp_battlefield")

        # Map positions to known deck cards (heuristic: assume our cards are our deck cards)
        state.we.battlefield = [
            self._make_card("unknown", Zone.BATTLEFIELD, pos) for pos in our_positions
        ]
        state.opponent.battlefield = [
            self._make_card("unknown", Zone.BATTLEFIELD, pos) for pos in opp_positions
        ]

        # --- Hand (count only; we can't reliably read card names from hand without hovering) ---
        hand_count = self.detector.detect_hand_count(frame)
        state.we.hand = [self._make_card("unknown", Zone.HAND) for _ in range(hand_count)]

        # --- Buttons ---
        buttons = self.detector.detect_buttons(frame)
        state.pass_button_visible, state.pass_button_pos = buttons["pass"]
        state.ok_button_visible, state.ok_button_pos = buttons["ok"]
        state.keep_hand_button_visible, state.keep_hand_button_pos = buttons["keep_hand"]
        state.mulligan_button_visible, state.mulligan_button_pos = buttons["mulligan"]

        # Priority: if the pass button is visible, we have priority
        state.has_priority = state.pass_button_visible or state.ok_button_visible

        logger.debug(
            f"Parsed state | phase={state.phase.name} life={state.we.life}/{state.opponent.life} "
            f"mana={state.we.mana_available} bf={len(state.we.battlefield)} hand={len(state.we.hand)}"
        )
        return state
