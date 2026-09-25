"""State, fills, hedging, settlement, and the bot cycle against a fake venue."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.income import state as st
from src.income.execution import Executor
from src.income.model import MarginModel
from src.pm_us.fees import maker_rebate, taker_fee
from tests.fakes import FakeLines, FakeVenue

G = "asc-cfb-aaa-bbb-2026-09-26"
S1, S2 = f"{G}-neg-1pt5", f"{G}-pos-0pt5"


@pytest.fixture
def env(tmp_path):
    store = st.Store(str(tmp_path / "state.json"), str(tmp_path / "ledger.jsonl"))
    s = store.load(start_equity=20)
    v = FakeVenue()
    ex = Executor(v, store, s, live=True, log=lambda *a: None)
    return store, s, v, ex


def ledger(store, kind=None):
    rows = [json.loads(l) for l in open(store.ledger)] if os.path.exists(store.ledger) else []
    return [r for r in rows if kind is None or r["kind"] == kind]


# ---- state -----------------------------------------------------------------

def test_atomic_save_and_corrupt_quarantine(tmp_path):
    store = st.Store(str(tmp_path / "s.json"), str(tmp_path / "l.jsonl"))
    s = store.load()
    s["orders"]["x"] = {"oid": "x"}
    store.save(s)
    assert store.load()["orders"]["x"]["oid"] == "x"
    open(store.path, "w").write('{"orders": {"x"')       # truncated mid-write
    with pytest.raises(st.CorruptState):
        store.load()
    assert not os.path.exists(store.path)
    assert any(".corrupt-" in f for f in os.listdir(tmp_path))


def test_apply_fill_idempotent_with_fee_signs(env):
    store, s, v, ex = env
    o = {"oid": "a", "game": G, "line": -1.5, "side": "sell", "px": 0.60,
         "qty": 5, "filled": 0, "maker": True}
    assert st.apply_fill(s, store, o, 3) == 3
    assert st.apply_fill(s, store, o, 3) == 0
    b = st.book(s, G)
    assert b["pos"]["-1.5"] == -3
    assert b["cash"] == pytest.approx(0.60 * 3 + maker_rebate(0.60, 3))
    t = {"oid": "b", "game": G, "line": 0.5, "side": "buy", "px": 0.52,
         "qty": 3, "filled": 0, "maker": False}
    st.apply_fill(s, store, t, 3)
    assert b["cash"] == pytest.approx(0.60 * 3 + maker_rebate(0.60, 3)
                                      - 0.52 * 3 - taker_fee(0.52, 3))


# ---- the phantom fill ------------------------------------------------------

def test_unreadable_order_books_nothing(env):
    store, s, v, ex = env
    v.set_book(S1, 0.50, 0.60)
    rec = ex.place(G, -1.5, S1, "sell", 0.599, 5, True, "rest_hedge")
    v.orders[rec["oid"]]["state"] = "ORDER_STATE_CANCELED"   # vanished
    v.fail_order_reads = True
    ex.reconcile()
    assert st.book(s, G)["pos"] == {}                 # not booked as filled
    assert s["orders"][rec["oid"]]["status"] == "open"


# ---- rest-hedge lifecycle --------------------------------------------------

def rest_group(ex, v):
    v.set_book(S1, 0.50, 0.60)
    v.set_book(S2, 0.53, 0.55)
    gid = ex.new_group("rest_hedge", G,
                       {"line": -1.5, "slug": S1, "side": "sell", "oids": []},
                       {"line": 0.5, "slug": S2, "side": "buy", "oids": []},
                       {"credit": 0.04, "hedge_px": 0.55, "min_credit": 0.0})
    rec = ex.place(G, -1.5, S1, "sell", 0.599, 5, True, "rest_hedge", group=gid)
    ex.s["groups"][gid]["leader"]["oids"].append(rec["oid"])
    return gid, rec


def test_partial_rest_fill_is_hedged_exactly(env):
    store, s, v, ex = env
    gid, rec = rest_group(ex, v)
    v.fill(rec["oid"], 3)
    ex.manage_groups()
    b = st.book(s, G)
    assert b["pos"] == {"-1.5": -3, "0.5": 3}
    assert gid in s["groups"]                          # leader still resting
    v.fill(rec["oid"], 2)
    ex.manage_groups()
    assert b["pos"] == {"-1.5": -5, "0.5": 5}
    assert gid not in s["groups"]
    done = ledger(store, "pair_done")[0]
    assert done["shares"] == 5 and done["credit_net"] > 0


def test_hedge_waits_on_slippage_then_forces(env):
    store, s, v, ex = env
    gid, rec = rest_group(ex, v)
    v.set_book(S2, 0.58, 0.60)                         # hedge ran 5c away
    v.fill(rec["oid"], 5)
    ex.manage_groups()
    assert st.book(s, G)["pos"].get("0.5", 0) == 0     # waited
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    s["groups"][gid]["naked_since"] = old
    ex.manage_groups()
    assert st.book(s, G)["pos"]["0.5"] == 5            # forced after max_naked_min


def test_suspended_book_is_no_price(env):
    """Liberty-Coastal: the book listed a 0.53 ask while the market was not
    matching, and six hedge IOCs at it got nothing."""
    store, s, v, ex = env
    gid, rec = rest_group(ex, v)
    v.states = {S2: "MARKET_STATE_SUSPENDED"}
    assert ex.quotes({0.5: S2}, [0.5]) == {}
    v.fill(rec["oid"], 5)
    ex.manage_groups(force_games={G})
    assert st.book(s, G)["pos"].get("0.5", 0) == 0     # no order sent into it
    assert not [o for o in v.orders.values() if o["marketSlug"] == S2]
    v.states = {}
    ex.manage_groups(force_games={G})
    assert st.book(s, G)["pos"]["0.5"] == 5            # hedged once it reopens


def test_resting_leader_pulled_when_hedge_moves(env):
    store, s, v, ex = env
    gid, rec = rest_group(ex, v)
    v.set_book(S2, 0.61, 0.63)
    ex.manage_groups()
    assert v.orders[rec["oid"]]["state"] == "ORDER_STATE_CANCELED"
    assert gid not in s["groups"]                      # nothing filled, closed
    assert ledger(store, "pull")


def test_taker_arb_short_leg_is_levelled(env):
    store, s, v, ex = env
    v.set_book(S1, 0.60, 0.62, bid_sz=5)
    v.set_book(S2, 0.50, 0.52, ask_sz=3)                # only 3 to buy
    gid = ex.new_group("taker_arb", G,
                       {"line": -1.5, "slug": S1, "side": "sell", "oids": []},
                       {"line": 0.5, "slug": S2, "side": "buy", "oids": []},
                       {"credit": 0.05, "hedge_px": 0.52})
    rec = ex.place(G, -1.5, S1, "sell", 0.60, 5, False, "taker_arb", group=gid)
    s["groups"][gid]["leader"]["oids"].append(rec["oid"])
    ex.manage_groups()
    assert st.book(s, G)["pos"] == {"-1.5": -5, "0.5": 3}   # 2 still naked
    v.set_book(S2, 0.50, 0.52, ask_sz=10)
    ex.manage_groups()
    assert st.book(s, G)["pos"] == {"-1.5": -5, "0.5": 5}


# ---- write-ahead -----------------------------------------------------------

def test_orphan_intent_freezes_game(env):
    store, s, v, ex = env
    st.new_intent(s, game=G, line=0.5, slug=S2, side="buy", px=0.52, qty=3,
                  maker=False, strat="value")
    ex.reconcile()
    assert G in s["suspect_games"] and not s["intents"]


def test_intent_recovered_from_open_orders(env):
    store, s, v, ex = env
    v.set_book(S1, 0.50, 0.60)
    r = v.place(S1, "sell", 0.599, 5, maker=True)          # sent, id never saved
    st.new_intent(s, game=G, line=-1.5, slug=S1, side="sell", px=0.599, qty=5,
                  maker=True, strat="rest_hedge")
    ex.reconcile()
    assert r["id"] in s["orders"]


# ---- settlement ------------------------------------------------------------

def test_settle_pays_the_pair_between(env):
    store, s, v, ex = env
    b = st.book(s, G)
    b["pos"] = {"-1.5": -5, "0.5": 5}
    b["cash"] = 5 * 0.08
    assert ex.settle(G, 1) == pytest.approx(5 + 0.40)      # -0.5 < 1 <= 1.5
    assert s["realized"] == pytest.approx(5.40)


# ---- bot cycle -------------------------------------------------------------

def make_bot(tmp_path, v, lines, argv):
    import income_bot
    a = income_bot.parse(argv + ["--state", str(tmp_path / "s.json"),
                                 "--ledger", str(tmp_path / "l.jsonl")])
    store = st.Store(a.state, a.ledger)
    s = store.load(start_equity=a.capital)
    bot = income_bot.Bot(a, v, store, s, lines, say=lambda *x: None)
    bot.ex.throttle = None
    return bot


def test_bot_value_take_respects_game_loss_limit(tmp_path, monkeypatch):
    import income_bot
    monkeypatch.setattr(income_bot, "within", lambda b, d: True)
    v = FakeVenue()
    m = MarginModel(0.0, 14.0)
    for k in (-9.5, -6.5, -2.5, 0.5, 6.5, 9.5):     # a ladder that agrees with m
        f = m.p_cover(k)
        slug = f"{G}-{'pos' if k > 0 else 'neg'}-{int(abs(k))}pt5"
        v.set_book(slug, round(f - 0.01, 3), round(f + 0.01, 3))
    L = f"{G}-pos-3pt5"
    v.set_book(L, 0.40, 0.42, ask_sz=500)           # fair ~0.61: mispriced
    lines = FakeLines({G: {"state": "pre", "model": m, "provider": "t",
                           "margin": None}})
    bot = make_bot(tmp_path, v, lines,
                   ["--live", "--capital", "50", "--strategies", "value",
                    "--max-order", "100", "--kelly", "1", "--max-game-loss", "2"])
    bot.manage()
    bot.scan()
    b = st.book(bot.s, G)
    from src.income.risk import worst_case
    assert b["pos"].get("3.5", 0) > 0
    assert worst_case(st.book_pos(b), b["cash"]) >= -2.0


def test_bot_dry_run_places_nothing(tmp_path, monkeypatch):
    import income_bot
    monkeypatch.setattr(income_bot, "within", lambda b, d: True)
    v = FakeVenue()
    v.set_book(S1, 0.60, 0.62)
    v.set_book(S2, 0.50, 0.52)
    pre = {"state": "pre", "model": None, "margin": None}
    bot = make_bot(tmp_path, v, FakeLines({G: pre}), ["--strategies", "taker_arb"])
    bot.manage()
    bot.scan()
    assert v.placed == []
    assert any(r["kind"] == "dry_order" for r in
               (json.loads(l) for l in open(bot.store.ledger)))


def test_bot_settles_finished_game(tmp_path):
    v = FakeVenue()
    lines = FakeLines({G: {"state": "post", "model": None, "margin": -3}})
    bot = make_bot(tmp_path, v, lines, ["--live"])
    b = st.book(bot.s, G)
    b["pos"], b["cash"] = {"3.5": 4}, -4 * 0.55
    bot.manage()
    assert G in bot.s["settled"]
    assert bot.s["realized"] == pytest.approx(4 - 2.2)     # -3 > -3.5 covers


# ---- regressions from the adversarial review -------------------------------

def test_flatten_does_not_feed_on_itself(env):
    store, s, v, ex = env
    v.set_book(S1, 0.60, 0.62, bid_sz=50)
    v.set_book(S2, 0.50, 0.52, ask_sz=50)
    gid = ex.new_group("taker_arb", G,
                       {"line": -1.5, "slug": S1, "side": "sell", "oids": []},
                       {"line": 0.5, "slug": S2, "side": "buy", "oids": []},
                       {"credit": 0.05, "hedge_px": 0.52})
    lead = ex.place(G, -1.5, S1, "sell", 0.60, 5, False, "taker_arb", group=gid)
    over = ex.place(G, 0.5, S2, "buy", 0.52, 10, False, "hedge", group=gid)
    s["groups"][gid]["leader"]["oids"].append(lead["oid"])
    s["groups"][gid]["follower"]["oids"].append(over["oid"])
    for _ in range(4):
        ex.manage_groups()
    assert st.book(s, G)["pos"] == {"-1.5": -5, "0.5": 5}


def test_pending_hedge_is_not_hedged_again(env):
    store, s, v, ex = env
    gid, rec = rest_group(ex, v)
    v.fill(rec["oid"], 5)
    real_place = v.place

    def resting_ioc(*a, **kw):                  # venue leaves the IOC working
        r = real_place(*a, **kw)
        o = v.orders[r["id"]]
        o["cumQuantity"], o["state"] = 0, "ORDER_STATE_NEW"
        return r
    v.place = resting_ioc
    ex.manage_groups()
    ex.manage_groups()
    hedges = [o for o in v.placed if o["marketSlug"] == S2]
    assert len(hedges) == 2                     # one, cancelled, then one retry
    assert all(o["state"] != "ORDER_STATE_NEW" for o in hedges[:-1])


def test_timeout_keeps_intent_and_freezes_game(env):
    store, s, v, ex = env
    v.set_book(S2, 0.50, 0.52)
    real_place = v.place

    def timeout(*a, **kw):
        real_place(*a, **kw)                    # the venue DID take it
        raise TimeoutError("read timed out")
    v.place = timeout
    assert ex.place(G, 0.5, S2, "buy", 0.52, 3, False, "value") is None
    assert ex.last_uncertain and ex.is_suspect(G) and s["intents"]


def test_recovered_leader_rejoins_its_group(env):
    store, s, v, ex = env
    v.set_book(S1, 0.50, 0.60)
    v.set_book(S2, 0.53, 0.55)
    gid = ex.new_group("rest_hedge", G,
                       {"line": -1.5, "slug": S1, "side": "sell", "oids": []},
                       {"line": 0.5, "slug": S2, "side": "buy", "oids": []},
                       {"credit": 0.04, "hedge_px": 0.55, "min_credit": 0.0})
    r = v.place(S1, "sell", 0.599, 5, maker=True)    # crash before the id was saved
    st.new_intent(s, game=G, line=-1.5, slug=S1, side="sell", px=0.599, qty=5,
                  maker=True, strat="rest_hedge", group=gid, leg="leader")
    ex.reconcile()
    assert r["id"] in s["groups"][gid]["leader"]["oids"]
    v.fill(r["id"], 5)
    ex.manage_groups()
    assert st.book(s, G)["pos"] == {"-1.5": -5, "0.5": 5}


def test_intent_recovery_skips_tracked_orders(env):
    store, s, v, ex = env
    v.set_book(S1, 0.50, 0.60)
    rec = ex.place(G, -1.5, S1, "sell", 0.599, 5, True, "rest_hedge")
    v.fill(rec["oid"], 3)
    ex.reconcile()
    st.new_intent(s, game=G, line=-1.5, slug=S1, side="sell", px=0.599, qty=5,
                  maker=True, strat="rest_hedge")
    ex.reconcile()
    assert st.book(s, G)["pos"] == {"-1.5": -3}


def test_partial_fills_priced_from_cumulative_notional(env):
    store, s, v, ex = env
    o = {"oid": "a", "game": G, "line": 0.5, "side": "buy", "px": 0.50,
         "qty": 10, "filled": 0, "maker": False}
    st.apply_fill(s, store, o, 5, 0.40)
    st.apply_fill(s, store, o, 10, 0.45)         # second 5 at 0.50
    want = -(0.40 * 5 + taker_fee(0.40, 5)) - (0.50 * 5 + taker_fee(0.50, 5))
    assert st.book(s, G)["cash"] == pytest.approx(want)


def test_resting_orders_count_against_game_limit(tmp_path, monkeypatch):
    v = FakeVenue()
    v.set_book(S1, 0.50, 0.60)
    bot = make_bot(tmp_path, v, FakeLines(), ["--live", "--max-game-loss", "2"])
    bot.ex.place(G, -1.5, S1, "sell", 0.599, 3, True, "rest_hedge")   # -1.20 if filled
    assert not bot.risk_ok(G, [(-1.5, "sell", 0.599, 2, 0.0)], quiet=True)  # -2.01
    assert bot.risk_ok(G, [(-1.5, "sell", 0.599, 1, 0.0)], quiet=True)      # -1.60


def test_unmatched_game_gets_no_new_risk(tmp_path, monkeypatch):
    import income_bot
    monkeypatch.setattr(income_bot, "within", lambda b, d: True)
    v = FakeVenue()
    v.set_book(S1, 0.50, 0.60)
    v.set_book(S2, 0.53, 0.55)
    bot = make_bot(tmp_path, v, FakeLines(), ["--live", "--strategies", "rest_hedge"])
    bot.scan()
    assert v.placed == []


def test_decided_game_settles_despite_open_group(tmp_path):
    v = FakeVenue()
    bot = make_bot(tmp_path, v, FakeLines({G: {"state": "post", "model": None,
                                                "margin": 3}}), ["--live"])
    ex = bot.ex
    gid, rec = rest_group(ex, v)
    v.fill(rec["oid"], 5)
    v.books.clear()                              # books gone after the whistle
    bot.manage()
    assert G in bot.s["settled"] and not bot.s["groups"]
    assert [o for o in v.placed if o["marketSlug"] == S2] == []   # never hedged


def test_postponed_game_is_not_settled(tmp_path):
    v = FakeVenue()
    bot = make_bot(tmp_path, v, FakeLines({G: {"state": "post", "model": None,
                                                "margin": None}}), ["--live"])
    b = st.book(bot.s, G)
    b["pos"], b["cash"] = {"3.5": 4}, -2.2
    bot.manage()
    assert G not in bot.s["settled"]
