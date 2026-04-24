from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Iterator
from loguru import logger

from src.game_state.state import (
    GameState, PlayerState, CardObject, Phase, Zone
)

LOG_PATH = Path.home() / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"

# GRE message type we care about most
_GSM = "GREMessageType_GameStateMessage"
_PREFIX = re.compile(r"^\[UnityCrossThreadLogger\]")

PHASE_MAP: dict[str, Phase] = {
    "Phase_Beginning":        Phase.BEGINNING,
    "Phase_Main1":            Phase.MAIN1,
    "Phase_Combat":           Phase.COMBAT_BEGIN,
    "Phase_CombatDeclareAttackers": Phase.COMBAT_ATTACK,
    "Phase_CombatDeclareBlockers":  Phase.COMBAT_BLOCK,
    "Phase_CombatDamage":     Phase.COMBAT_DAMAGE,
    "Phase_Main2":            Phase.MAIN2,
    "Phase_End":              Phase.ENDING,
}

ZONE_MAP: dict[str, Zone] = {
    "ZoneType_Hand":       Zone.HAND,
    "ZoneType_Battlefield": Zone.BATTLEFIELD,
    "ZoneType_Graveyard":  Zone.GRAVEYARD,
    "ZoneType_Exile":      Zone.EXILE,
    "ZoneType_Library":    Zone.LIBRARY,
    "ZoneType_Stack":      Zone.STACK,
}


class LogTailer:
    """Tails the Arena log file, yielding new lines as they appear."""

    def __init__(self, path: Path = LOG_PATH):
        self.path = path
        self._fh = None
        self._inode = None

    def _open(self) -> None:
        self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
        self._fh.seek(0, 2)  # seek to end on first open
        try:
            self._inode = self.path.stat().st_ino
        except Exception:
            self._inode = None

    def lines(self) -> Iterator[str]:
        if self._fh is None:
            if not self.path.exists():
                logger.warning(f"Log not found: {self.path}")
                time.sleep(2)
                return
            self._open()

        # Detect log rotation (Arena overwrites on launch)
        try:
            current_inode = self.path.stat().st_ino
        except Exception:
            current_inode = None
        if current_inode != self._inode:
            logger.info("Log file rotated — reopening from start")
            self._fh.close()
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
            self._inode = current_inode

        while True:
            line = self._fh.readline()
            if not line:
                break
            yield line.rstrip("\n")


class ArenaLogParser:
    """
    Parses Arena's Player.log to extract structured game state.

    Arena emits JSON blobs after [UnityCrossThreadLogger] lines.
    We look for GREMessageType_GameStateMessage payloads which contain
    the authoritative game state: zones, objects, players, phases, mana.
    """

    def __init__(self, our_seat_id: int | None = None):
        self.tailer = LogTailer()
        self._our_seat = our_seat_id  # 1 or 2; detected from log if None
        self._state = GameState()
        self._card_db: dict[int, dict] = {}  # grpId → card metadata
        self._zone_owners: dict[int, int] = {}  # zoneId → seatId
        self._zone_types: dict[int, Zone] = {}  # zoneId → Zone enum
        self._object_zones: dict[int, int] = {}  # instanceId → zoneId
        self._pending_json: list[str] = []
        self._in_block = False

    def poll(self) -> GameState | None:
        """Read new log lines and return updated GameState if anything changed."""
        changed = False
        for line in self.tailer.lines():
            if self._process_line(line):
                changed = True
        return self._state if changed else None

    def _process_line(self, line: str) -> bool:
        # Arena log format: JSON blobs appear after a trigger line.
        # We buffer lines that look like JSON object/array starts.
        stripped = line.strip()

        if stripped.startswith("{") or stripped.startswith("["):
            self._pending_json = [stripped]
            self._in_block = True
            return False

        if self._in_block:
            self._pending_json.append(stripped)
            # Attempt parse when we see a closing brace at depth 0
            blob = "\n".join(self._pending_json)
            try:
                data = json.loads(blob)
                self._in_block = False
                self._pending_json = []
                return self._handle_payload(data)
            except json.JSONDecodeError:
                # Not complete yet — keep buffering
                return False

        return False

    def _handle_payload(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False

        # Top-level GreToClientEvent wrapper
        msgs = data.get("greToClientEvent", {}).get("greToClientMessages", [])
        if not msgs:
            # Sometimes it's wrapped differently
            msgs = data.get("greToClientMessages", [])

        changed = False
        for msg in msgs:
            if msg.get("type") == _GSM:
                gsm = msg.get("gameStateMessage", {})
                if self._apply_game_state(gsm):
                    changed = True
        return changed

    def _apply_game_state(self, gsm: dict) -> bool:
        if not gsm:
            return False

        # --- Zones ---
        for zone in gsm.get("zones", []):
            zid = zone.get("zoneId")
            ztype = ZONE_MAP.get(zone.get("type", ""), None)
            owner = zone.get("ownerSeatId")
            if zid is not None:
                if ztype:
                    self._zone_types[zid] = ztype
                if owner is not None:
                    self._zone_owners[zid] = owner

        # --- Game objects (cards) ---
        for obj in gsm.get("gameObjects", []):
            self._index_object(obj)

        # --- Players ---
        players = gsm.get("players", [])
        if players:
            self._apply_players(players)

        # --- Turn / phase ---
        turn_info = gsm.get("turnInfo", {})
        if turn_info:
            self._apply_turn(turn_info)

        # --- Priority ---
        priority_player = gsm.get("priorityPlayer", {})
        if priority_player:
            seat = priority_player.get("seatId")
            self._state.has_priority = (seat == self._our_seat)

        self._rebuild_zones()
        return True

    def _index_object(self, obj: dict) -> None:
        iid = obj.get("instanceId")
        if iid is None:
            return
        zone_id = obj.get("zoneId")
        if zone_id is not None:
            self._object_zones[iid] = zone_id

        grp_id = obj.get("grpId", 0)
        owner_seat = obj.get("ownerSeatId")
        power = None
        toughness = None
        for attr in obj.get("attackState", {}).get("attackStateAttribute", []):
            pass  # extended attributes handled separately

        # Power/toughness from overlays
        pt = obj.get("power"), obj.get("toughness")

        is_tapped = obj.get("isTapped", False)
        is_sick = obj.get("hasSummoningSickness", False)

        self._card_db[iid] = {
            "grpId": grp_id,
            "ownerSeatId": owner_seat,
            "isTapped": is_tapped,
            "hasSummoningSickness": is_sick,
            "power": obj.get("power"),
            "toughness": obj.get("toughness"),
            "name": obj.get("name", f"card_{grp_id}"),
            "cardTypes": obj.get("cardTypes", []),
            "subtypes": obj.get("subtypes", []),
            "superTypes": obj.get("superTypes", []),
            "colors": obj.get("colors", []),
            "cmc": obj.get("convertedManaCost", 0),
            "keywords": [k.get("keyword", "") for k in obj.get("keywords", [])],
        }

    def _apply_players(self, players: list[dict]) -> None:
        for p in players:
            seat = p.get("seatId")
            life = p.get("lifeTotal")
            mana = p.get("manaPool", {})

            # Auto-detect our seat from "systemSeatIds" or from match context
            if self._our_seat is None and p.get("systemSeatIds"):
                self._our_seat = seat
                logger.info(f"Detected our seat ID: {self._our_seat}")

            if seat == self._our_seat:
                target = self._state.we
            else:
                target = self._state.opponent

            if life is not None:
                target.life = life
            if mana:
                target.mana_available = self._parse_mana(mana)

    def _apply_turn(self, turn_info: dict) -> None:
        active_seat = turn_info.get("activePlayer")
        self._state.is_our_turn = (active_seat == self._our_seat)
        self._state.turn_number = turn_info.get("turnNumber", self._state.turn_number)

        phase_str = turn_info.get("phase", "")
        step_str = turn_info.get("step", "")
        combined = step_str or phase_str
        self._state.phase = PHASE_MAP.get(combined, PHASE_MAP.get(phase_str, Phase.UNKNOWN))

    def _rebuild_zones(self) -> None:
        our_hand: list[CardObject] = []
        our_bf: list[CardObject] = []
        opp_bf: list[CardObject] = []
        our_grave: list[CardObject] = []

        for iid, meta in self._card_db.items():
            zone_id = self._object_zones.get(iid)
            if zone_id is None:
                continue
            zone = self._zone_types.get(zone_id, Zone.LIBRARY)
            owner = self._zone_owners.get(zone_id, self._zone_owners.get(meta.get("ownerSeatId")))
            is_ours = (owner == self._our_seat)

            card = CardObject(
                name=meta.get("name", "unknown"),
                zone=zone,
                cmc=meta.get("cmc", 0),
                power=meta.get("power"),
                toughness=meta.get("toughness"),
                is_tapped=meta.get("isTapped", False),
                is_summoning_sick=meta.get("hasSummoningSickness", False),
                keywords=meta.get("keywords", []),
                card_type=" ".join(meta.get("cardTypes", [])).lower(),
                color="".join(meta.get("colors", [])),
            )

            if zone == Zone.HAND and is_ours:
                our_hand.append(card)
            elif zone == Zone.BATTLEFIELD:
                if is_ours:
                    our_bf.append(card)
                else:
                    opp_bf.append(card)
            elif zone == Zone.GRAVEYARD and is_ours:
                our_grave.append(card)

        self._state.we.hand = our_hand
        self._state.we.battlefield = our_bf
        self._state.opponent.battlefield = opp_bf
        self._state.we.graveyard = our_grave

    @staticmethod
    def _parse_mana(mana_pool: dict) -> dict[str, int]:
        color_map = {"colorW": "W", "colorU": "U", "colorB": "B",
                     "colorR": "R", "colorG": "G", "colorC": "C"}
        result = {}
        for key, symbol in color_map.items():
            val = mana_pool.get(key, 0)
            if val:
                result[symbol] = val
        return result
