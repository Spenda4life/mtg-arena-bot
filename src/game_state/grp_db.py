from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from loguru import logger

# Arena stores its SQLite card database under the Raw downloads directory.
_ARENA_RAW_DIRS = [
    Path("C:/Program Files (x86)/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw"),
    Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw"),
]
_CARD_DB_GLOB = "Raw_CardDatabase_*.mtga"

# Legacy JSON card file (older Arena versions)
_ARENA_DATA_DIRS = [
    Path("C:/Program Files (x86)/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Data"),
    Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Data"),
]
_CARD_FILE_GLOB = "data_cards_*.mtga"

# Fallback: Scryfall bulk data downloaded by the user
_SCRYFALL_FALLBACK = Path("data/scryfall_cards.json")


def _find_arena_sqlite_db() -> Path | None:
    for raw_dir in _ARENA_RAW_DIRS:
        matches = sorted(raw_dir.glob(_CARD_DB_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def _find_arena_card_file() -> Path | None:
    for data_dir in _ARENA_DATA_DIRS:
        matches = sorted(data_dir.glob(_CARD_FILE_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def _load_arena_sqlite(path: Path) -> dict[int, dict]:
    """Load card metadata from Arena's Raw_CardDatabase_*.mtga SQLite file."""
    db: dict[int, dict] = {}
    try:
        con = sqlite3.connect(str(path))
        rows = con.execute("""
            SELECT c.GrpId, l.Loc,
                   c.Types, c.Subtypes, c.Colors,
                   c.Power, c.Toughness,
                   c.Order_CMCWithXLast
            FROM Cards c
            JOIN Localizations_enUS l ON c.TitleId = l.LocId
            WHERE l.Loc IS NOT NULL
        """).fetchall()
        con.close()
        for grp_id, name, types_raw, subtypes_raw, colors_raw, power, toughness, cmc in rows:
            type_str = _sqlite_types_to_str(types_raw)
            db[grp_id] = {
                "grpId":      grp_id,
                "name":       name,
                "cmc":        int(cmc) if cmc is not None else 0,
                "type":       type_str,
                "color":      _sqlite_colors_to_str(colors_raw),
                "power":      _safe_int(power),
                "toughness":  _safe_int(toughness),
                "keywords":   [],
                "produces":   _sqlite_produces(subtypes_raw, type_str),
            }
        logger.info(f"Loaded {len(db)} cards from Arena SQLite DB: {path.name}")
    except Exception as e:
        logger.warning(f"Failed to load Arena SQLite card DB: {e}")
    return db


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

        sqlite_db = _find_arena_sqlite_db()
        if sqlite_db:
            self._db = _load_arena_sqlite(sqlite_db)
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


# SQLite card type integer → readable string (from Enums WHERE Type='CardType')
_SQLITE_TYPE_MAP = {
    1: "artifact", 2: "creature", 3: "enchantment", 4: "instant",
    5: "land", 6: "phenomenon", 7: "plane", 8: "planeswalker",
    9: "scheme", 10: "sorcery", 11: "kindred", 12: "vanguard",
    13: "dungeon", 14: "battle",
}
# SQLite color integer → letter
_SQLITE_COLOR_MAP = {1: "W", 2: "U", 3: "B", 4: "R", 5: "G"}
_SQLITE_SUBTYPE_LANDS = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G"}
# Integer subtype IDs for basic land types
_SQLITE_SUBTYPE_ID_LANDS = {29: "G", 43: "U", 49: "R", 54: "W", 69: "B"}


def _sqlite_types_to_str(raw: str) -> str:
    if not raw:
        return ""
    parts = []
    for tok in raw.split(","):
        tok = tok.strip()
        try:
            parts.append(_SQLITE_TYPE_MAP.get(int(tok), tok))
        except ValueError:
            parts.append(tok.lower())
    return " ".join(parts)


def _sqlite_colors_to_str(raw: str) -> str:
    if not raw:
        return "C"
    result = []
    for tok in raw.split(","):
        tok = tok.strip()
        try:
            result.append(_SQLITE_COLOR_MAP.get(int(tok), tok))
        except ValueError:
            result.append(tok)
    return "".join(result) or "C"


def _sqlite_produces(subtypes_raw: str, type_str: str) -> list[str]:
    if "land" not in type_str or not subtypes_raw:
        return []
    for tok in subtypes_raw.split(","):
        tok = tok.strip()
        try:
            color = _SQLITE_SUBTYPE_ID_LANDS.get(int(tok))
            if color:
                return [color]
        except ValueError:
            if tok.lower() in _SQLITE_SUBTYPE_LANDS:
                return [_SQLITE_SUBTYPE_LANDS[tok.lower()]]
    return []
