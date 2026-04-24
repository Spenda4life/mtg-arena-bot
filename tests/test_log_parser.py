import json
import pytest
from src.game_state.log_parser import ArenaLogParser, JsonStreamExtractor
from src.game_state.grp_db import GrpDatabase
from src.game_state.state import Phase, Zone


SAMPLE_GSM_PAYLOAD = {
    "greToClientEvent": {
        "greToClientMessages": [
            {
                "type": "GREMessageType_GameStateMessage",
                "gameStateMessage": {
                    "turnInfo": {
                        "activePlayer": 1,
                        "turnNumber": 3,
                        "phase": "Phase_Main1",
                    },
                    "players": [
                        {"seatId": 1, "lifeTotal": 18, "systemSeatIds": [1],
                         "manaPool": {"colorR": 2}},
                        {"seatId": 2, "lifeTotal": 14},
                    ],
                    "zones": [
                        {"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1},
                        {"zoneId": 11, "type": "ZoneType_Battlefield", "ownerSeatId": 1},
                        {"zoneId": 20, "type": "ZoneType_Battlefield", "ownerSeatId": 2},
                    ],
                    "gameObjects": [
                        {
                            "instanceId": 100,
                            "grpId": 55555,
                            "zoneId": 10,
                            "ownerSeatId": 1,
                            "name": "Lightning Strike",
                            "cardTypes": ["Instant"],
                            "convertedManaCost": 2,
                            "colors": ["R"],
                            "keywords": [],
                            "isTapped": False,
                            "hasSummoningSickness": False,
                        },
                        {
                            "instanceId": 101,
                            "grpId": 55556,
                            "zoneId": 11,
                            "ownerSeatId": 1,
                            "name": "Monastery Swiftspear",
                            "cardTypes": ["Creature"],
                            "convertedManaCost": 1,
                            "colors": ["R"],
                            "power": 1,
                            "toughness": 2,
                            "keywords": [{"keyword": "haste"}, {"keyword": "prowess"}],
                            "isTapped": False,
                            "hasSummoningSickness": False,
                        },
                    ],
                    "priorityPlayer": {"seatId": 1},
                }
            }
        ]
    }
}


def _feed_payload(parser: ArenaLogParser, payload: dict) -> None:
    blob = json.dumps(payload)
    for obj in parser.extractor.feed(blob):
        parser._handle_payload(obj)


def _make_parser() -> ArenaLogParser:
    grp_db = GrpDatabase.__new__(GrpDatabase)
    grp_db._db = {}
    parser = ArenaLogParser.__new__(ArenaLogParser)
    parser.extractor = JsonStreamExtractor()
    parser.grp_db = grp_db
    parser._our_seat = None
    from src.game_state.state import GameState
    parser._state = GameState()
    parser._zone_owners = {}
    parser._zone_types = {}
    parser._objects = {}
    return parser


def test_life_totals():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert parser._state.we.life == 18
    assert parser._state.opponent.life == 14


def test_phase_detection():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert parser._state.phase == Phase.MAIN1


def test_mana_parsing():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert parser._state.we.mana_available == {"R": 2}


def test_hand_population():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert len(parser._state.we.hand) == 1
    assert parser._state.we.hand[0].name == "Lightning Strike"


def test_battlefield_population():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert len(parser._state.we.battlefield) == 1
    swiftspear = parser._state.we.battlefield[0]
    assert swiftspear.name == "Monastery Swiftspear"
    assert swiftspear.power == 1
    assert swiftspear.toughness == 2
    assert "haste" in swiftspear.keywords


def test_priority_detection():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert parser._state.has_priority is True


def test_our_turn_detection():
    parser = _make_parser()
    _feed_payload(parser, SAMPLE_GSM_PAYLOAD)
    assert parser._state.is_our_turn is True


def test_json_stream_extractor_multiline():
    """Extractor must handle JSON spread across multiple log lines."""
    extractor = JsonStreamExtractor()
    lines = ['{"key":', '"value"', "}"]
    results = []
    for line in lines:
        results.extend(extractor.feed(line))
    assert len(results) == 1
    assert results[0] == {"key": "value"}


def test_json_stream_extractor_multiple_objects():
    """Extractor must find multiple JSON objects in one line."""
    extractor = JsonStreamExtractor()
    results = extractor.feed('{"a":1}{"b":2}')
    assert len(results) == 2
    assert results[0] == {"a": 1}
    assert results[1] == {"b": 2}
