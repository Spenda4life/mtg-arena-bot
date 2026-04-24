from __future__ import annotations
import json
import glob
from pathlib import Path
from loguru import logger

# Arena stores its card data in a versioned file under its installation directory.
# We search for the most recently modified one.
_ARENA_DATA_DIRS = [
    Path("C:/Program Files (x86)/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Data"),
    Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Data"),
]
_CARD_FILE_GLOB = "data_cards_*.mtga"

# Fallback: Scryfall bulk data downloaded by the user
_SCRYFALL_FALLBACK = Path("data/scryfall_cards.json")


def _find_arena_card_file() -> Path | None:
    for data_dir in _ARENA_DATA_DIRS:
        matches = sorted(data_dir.glob(_CARD_FILE_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def _load_arena_cards(path: Path) -> dict[int, dict]:
    """
    Parse Arena's data_cards_*.mtga file.
    Format: a JSON array where each entry has at minimum:
      grpid, titleId, cardTypeTextId, subtypeTextId, colors, cmc, linkedFaceType, etc.
    The card name isn't in this file directly — it comes from a separate
    data_loc_*.mtga locale file. We do our best with what's available.
    """
    db: dict[int, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cards = data if isinstance(data, list) else data.get("cards", [])
        for card in cards:
            grp = card.get("grpid")
            if grp is None:
                continue
            colors = card.get("colors", [])
            subtypes = card.get("subtypes", [])
            supertypes = card.get("supertypes", [])
            card_types = card.get("types", [])

            db[grp] = {
                "grpId": grp,
                "name": card.get("name", ""),
                "cmc": card.get("cmc", 0),
                "color": _colors_to_str(colors),
                "type": " ".join(card_types).lower(),
                "keywords": [kw.get("keyword", "") for kw in card.get("abilities", [])
                             if "keyword" in kw],
                "produces": _produces_from_subtypes(subtypes),
                "power": card.get("power"),
                "toughness": card.get("toughness"),
            }
        logger.info(f"Loaded {len(db)} cards from Arena data: {path.name}")
    except Exception as e:
        logger.warning(f"Failed to load Arena card file: {e}")
    return db


def _load_scryfall_cards(path: Path) -> dict[int, dict]:
    """
    Parse a Scryfall bulk data file (oracle-cards or default-cards).
    Scryfall doesn't have grpId, so this is only useful if we also
    have an Arena grpId→scryfallId mapping file.
    """
    db: dict[int, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            cards = json.load(f)
        for card in cards:
            arena_id = card.get("arena_id")
            if arena_id is None:
                continue
            db[arena_id] = {
                "grpId": arena_id,
                "name": card.get("name", ""),
                "cmc": int(card.get("cmc", 0)),
                "color": "".join(card.get("colors", [])),
                "type": card.get("type_line", "").lower(),
                "keywords": card.get("keywords", []),
                "produces": _produces_from_oracle(card.get("oracle_text", "")),
                "power": _safe_int(card.get("power")),
                "toughness": _safe_int(card.get("toughness")),
            }
        logger.info(f"Loaded {len(db)} cards from Scryfall data: {path.name}")
    except Exception as e:
        logger.warning(f"Failed to load Scryfall card file: {e}")
    return db


class GrpDatabase:
    """
    Maps Arena GRP IDs to card metadata.
    Tries Arena's own data files first, falls back to Scryfall bulk data.
    """

    def __init__(self, custom_path: Path | None = None):
        self._db: dict[int, dict] = {}
        self._load(custom_path)

    def _load(self, custom_path: Path | None) -> None:
        if custom_path and custom_path.exists():
            self._db = _load_arena_cards(custom_path)
            return

        arena_file = _find_arena_card_file()
        if arena_file:
            self._db = _load_arena_cards(arena_file)
            return

        if _SCRYFALL_FALLBACK.exists():
            self._db = _load_scryfall_cards(_SCRYFALL_FALLBACK)
            return

        logger.warning(
            "No card database found. Card names will show as 'card_<grpId>'. "
            "Download Scryfall bulk data to data/scryfall_cards.json, or ensure "
            "MTG Arena is installed at the default path."
        )

    def get(self, grp_id: int) -> dict:
        return self._db.get(grp_id, {})

    def name(self, grp_id: int) -> str:
        return self._db.get(grp_id, {}).get("name", f"card_{grp_id}")

    def __len__(self) -> int:
        return len(self._db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_COLOR_MAP = {1: "W", 2: "U", 3: "B", 4: "R", 5: "G"}

def _colors_to_str(colors: list) -> str:
    if not colors:
        return "C"
    return "".join(_COLOR_MAP.get(c, str(c)) for c in colors)


_BASIC_LANDS = {
    "plains": ["W"], "island": ["U"], "swamp": ["B"],
    "mountain": ["R"], "forest": ["G"],
}

def _produces_from_subtypes(subtypes: list[str]) -> list[str]:
    for sub in subtypes:
        if sub.lower() in _BASIC_LANDS:
            return _BASIC_LANDS[sub.lower()]
    return []


def _produces_from_oracle(oracle_text: str) -> list[str]:
    text = oracle_text.lower()
    produces = []
    for sym, kw in [("W", "white"), ("U", "blue"), ("B", "black"), ("R", "red"), ("G", "green")]:
        if kw in text and "add" in text:
            produces.append(sym)
    return produces


def _safe_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
