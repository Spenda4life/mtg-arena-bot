import json
import pytest
from unittest.mock import patch, MagicMock
from src.game_state.log_parser import ArenaLogParser
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


def _make_parser_with_payload(payload: dict) -> ArenaLogParser:
    parser = ArenaLogParser()
    lines = json.dumps(payload).splitlines()
    for line in lines:
        parser._process_line(line)
    return parser


def test_life_totals():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert parser._state.we.life == 18
    assert parser._state.opponent.life == 14


def test_phase_detection():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert parser._state.phase == Phase.MAIN1


def test_mana_parsing():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert parser._state.we.mana_available == {"R": 2}


def test_hand_population():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert len(parser._state.we.hand) == 1
    assert parser._state.we.hand[0].name == "Lightning Strike"


def test_battlefield_population():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert len(parser._state.we.battlefield) == 1
    swiftspear = parser._state.we.battlefield[0]
    assert swiftspear.name == "Monastery Swiftspear"
    assert swiftspear.power == 1
    assert swiftspear.toughness == 2
    assert "haste" in swiftspear.keywords


def test_priority_detection():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert parser._state.has_priority is True


def test_our_turn_detection():
    parser = _make_parser_with_payload(SAMPLE_GSM_PAYLOAD)
    assert parser._state.is_our_turn is True
