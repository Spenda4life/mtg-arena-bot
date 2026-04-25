from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Phase(Enum):
    UNKNOWN = auto()
    BEGINNING = auto()   # untap / upkeep / draw
    MAIN1 = auto()
    COMBAT_BEGIN = auto()
    COMBAT_ATTACK = auto()
    COMBAT_BLOCK = auto()
    COMBAT_DAMAGE = auto()
    COMBAT_END = auto()
    MAIN2 = auto()
    ENDING = auto()      # end step / cleanup


class Zone(Enum):
    HAND = auto()
    BATTLEFIELD = auto()
    GRAVEYARD = auto()
    EXILE = auto()
    LIBRARY = auto()
    STACK = auto()


@dataclass
class CardObject:
    name: str
    zone: Zone
    cmc: int = 0
    power: Optional[int] = None
    toughness: Optional[int] = None
    is_tapped: bool = False
    is_summoning_sick: bool = False
    produces_mana: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    card_type: str = ""
    color: str = ""
    # Pixel coordinates of the card on screen (center)
    screen_x: Optional[int] = None
    screen_y: Optional[int] = None

    @property
    def is_land(self) -> bool:
        return "land" in self.card_type.lower()

    @property
    def is_creature(self) -> bool:
        return "creature" in self.card_type.lower()

    @property
    def can_attack(self) -> bool:
        return (
            self.is_creature
            and not self.is_tapped
            and not self.is_summoning_sick
        )

    @property
    def has_haste(self) -> bool:
        return "haste" in self.keywords


@dataclass
class PlayerState:
    life: int = 20
    mana_available: dict[str, int] = field(default_factory=dict)  # {"R": 2, "G": 1}
    mana_total: dict[str, int] = field(default_factory=dict)
    hand: list[CardObject] = field(default_factory=list)
    battlefield: list[CardObject] = field(default_factory=list)
    graveyard: list[CardObject] = field(default_factory=list)
    library_count: int = 60
    is_active: bool = False  # currently has priority

    @property
    def total_mana_available(self) -> int:
        return sum(self.mana_available.values())

    @property
    def untapped_lands(self) -> list[CardObject]:
        return [c for c in self.battlefield if c.is_land and not c.is_tapped]

    @property
    def attackers(self) -> list[CardObject]:
        return [c for c in self.battlefield if c.can_attack]


@dataclass
class GameState:
    phase: Phase = Phase.UNKNOWN
    turn_number: int = 0
    is_our_turn: bool = False
    has_priority: bool = False
    we: PlayerState = field(default_factory=PlayerState)
    opponent: PlayerState = field(default_factory=PlayerState)
    stack: list[str] = field(default_factory=list)
    # UI buttons currently visible on screen
    pass_button_visible: bool = False
    ok_button_visible: bool = False
    keep_hand_button_visible: bool = False
    mulligan_button_visible: bool = False
    # Coordinates of visible action buttons
    pass_button_pos: Optional[tuple[int, int]] = None
    ok_button_pos: Optional[tuple[int, int]] = None
    keep_hand_button_pos: Optional[tuple[int, int]] = None
    mulligan_button_pos: Optional[tuple[int, int]] = None
    # Action types Arena says are currently legal (from GSM actions array)
    available_action_types: list[str] = field(default_factory=list)
    # Pixel position of the opponent avatar/life total (for targeting face)
    opponent_player_pos: Optional[tuple[int, int]] = None
    # Centers of hand cards that have a blue "playable" outline, sorted left-to-right
    playable_hand_positions: list[tuple[int, int]] = field(default_factory=list)
    # Discard-to-hand-size prompt
    discard_prompt_visible: bool = False
    discard_submit_pos: Optional[tuple[int, int]] = None
