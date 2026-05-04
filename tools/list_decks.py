"""
Reads the current Arena log from the beginning and prints all decks found
in the Inventory.GetPlayerDecks payload.

Usage:
    python tools/list_decks.py

Arena must have been opened and reached the home screen at least once since
the last log rotation for the inventory data to be present.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.game_state.log_parser import ArenaLogParser, JsonStreamExtractor, DeckInfo, _DECK_INVENTORY_HEADER
from src.game_state.grp_db import GrpDatabase

LOG_PATH = Path.home() / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"


def scan_log_for_decks(log_path: Path) -> dict[str, DeckInfo]:
    if not log_path.exists():
        print(f"ERROR: Log not found at {log_path}")
        sys.exit(1)

    grp_db = GrpDatabase()
    parser = ArenaLogParser.__new__(ArenaLogParser)
    parser.extractor = JsonStreamExtractor()
    parser.grp_db = grp_db
    parser._our_seat = None
    parser.decks = {}
    parser._next_is_deck_inventory = False
    from src.game_state.state import GameState
    parser._state = GameState()
    parser._zone_owners = {}
    parser._zone_types = {}
    parser._objects = {}

    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if _DECK_INVENTORY_HEADER.search(line):
                parser._next_is_deck_inventory = True
            for payload in parser.extractor.feed(line):
                if parser._next_is_deck_inventory:
                    parser._next_is_deck_inventory = False
                    parser._handle_deck_inventory(payload)

    return parser.decks


def main() -> None:
    print(f"Scanning: {LOG_PATH}\n")
    decks = scan_log_for_decks(LOG_PATH)

    if not decks:
        print("No decks found in log.")
        print("Make sure Arena has been opened and reached the home screen since the last launch.")
        return

    # Filter to player-created decks only (precons have locale-key names like ?=?Loc/...)
    custom = {did: d for did, d in decks.items() if not d.name.startswith("?=?Loc/")}
    print(f"Found {len(custom)} custom deck(s) ({len(decks)} total including precons):\n")
    decks = custom
    for deck in sorted(decks.values(), key=lambda d: d.name.lower()):
        land_count = sum(1 for c in deck.main if c.is_land)
        spell_count = len(deck.main) - land_count
        side_count = len(deck.sideboard)
        print(f"  [{deck.name}]")
        print(f"    ID       : {deck.id}")
        print(f"    Main     : {len(deck.main)} cards ({spell_count} spells, {land_count} lands)")
        if side_count:
            print(f"    Sideboard: {side_count} cards")
        # Show unique card names with counts
        from collections import Counter
        counts = Counter(c.name for c in deck.main)
        for name, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"      {n}x {name}")
        print()


if __name__ == "__main__":
    main()
