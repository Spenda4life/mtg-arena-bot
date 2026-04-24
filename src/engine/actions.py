from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class ActionType(Enum):
    # Keyboard actions (preferred — faster and more reliable than button hunting)
    KEY_SPACE = auto()        # OK / Yes / Pass priority / Next / Keep hand
    KEY_F6 = auto()           # Pass turn (yields all remaining priority this turn)
    KEY_F4 = auto()           # Pass to next phase (stop at next phase boundary)
    KEY_ESCAPE = auto()       # Cancel / close modal
    KEY_ENTER = auto()        # Confirm (alternative to space in some prompts)

    # Click actions — used when we need to interact with a specific card or target
    CLICK = auto()            # Generic click at (target_x, target_y)
    PLAY_LAND = auto()        # Click a land in hand
    CAST_SPELL = auto()       # Click a spell in hand (may need target afterward)
    DECLARE_ATTACKER = auto() # Click a creature to mark as attacker
    DECLARE_BLOCKER = auto()  # Click blocker then click attacker
    CLICK_TARGET = auto()     # Click a target (creature or player) after casting

    # Composite (controller expands these into sequences)
    KEEP_HAND = auto()        # Space (keep opening hand)
    MULLIGAN = auto()         # Click mulligan button (no keyboard shortcut)
    CONFIRM_ATTACKERS = auto()# Space after declaring all attackers
    CONFIRM_BLOCKERS = auto() # Space after declaring all blockers


# Actions that are pure keystrokes — no coordinates needed
KEYBOARD_ACTIONS = {
    ActionType.KEY_SPACE,
    ActionType.KEY_F6,
    ActionType.KEY_F4,
    ActionType.KEY_ESCAPE,
    ActionType.KEY_ENTER,
    ActionType.KEEP_HAND,
    ActionType.CONFIRM_ATTACKERS,
    ActionType.CONFIRM_BLOCKERS,
}


@dataclass
class Action:
    type: ActionType
    target_x: Optional[int] = None
    target_y: Optional[int] = None
    target2_x: Optional[int] = None   # second click (blocker → attacker)
    target2_y: Optional[int] = None
    description: str = ""

    def __str__(self) -> str:
        loc = f" ({self.target_x},{self.target_y})" if self.target_x is not None else ""
        desc = f" — {self.description}" if self.description else ""
        return f"{self.type.name}{loc}{desc}"
