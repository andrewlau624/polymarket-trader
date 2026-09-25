"""In-play: live model, persistence filter, paper round trips, go rule, loop."""

import math

import pytest

from src.income.inplay import (GO_RULE, Tracker, go_status, live_model,
                               paper_close, paper_exit_reason, paper_open,
                               round_trip_cost, tau_remaining)
from src.income.model import MarginModel
from src.pm_us.fees import taker_fee


def test_tau():
    assert tau_remaining("cfb", 1, 900) == 1.0
    assert tau_remaining("cfb", 3, 450) == pytest.approx(0.375)
    assert tau_remaining("cfb", 5, 0) is None              # overtime
    assert tau_remaining("cfb", 0, 0) is None               # not started


def test_live_model_collapses_to_score_at_the_end():
    lm = live_model(-7.0, 15.0, margin_now=10, tau=0.0)
    assert lm.p_cover(-9.5) > 0.6 and lm.p_cover(-10.5) < 0.4   # margin ~10
    start = live_model(-7.0, 15.0, margin_now=0, tau=1.0)
    pre = MarginModel(-7.0, 15.0, key_numbers=False)
    assert start.p_cover(3.5) == pytest.approx(pre.p_cover(3.5), abs=1e-6)


def test_round_trip_is_about_four_cents_at_the_money():
    c = round_trip_cost(0.50, 0.50, 0.49, 0.51)
    assert c == pytest.approx(2 * taker_fee(0.50) + 0.01 + 0.01)


def test_tracker_needs_cooldown_persistence_and_a_still_market():
    tr = Tracker(persist_s=60, cooldown_s=100)
    g = "asc-cfb-a-b-2026-09-26"
    tr.observe_score(g, (7, 0), now=0)
    # a 10c gap (fair 0.70 vs ask 0.60): clears ~4c costs + 1c buffer
    assert tr.signal(g, 3.5, 0.70, 0.58, 0.60, now=50) is None     # cooldown
    assert tr.signal(g, 3.5, 0.70, 0.58, 0.60, now=110) is None    # starts clock
    assert tr.signal(g, 3.5, 0.70, 0.58, 0.60, now=150) is None    # 40s < 60s
    assert tr.signal(g, 3.5, 0.70, 0.58, 0.60, now=175) == "buy"
    tr.observe_score(g, (14, 0), now=176)                          # a score resets it
    assert tr.signal(g, 3.5, 0.70, 0.58, 0.60, now=200) is None
    tr2 = Tracker(persist_s=60, cooldown_s=0)
    tr2.observe_score(g, (0, 0), now=0)
    tr2.signal(g, 3.5, 0.70, 0.58, 0.60, now=10)
    assert tr2.signal(g, 3.5, 0.72, 0.61, 0.63, now=80) is None    # market moving


def test_small_gap_never_signals():
    tr = Tracker(persist_s=0, cooldown_s=0)
    tr.observe_score("g", (0, 0), now=0)
    for t in range(1, 5):
        assert tr.signal("g", 0.5, 0.63, 0.58, 0.60, now=t) is None   # 3c < costs


def test_paper_round_trip_pays_both_fees():
    paper = {}
    pos = paper_open(paper, "g", 3.5, "buy", 0.58, 0.60, 0.70, now=0)
    assert paper_open(paper, "g", 3.5, "buy", 0.58, 0.60, 0.70, now=1) is None
    assert paper_exit_reason(pos, 0.70, 0.72, 0.70, tau=0.5, now=10, state="in") == "converged"
    pnl = paper_close(pos, 0.70, 0.72)
    assert pnl == pytest.approx(0.695 - 0.605 - taker_fee(0.605) - taker_fee(0.695))
    assert paper_exit_reason(pos, 0.60, 0.62, 0.70, tau=0.01, now=10, state="in") == "end_of_game"
    assert paper_exit_reason(pos, 0.52, 0.54, 0.70, tau=0.5, now=10, state="in") == "stop"
    assert paper_exit_reason(pos, 0.62, 0.64, 0.70, tau=0.5, now=901, state="in") == "time_stop"


def test_go_rule_demands_both_halves():
    good = [(f"g{i % 8}", 0.01 + (i % 3) * 0.001, i) for i in range(60)]
    assert go_status(good)[0]
    front_loaded = [(f"g{i % 8}", 0.05 if i < 30 else -0.01, i) for i in range(60)]
    assert not go_status(front_loaded)[0]
    assert not go_status(good[:GO_RULE["min_trips"] - 1])[0]


# ---- the loop against a fake venue -------------------------------------------

def test_inplay_arb_trades_live_and_divergence_never_does(tmp_path, monkeypatch):
    import datetime
    import income_bot
    from src.income import state as st
    from tests.fakes import FakeLines, FakeVenue
    from tests.test_execution import make_bot
    G = f"asc-cfb-aaa-bbb-{datetime.date.today().isoformat()}"   # live today
    v = FakeVenue()
    v.set_book(f"{G}-neg-1pt5", 0.60, 0.62)        # violation: bid(-1.5) > ask(+0.5)
    v.set_book(f"{G}-pos-0pt5", 0.50, 0.52)
    v.set_book(f"{G}-pos-3pt5", 0.97, 0.98)        # monotone, but far above fair
    info = {"state": "in", "model": MarginModel(0.0, 15.0), "margin": None,
            "margin_now": 3, "score": ("10", "7"), "period": 2, "clock": 300,
            "league": "cfb"}
    bot = make_bot(tmp_path, v, FakeLines({G: info}),
                   ["--live", "--strategies", "inplay_arb,divergence", "--poll", "0"])
    monkeypatch.setattr(income_bot, "moneyline_inventory", lambda c: [])
    bot.inplay_loop(minutes=0.001)
    arb = [o for o in v.placed if "pt5" in o["marketSlug"]]
    assert {o["marketSlug"][-8:] for o in arb} == {"neg-1pt5", "pos-0pt5"}
    assert not any(o["marketSlug"].endswith("pos-3pt5") for o in v.placed)
    b = st.book(bot.s, G)
    assert b["pos"]["-1.5"] == -b["pos"]["0.5"]      # levelled pair


def test_divergence_opens_and_closes_on_paper_only(tmp_path):
    import time
    from src.income.inplay import Divergence, Tracker, live_model
    from tests.fakes import FakeLines, FakeVenue
    from tests.test_execution import make_bot
    G = "asc-cfb-aaa-bbb-2026-09-26"
    lm = live_model(0.0, 15.0, margin_now=3, tau=300 / 3600 + 0.5)
    v = FakeVenue()
    ks = {}
    for k in (-6.5, -3.5, -1.5, 0.5, 2.5, 5.5):
        f = lm.p_cover(k)
        slug = f"{G}-{'pos' if k > 0 else 'neg'}-{int(abs(k))}pt5"
        ks[k] = slug
        v.set_book(slug, round(f - 0.01, 3), round(f + 0.01, 3))
    f0 = lm.p_cover(0.5)
    v.set_book(ks[0.5], round(f0 - 0.14, 3), round(f0 - 0.12, 3))    # 12c cheap
    info = {"state": "in", "model": MarginModel(0.0, 15.0), "margin": None,
            "margin_now": 3, "score": ("10", "7"), "period": 2, "clock": 300,
            "league": "cfb"}
    bot = make_bot(tmp_path, v, FakeLines({G: info}),
                   ["--live", "--strategies", "divergence"])
    now = time.time()
    bot.tracker = Tracker()
    bot.tracker.last_score[G] = (("10", "7"), now - 1000)
    mid = round(f0 - 0.13, 3)
    bot.tracker.state[(G, 0.5)] = Divergence(1, now - 1000, mid)
    bot.inplay_game(G, "ladder", ks, info, now)
    assert list(bot.s["paper"]) == [f"{G}|0.5"]
    assert v.placed == []                              # paper means paper
    v.set_book(ks[0.5], round(f0 - 0.005, 3), round(f0 + 0.015, 3))   # converged
    bot.inplay_game(G, "ladder", ks, info, now + 30)
    assert bot.s["paper"] == {}
    import json
    closes = [json.loads(l) for l in open(bot.store.ledger) if '"paper_close"' in l]
    assert closes[0]["reason"] == "converged" and closes[0]["pnl"] > 0
