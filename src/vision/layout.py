from __future__ import annotations
from dataclasses import dataclass
from loguru import logger


@dataclass
class LayoutConfig:
    """
    Fractional screen positions for Arena UI zones.
    All values are fractions of screen width (x) or height (y), so they
    scale automatically with resolution.

    Calibrated against 2560x1440 and 1920x1080 — the fractions are identical.
    Tweak via config/settings.yaml > layout if your setup differs.
    """
    # Hand
    hand_y: float = 0.905           # vertical center of hand cards
    hand_x_min: float = 0.175       # leftmost card center at 7 cards
    hand_x_max: float = 0.825       # rightmost card center at 7 cards

    # Our battlefield — non-land creatures row
    our_creatures_y: float = 0.615
    our_creatures_x_min: float = 0.12
    our_creatures_x_max: float = 0.88

    # Our battlefield — lands row (below creatures)
    our_lands_y: float = 0.725
    our_lands_x_min: float = 0.12
    our_lands_x_max: float = 0.88

    # Opponent battlefield — non-land creatures row
    opp_creatures_y: float = 0.385
    opp_creatures_x_min: float = 0.12
    opp_creatures_x_max: float = 0.88

    # Opponent battlefield — lands row (above creatures)
    opp_lands_y: float = 0.275
    opp_lands_x_min: float = 0.12
    opp_lands_x_max: float = 0.88

    # Opponent player (for targeting life total / player)
    opp_player_x: float = 0.5
    opp_player_y: float = 0.06

    # Our player
    our_player_x: float = 0.5
    our_player_y: float = 0.94


def _spread(index: int, total: int, x_min: float, x_max: float) -> float:
    """Evenly distribute `total` items across [x_min, x_max], return position of item `index`."""
    if total <= 1:
        return (x_min + x_max) / 2
    return x_min + index * (x_max - x_min) / (total - 1)


class CardPositionMapper:
    """
    Maps logical card positions (hand index, battlefield slot) to pixel coordinates.

    Arena keeps hand cards evenly fanned at a fixed Y, and battlefield cards
    in two rows (creatures, lands) also evenly spread. The exact pixel location
    of card N in a hand of M cards is pure geometry from the layout config.
    """

    def __init__(self, screen_w: int, screen_h: int, cfg: LayoutConfig | None = None):
        self.w = screen_w
        self.h = screen_h
        self.cfg = cfg or LayoutConfig()

    def hand_position(self, index: int, total: int) -> tuple[int, int]:
        """Pixel center of the `index`-th card in a hand of `total` cards."""
        # As hand shrinks below 7, cards spread wider but stay within bounds.
        # Arena keeps them centered, so we mirror that by clamping spread.
        effective_max = min(self.cfg.hand_x_max, 0.5 + (total / 7) * 0.325)
        effective_min = max(self.cfg.hand_x_min, 0.5 - (total / 7) * 0.325)
        fx = _spread(index, total, effective_min, effective_max)
        fy = self.cfg.hand_y
        return int(fx * self.w), int(fy * self.h)

    def our_creature_position(self, index: int, total: int) -> tuple[int, int]:
        fx = _spread(index, total, self.cfg.our_creatures_x_min, self.cfg.our_creatures_x_max)
        return int(fx * self.w), int(self.cfg.our_creatures_y * self.h)

    def our_land_position(self, index: int, total: int) -> tuple[int, int]:
        fx = _spread(index, total, self.cfg.our_lands_x_min, self.cfg.our_lands_x_max)
        return int(fx * self.w), int(self.cfg.our_lands_y * self.h)

    def opp_creature_position(self, index: int, total: int) -> tuple[int, int]:
        fx = _spread(index, total, self.cfg.opp_creatures_x_min, self.cfg.opp_creatures_x_max)
        return int(fx * self.w), int(self.cfg.opp_creatures_y * self.h)

    def opp_land_position(self, index: int, total: int) -> tuple[int, int]:
        fx = _spread(index, total, self.cfg.opp_lands_x_min, self.cfg.opp_lands_x_max)
        return int(fx * self.w), int(self.cfg.opp_lands_y * self.h)

    def opp_player_position(self) -> tuple[int, int]:
        return int(self.cfg.opp_player_x * self.w), int(self.cfg.opp_player_y * self.h)

    def assign_hand_positions(self, hand: list) -> None:
        """Write screen_x/screen_y into each CardObject in the hand list in-place."""
        non_lands = [c for c in hand if not c.is_land]
        lands = [c for c in hand if c.is_land]
        total = len(hand)
        for i, card in enumerate(hand):
            card.screen_x, card.screen_y = self.hand_position(i, total)

    def assign_battlefield_positions(self, battlefield: list, is_ours: bool) -> None:
        """Write screen_x/screen_y into each battlefield CardObject in-place."""
        creatures = [c for c in battlefield if c.is_creature]
        lands = [c for c in battlefield if c.is_land]
        others = [c for c in battlefield if not c.is_creature and not c.is_land]

        # Treat non-creature non-land permanents (enchantments, planeswalkers) as creatures
        # for positioning — they sit in the same row
        front_row = creatures + others

        if is_ours:
            for i, card in enumerate(front_row):
                card.screen_x, card.screen_y = self.our_creature_position(i, len(front_row))
            for i, card in enumerate(lands):
                card.screen_x, card.screen_y = self.our_land_position(i, len(lands))
        else:
            for i, card in enumerate(front_row):
                card.screen_x, card.screen_y = self.opp_creature_position(i, len(front_row))
            for i, card in enumerate(lands):
                card.screen_x, card.screen_y = self.opp_land_position(i, len(lands))

    @classmethod
    def from_config(cls, config: dict) -> "CardPositionMapper":
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[1]
            w, h = mon["width"], mon["height"]

        layout_cfg = config.get("layout", {})
        lc = LayoutConfig(**{k: v for k, v in layout_cfg.items()
                             if hasattr(LayoutConfig, k)})
        logger.info(f"Layout mapper: {w}x{h} screen")
        return cls(w, h, lc)
