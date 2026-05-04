import pytest
from src.vision.layout import CardPositionMapper
from src.game_state.state import CardObject, Zone


def _mapper(w=1920, h=1080) -> CardPositionMapper:
    return CardPositionMapper(w, h)


def _card(is_land=False, is_creature=False) -> CardObject:
    c = CardObject(name="test", zone=Zone.HAND, cmc=2)
    if is_land:
        c.card_type = "land"
    if is_creature:
        c.card_type = "creature"
    return c


def test_single_hand_card_is_centered():
    m = _mapper()
    x, y = m.hand_position(0, 1)
    assert x == pytest.approx(1920 * 0.5, abs=5)


def test_seven_hand_cards_span_expected_range():
    m = _mapper()
    positions = [m.hand_position(i, 7) for i in range(7)]
    xs = [p[0] for p in positions]
    assert xs[0] < xs[-1]
    # Leftmost should be near hand_x_min, rightmost near hand_x_max
    assert xs[0] == pytest.approx(1920 * 0.175, abs=5)
    assert xs[-1] == pytest.approx(1920 * 0.825, abs=5)


def test_hand_positions_evenly_spaced():
    m = _mapper()
    xs = [m.hand_position(i, 5)[0] for i in range(5)]
    gaps = [xs[i+1] - xs[i] for i in range(len(xs)-1)]
    assert max(gaps) - min(gaps) < 2  # within 2px of equal spacing


def test_assign_hand_positions_sets_coordinates():
    m = _mapper()
    hand = [_card() for _ in range(3)]
    m.assign_hand_positions(hand)
    for card in hand:
        assert card.screen_x is not None
        assert card.screen_y is not None


def test_assign_battlefield_positions_separates_rows():
    m = _mapper()
    bf = [_card(is_creature=True), _card(is_land=True), _card(is_creature=True)]
    m.assign_battlefield_positions(bf, is_ours=True)
    creatures = [c for c in bf if c.is_creature]
    lands = [c for c in bf if c.is_land]
    # Creatures should be higher up the screen (smaller y) than lands
    assert creatures[0].screen_y < lands[0].screen_y


def test_opp_battlefield_y_above_midpoint():
    m = _mapper(1920, 1080)
    bf = [_card(is_creature=True)]
    m.assign_battlefield_positions(bf, is_ours=False)
    assert bf[0].screen_y < 1080 // 2


def test_our_battlefield_y_below_midpoint():
    m = _mapper(1920, 1080)
    bf = [_card(is_creature=True)]
    m.assign_battlefield_positions(bf, is_ours=True)
    assert bf[0].screen_y > 1080 // 2


def test_scales_with_resolution():
    m_hd = _mapper(1920, 1080)
    m_qhd = _mapper(2560, 1440)
    x_hd, y_hd = m_hd.hand_position(0, 7)
    x_qhd, y_qhd = m_qhd.hand_position(0, 7)
    # QHD should be proportionally larger
    assert x_qhd == pytest.approx(x_hd * 2560 / 1920, abs=5)
    assert y_qhd == pytest.approx(y_hd * 1440 / 1080, abs=5)


def test_opp_player_position():
    m = _mapper(1920, 1080)
    x, y = m.opp_player_position()
    assert y < 1080 * 0.15  # opponent is near top of screen
