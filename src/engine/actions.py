from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class ActionType(Enum):
    PASS_PRIORITY = auto()
    CLICK_OK = auto()
    KEEP_HAND = auto()
    MULLIGAN = auto()
    CLICK_CARD = auto()       # generic: hover/select a card
    PLAY_LAND = auto()
    CAST_SPELL = auto()
    DECLARE_ATTACKER = auto()
    DECLARE_BLOCKER = auto()
    CONFIRM_ATTACKERS = auto()
    CONFIRM_BLOCKERS = auto()
    CLICK_END_STEP = auto()


@dataclass
class Action:
    type: ActionType
    target_x: Optional[int] = None
    target_y: Optional[int] = None
    # Optional second target (e.g. blocker → attacker)
    target2_x: Optional[int] = None
    target2_y: Optional[int] = None
    description: str = ""

    def __str__(self) -> str:
        loc = f"({self.target_x},{self.target_y})" if self.target_x is not None else ""
        return f"{self.type.name}{loc}" + (f" — {self.description}" if self.description else "")
