"""Model, signals, sizing, risk: the pure layer."""

import math

import pytest

from src.income.model import MarginModel, covers
from src.income.risk import (Limits, allowed, evaluate_kills, halt_reason,
                             scenario_pnl, with_trade, worst_case)
from src.income.signals import (hedge_credit, mm_quotes, model_agrees, rest_hedge,
                                taker_arbs, value_takes)
from src.income.sizing import arb_shares, kelly_binary, value_shares
from src.pm_us.fees import taker_fee


def q(bid, ask, bsz=10, asz=10):
    return {"bid": bid, "ask": ask, "bid_sz": bsz, "ask_sz": asz}


# ---- model -----------------------------------------------------------------

def test_pmf_normalised_after_key_numbers():
    m = MarginModel(-3.5, 14.0)
    assert math.isclose(sum(m.pmf.values()), 1.0, abs_tol=1e-9)
    assert 0 not in m.pmf                       # no ties in football


def test_p_cover_monotone_and_consistent():
    m = MarginModel(7.0, 15.0)
    lines = [x + 0.5 for x in range(-20, 20)]
    ps = [m.p_cover(L) for L in lines]
    assert all(a <= b + 1e-12 for a, b in zip(ps, ps[1:]))   # higher line easier
    assert math.isclose(m.p_between(-3.5, 2.5), m.p_cover(2.5) - m.p_cover(-3.5))


def test_key_numbers_carry_extra_mass():
    m = MarginModel(0.0, 14.0)
    assert m.pmf[3] > 2 * m.pmf[2] and m.pmf[7] > 2 * m.pmf[8]


def test_pickem_sigma_uses_prior_not_blowup():
    # Clemson @ Cal, home +1.5, near-even moneyline: implied sigma hits 30
    m = MarginModel.from_line(1.5, 0.52, ref_is_home=False, league="cfb")
    assert 14.0 < m.sigma < 17.0


def test_covers_matches_venue_rule():
    assert covers(-3.5, 4) and not covers(-3.5, 3)     # pays iff margin > -L
    assert covers(2.5, -2) and not covers(2.5, -3)


# ---- signals ---------------------------------------------------------------

def test_consistent_book_is_not_an_arb():
    # the exact book the old maker scan called "risk-free"
    book = {-1.5: q(0.49, 0.52), -0.5: q(0.52, 0.55)}
    assert taker_arbs(book) == []
    assert rest_hedge(book, min_credit=0.005) == []


def test_real_violation_survives_fees():
    book = {-1.5: q(0.60, 0.62), 0.5: q(0.50, 0.52)}
    arbs = taker_arbs(book, min_credit=0.0)
    assert len(arbs) == 1
    a = arbs[0]
    assert a["sell"] == -1.5 and a["buy"] == 0.5
    assert math.isclose(a["credit"], 0.60 - 0.52 - taker_fee(0.60) - taker_fee(0.52))


def test_small_violation_killed_by_taker_fees():
    book = {-1.5: q(0.53, 0.56), 0.5: q(0.50, 0.52)}   # 1c gross, ~3.4c fees
    assert taker_arbs(book, min_credit=0.0) == []


def test_rest_hedge_prices_hedge_at_taker_fee():
    # L1 wide: resting a sell at 0.599 and buying L2 at 0.55 pays
    book = {-1.5: q(0.50, 0.60), 0.5: q(0.53, 0.55)}
    sigs = rest_hedge(book, tick=0.001, min_credit=0.005)
    s = sigs[0]
    assert s["rest_side"] == "sell" and s["rest_px"] == pytest.approx(0.599)
    assert s["credit"] < 0.599 - 0.55                # net of the hedge's taker fee


def test_hedge_credit_reprices_on_move():
    assert hedge_credit("sell", 0.599, q(0.53, 0.55)) > 0
    assert hedge_credit("sell", 0.599, q(0.60, 0.62)) < 0     # hedge ran away


def test_value_take_needs_edge_after_fee():
    fair = {3.5: 0.70}
    assert value_takes({3.5: q(0.60, 0.66)}, fair, edge_min=0.03) == []   # 4c - 1.6c fee
    got = value_takes({3.5: q(0.60, 0.63)}, fair, edge_min=0.03)
    assert got and got[0]["side"] == "buy"
    got = value_takes({3.5: q(0.76, 0.80)}, fair, edge_min=0.03)
    assert got and got[0]["side"] == "sell"


def test_mm_quotes_never_cross_or_quote_through_fair():
    book = {0.5: q(0.48, 0.50), 1.5: q(0.40, 0.60)}
    fair = {0.5: 0.52, 1.5: 0.55}
    for w in mm_quotes(book, fair, half_spread=0.02):
        b = book[w["line"]]
        assert w["bid"] < b["ask"] and w["ask"] > b["bid"]
        assert w["bid"] < w["fair"] < w["ask"]


# ---- sizing ----------------------------------------------------------------

def test_kelly():
    assert kelly_binary(0.5, 0.5) == 0.0
    assert kelly_binary(0.6, 0.5) == pytest.approx(0.2)


def test_value_shares_uses_kelly_and_caps():
    sig = {"side": "buy", "px": 0.50, "fair": 0.60}
    n = value_shares(sig, bankroll=100, kelly_frac=0.25, max_order_usd=1000)
    cost = 0.50 + taker_fee(0.50)
    assert n == int(kelly_binary(0.60, cost) * 0.25 * 100 / cost)
    assert value_shares(sig, 100, 0.25, max_order_usd=2.0) == int(2.0 / cost)
    assert value_shares({"side": "sell", "px": 0.50, "fair": 0.60}, 100) == 0


def test_arb_shares_capital_bound():
    sig = {"kind": "taker_arb", "sell_px": 0.60, "buy_px": 0.52, "size": 100}
    assert arb_shares(sig, free_capital=4.0, max_shares=50) == int(4.0 / 0.92)


# ---- risk ------------------------------------------------------------------

def test_hedged_pair_worst_case_is_the_credit():
    pos, cash = with_trade({}, 0.0, -1.5, "sell", 0.60, 5, 0.0)
    pos, cash = with_trade(pos, cash, 0.5, "buy", 0.52, 5, 0.0)
    assert worst_case(pos, cash) == pytest.approx(0.08 * 5)


def test_naked_short_worst_case():
    pos, cash = with_trade({}, 0.0, -1.5, "sell", 0.60, 5, 0.0)
    assert worst_case(pos, cash) == pytest.approx(-0.40 * 5)
    pnl = scenario_pnl(pos, cash)
    assert pnl[1] == pytest.approx(3.0) and pnl[2] == pytest.approx(-2.0)


def test_limits():
    lim = Limits(max_game_loss=1.0, max_total_loss=2.0)
    pos, cash = with_trade({}, 0.0, 3.5, "buy", 0.50, 3, 0.0)
    assert allowed(pos, cash, 0.0, lim)[0] is False           # -1.50 on one game
    pos, cash = with_trade({}, 0.0, 3.5, "buy", 0.50, 2, 0.0)
    assert allowed(pos, cash, 0.0, lim)[0] is True
    assert allowed(pos, cash, -1.5, lim)[0] is False          # book-wide cap


def test_kill_rules_wait_for_sample_then_trip():
    bad = [-0.01, -0.02, 0.005] * 10
    assert evaluate_kills({"value": bad}, {}) == {}           # n=30 < 40
    k = evaluate_kills({"value": bad * 2}, {})
    assert "value" in k
    assert evaluate_kills({"value": [0.01, 0.02] * 30}, {}) == {}


def test_halts():
    lim = Limits(daily_loss=3.0, max_drawdown_frac=0.25)
    assert halt_reason(-1, -3.5, 20, lim).startswith("daily")
    assert halt_reason(-6, -1, 20, lim).startswith("drawdown")
    assert halt_reason(-1, -1, 20, lim) is None


def test_flipped_model_is_rejected():
    m = MarginModel(-7.0, 15.0)
    lines = [x + 0.5 for x in range(-15, 15, 3)]
    book = {L: q(round(m.p_cover(L) - 0.01, 3), round(m.p_cover(L) + 0.01, 3))
            for L in lines}
    assert model_agrees(book, m.ladder(lines))[0]
    flipped = MarginModel(7.0, 15.0)                 # wrong side of the game
    assert not model_agrees(book, flipped.ladder(lines))[0]


def test_clv_is_judged_on_the_ladder_close_not_our_model(tmp_path):
    from src.income.measure import metrics
    from src.pm_us.jsonlog import append
    p = str(tmp_path / "l.jsonl")
    G = "asc-cfb-a-b-2026-09-26"
    append(p, {"kind": "fill", "strat": "value", "game": G, "line": 3.5,
               "side": "buy", "px": 0.50, "qty": 2, "ts": "2"})
    append(p, {"kind": "mark", "game": G, "line": 3.5, "state": "pre", "mid": 0.56,
               "ts": "3"})
    append(p, {"kind": "mark", "game": G, "line": 3.5, "state": "pre", "mid": 0.58,
               "ts": "4"})                                # the close
    append(p, {"kind": "fair", "game": G, "state": "in", "ts": "5"})
    m = metrics(p)
    want = 0.58 - 0.50 - taker_fee(0.50)
    assert m["value"] == [pytest.approx(want)] * 2         # per share


def test_rest_hedge_metric_excludes_taker_arb_pairs(tmp_path):
    from src.income.measure import metrics
    from src.pm_us.jsonlog import append
    p = str(tmp_path / "l.jsonl")
    append(p, {"kind": "pair_done", "pair_kind": "taker_arb", "shares": 3,
               "credit_net": -0.5})
    append(p, {"kind": "pair_done", "pair_kind": "rest_hedge", "shares": 3,
               "credit_net": 0.01})
    assert metrics(p)["rest_hedge"] == [0.01]


def test_unfillable_rest_leg_is_skipped():
    from income_bot import fillable
    sig = {"rest_line": 7.5, "rest_side": "sell", "rest_px": 0.189}
    assert not fillable(sig, {7.5: 0.003}, 0.08)      # Howard +7.5, worth 0.3c
    assert fillable(sig, {7.5: 0.15}, 0.08)
    assert fillable(sig, {}, 0.08)                     # no model: cannot judge
    buy = {"rest_line": 14.5, "rest_side": "buy", "rest_px": 0.066}
    assert not fillable(buy, {14.5: 0.20}, 0.08)


def test_only_football_gets_a_model():
    from src.income.fairvalue import LineSource
    ls = LineSource(pause=0)
    ls.pre_lines["e1"] = (-1.5, 0.55, 0.45, "DK")
    out = {"event_id": "e1", "_path": "baseball/mlb", "league": "mlb",
           "ref_is_home": True, "model": None}
    ls._attach_line(out)
    assert out["model"] is None
    out = {**out, "league": "cfb", "model": None}
    ls._attach_line(out)
    assert out["model"] is not None
