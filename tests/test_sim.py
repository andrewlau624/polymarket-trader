"""Trade shape, walk-forward Kelly replay, and Riot-derived game winners."""

import pytest

from src import sim
from src.esports.results import winners


def T(i, pnl, cost=0.40, game=None):
    return {"t": float(i), "game": game or f"g{i}", "cost": cost, "pnl": pnl}


def test_shape_payoff_vs_expectancy():
    # 30% winners at +0.55, 70% losers at -0.25: payoff 2.2 < breakeven 2.33 -> negative
    tr = [T(i, 0.55) for i in range(3)] + [T(i + 3, -0.25) for i in range(7)]
    s = sim.shape(tr)
    assert s["win_rate"] == pytest.approx(0.3) and s["payoff"] == pytest.approx(2.2)
    assert s["breakeven_payoff"] == pytest.approx(7 / 3)
    assert s["exp_share"] < 0 and s["profit_factor"] < 1


def test_replay_bets_nothing_on_a_losing_history_and_grows_on_a_winning_one():
    losing = [T(i, -0.05 if i % 2 else 0.03) for i in range(200)]
    f, dd, bets = sim.replay(losing, 200.0)
    assert bets == 0 and f == 200.0                       # edge estimate never above 0
    winning = [T(i, 0.30 if i % 2 else -0.20) for i in range(200)]
    f, dd, bets = sim.replay(winning, 200.0)
    assert bets > 0 and f > 200.0


def test_replay_never_stakes_more_than_max_frac():
    big = [T(i, 0.59, cost=0.40) for i in range(60)]    # absurd edge, capped at 5%
    f, _dd, bets = sim.replay(big, 100.0)
    assert f <= 100.0 * (1 + sim.MAX_FRAC * 0.59 / 0.40) ** bets + 1e-6


def test_monte_carlo_needs_games():
    assert sim.monte_carlo([T(i, 0.1) for i in range(3)]) is None
    mc = sim.monte_carlo([T(i, 0.2 if i % 3 else -0.3, game=f"g{i % 10}") for i in range(90)],
                         reps=200)
    assert 0.0 <= mc["p_loss"] <= 1.0 and mc["p05"] <= mc["median"] <= mc["p95"]


def test_winners_from_series_counter_and_riot_result():
    recs = [{"ts": "2026-09-28T10:00", "event": "lol-tp-tos-2026-09-28", "wins": [0, 0], "game": 1},
            {"ts": "2026-09-28T10:40", "event": "lol-tp-tos-2026-09-28", "wins": [0, 1], "game": 2},
            {"ts": "2026-09-28T11:20", "event": "lol-tp-tos-2026-09-28", "wins": [1, 1], "game": 3}]
    matches = [{"start": "2026-09-28T10:00:00Z",
                "teams": [{"code": "tp", "wins": 2, "won": True},
                          {"code": "ots", "wins": 1, "won": False}]}]
    w = winners(recs, matches)
    assert w == {"lol-tp-tos-2026-09-28#1": -1, "lol-tp-tos-2026-09-28#2": 1,
                 "lol-tp-tos-2026-09-28#3": 1}


def test_one_game_never_carries_more_than_the_game_cap():
    hist = [T(i, 0.30 if i % 2 else -0.20) for i in range(40)]
    same = [T(100 + i, -0.40, game="g-big") for i in range(20)]   # 20 entries, one losing game
    f_hist, _, _ = sim.replay(hist, 200.0)
    f_all, _, _ = sim.replay(hist + same, 200.0)
    assert f_all >= f_hist * (1 - sim.MAX_GAME_FRAC / 0.40 * 0.40) - 1e-6
