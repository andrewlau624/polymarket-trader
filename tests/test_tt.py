"""T3 table tennis: point model, score parsing and sides."""

import pytest

from hunt import tt_report as tt


def test_even_players_are_coin_flips():
    assert tt.p_game(0.5, 0, 0) == pytest.approx(0.5)
    assert tt.p_match(0.5, 0, 0) == pytest.approx(0.5)
    assert tt.p_game(0.5, 10, 10) == pytest.approx(0.5)          # deuce


def test_two_games_up_between_equals_is_seven_eighths():
    assert tt.p_match(0.5, 2, 0) == pytest.approx(0.875)
    assert tt.p_match(0.5, 0, 2) == pytest.approx(0.125)


def test_game_point_and_finished_games():
    assert tt.p_game(0.5, 10, 0) > 0.99
    assert tt.p_game(0.5, 11, 9) == 1.0 and tt.p_game(0.5, 12, 14) == 0.0
    assert tt.p_game(0.5, 11, 10) == pytest.approx(0.75)          # win next point, or deuce


def test_solve_q_inverts_the_match_probability():
    for p0 in (0.2, 0.5, 0.73, 0.9):
        assert tt.p_match(tt.solve_q(p0), 0, 0) == pytest.approx(p0, abs=1e-4)


def test_parse_counts_games_and_flips_for_the_second_team():
    assert tt.parse("11-7, 11-5, 3-2", flip=False) == (2, 0, 3, 2)
    assert tt.parse("11-7, 11-5, 3-2", flip=True) == (0, 2, 2, 3)
    assert tt.parse("7-11, 11-3, 10-12, 5-11", flip=False) == (1, 3, 0, 0)
    assert tt.parse("12-10, 9-9", flip=False) == (1, 0, 9, 9)       # deuce game finished
    assert tt.parse("8-7", flip=False) == (0, 0, 8, 7)
    assert tt.parse("", flip=False) == (0, 0, 0, 0)
