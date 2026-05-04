from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from src.game_state.state import GameState, CardObject, Phase, Zone
from src.game_state.grp_db import GrpDatabase

logger = logging.getLogger(__name__)


@dataclass
class DeckInfo:
    id: str
    name: str
    main: list[CardObject]       # full list with duplicates (60 cards for a 60-card deck)
    sideboard: list[CardObject] = field(default_factory=list)

# Default log location on Windows
LOG_PATH = Path.home() / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"

PHASE_MAP: dict[str, Phase] = {
    "Phase_Beginning":              Phase.BEGINNING,
    "Phase_Main1":                  Phase.MAIN1,
    "Phase_Combat":                 Phase.COMBAT_BEGIN,
    "Phase_CombatDeclareAttackers": Phase.COMBAT_ATTACK,
    "Phase_CombatDeclareBlockers":  Phase.COMBAT_BLOCK,
    "Phase_CombatDamage":           Phase.COMBAT_DAMAGE,
    "Phase_Main2":                  Phase.MAIN2,
    "Phase_End":                    Phase.ENDING,
}

ZONE_MAP: dict[str, Zone] = {
    "ZoneType_Hand":        Zone.HAND,
    "ZoneType_Battlefield": Zone.BATTLEFIELD,
    "ZoneType_Graveyard":   Zone.GRAVEYARD,
    "ZoneType_Exile":       Zone.EXILE,
    "ZoneType_Library":     Zone.LIBRARY,
    "ZoneType_Stack":       Zone.STACK,
}

# Log lines that precede a GRE JSON blob
_GRE_HEADER = re.compile(r"==> Message\.GRE:|<== Message\.GRE:|GreToClientEvent|greToClientEvent")
# Log line that precedes the StartHook payload (contains player decks + inventory)
_DECK_INVENTORY_HEADER = re.compile(r"<== StartHook\(")


class LogTailer:
    """
    Tails Arena's Player.log, yielding new lines as they are written.
    Handles log rotation (Arena overwrites the file on each launch).
    """

    # How far back to seek from EOF on first open. Enough to catch the current
    # game's opening GSM even if the bot starts mid-mulligan.
    LOOKBACK_BYTES = 512 * 1024  # 512 KB

    def __init__(self, path: Path = LOG_PATH):
        self.path = path
        self._fh = None
        self._size = 0

    def _open(self, seek_end: bool = True) -> None:
        self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
        if seek_end:
            file_size = self.path.stat().st_size
            lookback = max(0, file_size - self.LOOKBACK_BYTES)
            self._fh.seek(lookback)
            if lookback > 0:
                self._fh.readline()  # discard partial line at seek point
        self._size = self.path.stat().st_size

    def lines(self) -> Iterator[str]:
        if not self.path.exists():
            logger.warning(f"Arena log not found: {self.path}")
            return

        if self._fh is None:
            self._open(seek_end=True)

        # Detect rotation: file shrank → Arena restarted
        try:
            current_size = self.path.stat().st_size
        except OSError:
            return
        if current_size < self._size:
            logger.info("Log file rotated — re-opening from start")
            self._fh.close()
            self._open(seek_end=False)
        self._size = current_size

        while line := self._fh.readline():
            yield line.rstrip("\n")


class JsonStreamExtractor:
    """
    Extracts complete JSON objects from a stream of log lines using brace counting.
    Arena sometimes puts the JSON on the same line as the header, sometimes on
    the next line, and sometimes spread across many lines.
    """

    def __init__(self):
        self._buf: list[str] = []
        self._depth = 0
        self._active = False
        self._in_string = False
        self._escape = False

    @staticmethod
    def _is_json_array_start(line: str, idx: int) -> bool:
        """Reject bracketed log prefixes like [UnityCrossThreadLogger]."""
        for ch in line[idx + 1:]:
            if ch.isspace():
                continue
            return ch in '{["-0123456789tfn]'
        return False

    def feed(self, line: str) -> list[dict]:
        results = []
        for ch_idx, ch in enumerate(line):
            if not self._active:
                if ch == "{":
                    self._active = True
                    self._depth = 1
                    self._buf = ["{"]
                    self._in_string = False
                    self._escape = False
                elif ch == "[" and self._is_json_array_start(line, ch_idx):
                    self._active = True
                    self._depth = 1
                    self._buf = ["["]
                    self._in_string = False
                    self._escape = False
            else:
                self._buf.append(ch)
                if self._in_string:
                    if self._escape:
                        self._escape = False
                    elif ch == "\\":
                        self._escape = True
                    elif ch == '"':
                        self._in_string = False
                    continue

                if ch == '"':
                    self._in_string = True
                elif ch in ("{", "["):
                    self._depth += 1
                elif ch in ("}", "]"):
                    self._depth -= 1
                    if self._depth == 0:
                        blob = "".join(self._buf)
                        self._buf = []
                        self._active = False
                        self._in_string = False
                        self._escape = False
                        try:
                            results.append(json.loads(blob))
                        except json.JSONDecodeError as e:
                            logger.debug(f"JSON parse error: {e}")
        return results


class ArenaLogParser:
    """
    Parses Arena's Player.log into a structured GameState.

    Arena emits authoritative game state via GREMessageType_GameStateMessage
    JSON blobs. These contain exact zone contents, card objects, player state,
    mana pools, phase/step, and available actions.
    """

    def __init__(self, grp_db: GrpDatabase | None = None, log_path: Path = LOG_PATH):
        self.tailer = LogTailer(log_path)
        self.extractor = JsonStreamExtractor()
        self.grp_db = grp_db or GrpDatabase()
        self._our_seat: int | None = None
        self._state = GameState()
        self.decks: dict[str, DeckInfo] = {}        # deckId → DeckInfo
        self._next_is_deck_inventory = False

        # Internal indexes rebuilt each GSM
        self._zone_owners: dict[int, int] = {}   # zoneId → seatId
        self._zone_types: dict[int, Zone] = {}   # zoneId → Zone
        self._objects: dict[int, dict] = {}       # instanceId → raw object dict

    def _reset_game_state(self) -> None:
        """Discard all in-game state. Called on each new ConnectResp."""
        self._our_seat = None
        self._state = GameState()
        self._zone_owners = {}
        self._zone_types = {}
        self._objects = {}
        logger.info("New game detected — game state reset")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll(self) -> GameState | None:
        """
        Read any new log lines. Returns the updated GameState if game state
        changed, else None.
        """
        changed = False
        for line in self.tailer.lines():
            if _DECK_INVENTORY_HEADER.search(line):
                self._next_is_deck_inventory = True
            for payload in self.extractor.feed(line):
                if self._next_is_deck_inventory:
                    self._next_is_deck_inventory = False
                    self._handle_deck_inventory(payload)
                elif self._handle_payload(payload):
                    changed = True
        return self._state if changed else None

    @property
    def state(self) -> GameState:
        return self._state

    # ------------------------------------------------------------------
    # Payload routing
    # ------------------------------------------------------------------

    def _handle_payload(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False

        # Unwrap GreToClientEvent wrapper (two common shapes)
        msgs = (
            data.get("greToClientEvent", {}).get("greToClientMessages")
            or data.get("greToClientMessages")
            or []
        )

        # Sometimes the GSM is the top-level payload itself
        if data.get("type") == "GREMessageType_GameStateMessage":
            msgs = [data]

        changed = False
        for msg in msgs:
            # systemSeatIds on the message envelope identifies our local seat
            sys_seats = msg.get("systemSeatIds", [])
            if self._our_seat is None and len(sys_seats) == 1:
                self._our_seat = sys_seats[0]
                logger.info(f"Our seat ID from message envelope: {self._our_seat}")

            msg_type = msg.get("type", "")
            if msg_type == "GREMessageType_GameStateMessage":
                gsm = msg.get("gameStateMessage", msg)
                if self._apply_gsm(gsm):
                    changed = True
            elif msg_type == "GREMessageType_ConnectResp":
                # New game — discard all state from any previous game in the lookback
                self._reset_game_state()
                self._handle_connect(msg)
        return changed

    # ------------------------------------------------------------------
    # GSM application
    # ------------------------------------------------------------------

    def _apply_gsm(self, gsm: dict) -> bool:
        if not gsm:
            return False

        # Order matters: zones first, then objects (objects reference zones)
        for zone in gsm.get("zones", []):
            zid = zone.get("zoneId")
            if zid is None:
                continue
            ztype = ZONE_MAP.get(zone.get("type", ""))
            if ztype:
                self._zone_types[zid] = ztype
            owner = zone.get("ownerSeatId")
            if owner is not None:
                self._zone_owners[zid] = owner

        for obj in gsm.get("gameObjects", []):
            iid = obj.get("instanceId")
            if iid is not None:
                # Merge: preserve existing data, update with new fields
                existing = self._objects.get(iid, {})
                existing.update({k: v for k, v in obj.items() if v is not None})
                self._objects[iid] = existing

        players = gsm.get("players", [])
        if players:
            self._apply_players(players)

        turn_info = gsm.get("turnInfo", {})
        if turn_info:
            self._apply_turn(turn_info)

        # Available actions — each entry is {"seatId": N, "action": {"actionType": ...}}
        actions = gsm.get("actions", [])
        if actions:
            self._state.available_action_types = [
                a.get("action", {}).get("actionType", "") for a in actions
            ]

        self._rebuild_zones()
        return True

    def _apply_players(self, players: list[dict]) -> None:
        for p in players:
            seat = p.get("seatId")

            # Auto-detect our seat on first GSM that includes systemSeatIds
            if self._our_seat is None and p.get("systemSeatIds"):
                self._our_seat = seat
                logger.info(f"Our seat ID detected: {seat}")

            target = self._state.we if seat == self._our_seat else self._state.opponent

            life = p.get("lifeTotal")
            if life is not None:
                target.life = life

            mana = p.get("manaPool")
            if mana:
                target.mana_available = _parse_mana(mana)

            library = p.get("library")
            if isinstance(library, dict):
                target.library_count = library.get("deckCardCount", target.library_count)

    def _apply_turn(self, turn_info: dict) -> None:
        active = turn_info.get("activePlayer")
        if active is not None:
            self._state.is_our_turn = (active == self._our_seat)

        turn_num = turn_info.get("turnNumber")
        if turn_num is not None:
            self._state.turn_number = turn_num

        phase_str = turn_info.get("phase", "")
        step_str = turn_info.get("step", "")
        # Step is more specific than phase; prefer it
        key = step_str or phase_str
        if key in PHASE_MAP:
            self._state.phase = PHASE_MAP[key]
        elif phase_str in PHASE_MAP:
            self._state.phase = PHASE_MAP[phase_str]

        # priorityPlayer lives inside turnInfo in the real log
        pp = turn_info.get("priorityPlayer")
        if pp is not None:
            self._state.has_priority = (pp == self._our_seat)

    def _handle_connect(self, msg: dict) -> None:
        """Extract our seat ID from a ConnectResp."""
        seat = msg.get("seatId") or msg.get("connectResp", {}).get("seatId")
        if seat and self._our_seat is None:
            self._our_seat = seat
            logger.info(f"Seat ID from ConnectResp: {seat}")

    # ------------------------------------------------------------------
    # Deck inventory
    # ------------------------------------------------------------------

    def _handle_deck_inventory(self, payload: dict) -> None:
        # StartHook payload shape:
        #   "DeckSummaries": [{"DeckId": "uuid", "Name": "...", ...}, ...]
        #   "Decks": {"uuid": {"MainDeck": [{"cardId": N, "quantity": N}, ...],
        #                      "Sideboard": [...]}, ...}
        if not isinstance(payload, dict):
            return

        summaries = {s["DeckId"]: s["Name"]
                     for s in payload.get("DeckSummaries", [])
                     if "DeckId" in s}

        decks_data = payload.get("Decks", {})
        if not isinstance(decks_data, dict):
            logger.debug("Deck inventory payload had unexpected shape, skipping")
            return

        for deck_id, deck in decks_data.items():
            name = summaries.get(deck_id, deck_id)
            main_cards = [
                card
                for entry in deck.get("MainDeck", [])
                for card in [self._grp_to_card(entry["cardId"])] * entry.get("quantity", 1)
            ]
            side_cards = [
                card
                for entry in deck.get("Sideboard", [])
                for card in [self._grp_to_card(entry["cardId"])] * entry.get("quantity", 1)
            ]
            self.decks[deck_id] = DeckInfo(id=deck_id, name=name,
                                           main=main_cards, sideboard=side_cards)
        logger.info(f"Loaded {len(self.decks)} deck(s) from inventory")

    def _grp_to_card(self, grp_id: int) -> CardObject:
        db = self.grp_db.get(grp_id)
        return CardObject(
            name=db.get("name") or f"card_{grp_id}",
            zone=Zone.LIBRARY,
            cmc=db.get("cmc", 0),
            power=db.get("power"),
            toughness=db.get("toughness"),
            card_type=db.get("type", ""),
            color=db.get("color", ""),
            keywords=db.get("keywords", []),
            produces_mana=db.get("produces", []),
        )

    # ------------------------------------------------------------------
    # Zone rebuild
    # ------------------------------------------------------------------

    def _rebuild_zones(self) -> None:
        our_hand: list[CardObject] = []
        our_bf: list[CardObject] = []
        opp_bf: list[CardObject] = []
        our_grave: list[CardObject] = []
        stack: list[CardObject] = []

        for _iid, obj in self._objects.items():
            zone_id = obj.get("zoneId")
            if zone_id is None:
                continue

            zone = self._zone_types.get(zone_id, Zone.LIBRARY)
            zone_owner = self._zone_owners.get(zone_id)
            obj_owner = obj.get("ownerSeatId")
            is_ours = (zone_owner == self._our_seat) or (
                zone_owner is None and obj_owner == self._our_seat
            )

            card = self._make_card(obj, zone)

            if zone == Zone.HAND and is_ours:
                our_hand.append(card)
            elif zone == Zone.BATTLEFIELD:
                (our_bf if is_ours else opp_bf).append(card)
            elif zone == Zone.GRAVEYARD and is_ours:
                our_grave.append(card)
            elif zone == Zone.STACK:
                stack.append(card)

        self._state.we.hand = our_hand
        self._state.we.battlefield = our_bf
        self._state.we.graveyard = our_grave
        self._state.opponent.battlefield = opp_bf
        self._state.stack = [c.name for c in stack]

    def _make_card(self, obj: dict, zone: Zone) -> CardObject:
        grp_id = obj.get("grpId", 0)
        db_entry = self.grp_db.get(grp_id)

        name = db_entry.get("name") or obj.get("name") or f"card_{grp_id}"
        cmc = db_entry.get("cmc") or obj.get("convertedManaCost", 0)
        card_type = db_entry.get("type") or " ".join(obj.get("cardTypes", [])).lower()
        color = db_entry.get("color") or "".join(obj.get("colors", []))
        keywords = db_entry.get("keywords") or [
            k.get("keyword", "") for k in obj.get("keywords", [])
        ]
        produces = db_entry.get("produces") or []

        return CardObject(
            name=name,
            zone=zone,
            instance_id=obj.get("instanceId"),
            cmc=cmc,
            power=obj.get("power"),
            toughness=obj.get("toughness"),
            is_tapped=obj.get("isTapped", False),
            is_summoning_sick=obj.get("hasSummoningSickness", False),
            keywords=keywords,
            card_type=card_type,
            color=color,
            produces_mana=produces,
        )


def _parse_mana(pool: dict) -> dict[str, int]:
    mapping = {
        "colorW": "W", "colorU": "U", "colorB": "B",
        "colorR": "R", "colorG": "G", "colorC": "C",
    }
    return {sym: pool[key] for key, sym in mapping.items() if pool.get(key, 0) > 0}
