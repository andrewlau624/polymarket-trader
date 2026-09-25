"""Favourites paper tracker: band, both sides, marks, grading, listing safety."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.income import favs
from src.income import state as st
from src.pm_us.fees import taker_fee

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
SOON = (NOW + timedelta(hours=30)).isoformat().replace("+00:00", "Z")


def row(slug, bid, ask, t="moneyline", start=SOON):
    return {"slug": slug, "marketType": t, "gameStartTime": start,
            "bestBidQuote": {"value": f"{bid:.4f}"}, "bestAskQuote": {"value": f"{ask:.4f}"}}


def test_quote_row_skips_futures_and_one_sided_books():
    assert favs.quote_row(row("a", 0.90, 0.91)) == ["moneyline", SOON, 0.90, 0.91]
    assert favs.quote_row(row("a", 0.90, 0.91, t="futures")) is None
    assert favs.quote_row({"slug": "a", "marketType": "props",
                           "bestAskQuote": {"value": "0.9"}}) is None


def test_in_band_finds_the_favourite_on_either_side():
    assert favs.in_band(0.91, 0.92) == [("fav", "long", 0.92)]
    assert favs.in_band(0.08, 0.09) == [("fav", "short", 0.92)]   # other side at 1 - bid
    assert favs.in_band(0.50, 0.51) == []
    assert favs.in_band(0.80, 0.92) == []                      # 12c wide: not a price


def test_longshots_are_their_own_band_with_multiple_targets():
    assert favs.in_band(0.01, 0.02) == [("long", "long", 0.02)]
    assert favs.in_band(0.98, 0.985) == [("long", "short", 0.02)]
    assert favs.targets_for("long", 0.02) == {"2x": 0.04, "5x": 0.1}
    d = favs.fresh()
    favs.screen(d, {"a": ["props", SOON, 0.01, 0.02]}, NOW)
    p = d["open"]["a|long|long"]
    favs.mark(d, {"a": ["props", SOON, 0.05, 0.06]}, NOW)
    assert set(p["hits"]) == {"2x"}
    r = favs.result(p, 0.0)
    assert r["x2x"] == pytest.approx(0.04 - 0.02 - taker_fee(0.02) - taker_fee(0.04), abs=1e-5)
    assert r["x5x"] == r["hold"] == pytest.approx(-0.02 - taker_fee(0.02), abs=1e-5)


def test_positions_from_before_bands_grade_as_favourites():
    old = {"side": "long", "px": 0.92, "hits": {"0.98": "t"}}     # no band / targets
    assert set(favs.result(old, 1.0)) == {"payout", "hold", "x0.97", "x0.98", "x0.99"}


def test_game_key_clusters_props_with_their_game():
    assert favs.game_key("astatc-ufc-rauros-raobar-2026-09-26-mov-f1-ko") == \
        favs.game_key("aec-ufc-rauros-raobar-2026-09-26") == "ufc-rauros-raobar-2026-09-26"


def test_screen_opens_once_and_only_before_the_game():
    d = favs.fresh()
    started = (NOW - timedelta(hours=1)).isoformat()
    far = (NOW + timedelta(days=30)).isoformat()
    q = {"a": ["moneyline", SOON, 0.91, 0.92], "b": ["props", started, 0.91, 0.92],
         "c": ["spreads", far, 0.91, 0.92], "e": ["totals", SOON, 0.40, 0.41]}
    assert favs.screen(d, q, NOW) == 1
    assert list(d["open"]) == ["a|long"]
    assert d["open"]["a|long"]["hours"] == pytest.approx(30.0)
    assert favs.screen(d, q, NOW) == 0                         # never re-entered


def test_marks_record_low_and_first_target_touch():
    d = favs.fresh()
    favs.screen(d, {"a": ["moneyline", SOON, 0.91, 0.92]}, NOW)
    favs.mark(d, {"a": ["moneyline", SOON, 0.86, 0.87]}, NOW)
    favs.mark(d, {"a": ["moneyline", SOON, 0.975, 0.98]}, NOW + timedelta(hours=1))
    favs.mark(d, {"a": ["moneyline", SOON, 0.99, 0.995]}, NOW + timedelta(hours=2))
    p = d["open"]["a|long"]
    assert p["low"] == 0.86
    assert set(p["hits"]) == {"0.97", "0.98", "0.99"}
    assert p["hits"]["0.98"] == (NOW + timedelta(hours=2)).isoformat()
    assert p["hits"]["0.97"] == (NOW + timedelta(hours=1)).isoformat()


def test_short_side_exit_is_one_minus_ask():
    d = favs.fresh()
    favs.screen(d, {"a": ["moneyline", SOON, 0.08, 0.09]}, NOW)
    favs.mark(d, {"a": ["moneyline", SOON, 0.01, 0.02]}, NOW)
    assert "0.98" in d["open"]["a|short"]["hits"]


def test_result_long_short_and_targets():
    pos = {"side": "long", "px": 0.92, "hits": {"0.97": "t"}}
    r = favs.result(pos, 0.0)                                  # upset
    assert r["hold"] == pytest.approx(-0.92 - taker_fee(0.92), abs=1e-5)
    assert r["x0.97"] == pytest.approx(0.05 - taker_fee(0.92) - taker_fee(0.97), abs=1e-5)
    assert r["x0.99"] == r["hold"]                             # never touched: held
    short = {"side": "short", "px": 0.92, "hits": {}}
    assert favs.result(short, 0.0)["payout"] == 1.0            # the other side won


def test_resolve_grades_gone_markets_and_retries_open_ones():
    d = favs.fresh()
    favs.screen(d, {"a": ["moneyline", SOON, 0.91, 0.92],
                    "b": ["moneyline", SOON, 0.91, 0.92]}, NOW)
    logs = []

    def settle(slug):
        if slug == "a":
            return {"slug": "a", "settlement": 1}
        raise RuntimeError("Settlement not found")             # still open
    n = favs.resolve(d, active=set(), settle_fn=settle, now=NOW,
                     log=lambda k, **r: logs.append((k, r)))
    assert n == 1 and list(d["open"]) == ["b|long"]
    assert logs[0][1]["hold"] == pytest.approx(0.08 - taker_fee(0.92), abs=1e-5)
    # an incomplete listing proves nothing about what closed
    assert favs.resolve(d, active=None, settle_fn=settle, now=NOW) == 0
    later = NOW + timedelta(days=favs.GIVE_UP_DAYS)
    favs.resolve(d, active=set(), settle_fn=settle, now=later,
                 log=lambda k, **r: logs.append((k, r)))
    assert not d["open"] and logs[-1][1]["reason"] == "unresolved"


def test_cycle_runs_once_per_listing(tmp_path):
    path, ledger = str(tmp_path / "f.json"), str(tmp_path / "f.jsonl")
    q = {"a": ["moneyline", SOON, 0.91, 0.92]}
    kw = dict(say=lambda *_: None, path=path, ledger=ledger, now=NOW)
    assert favs.cycle(100.0, q, {"a"}, lambda s: None, **kw) == (1, 0)
    assert favs.cycle(100.0, q, {"a"}, lambda s: None, **kw) is None   # same listing
    assert favs.cycle(200.0, {}, None, lambda s: None, **kw) is None   # failed listing
    recs = [json.loads(x) for x in open(ledger)]
    assert [r["kind"] for r in recs] == ["fav_open"]


def test_peek_book_does_not_open_a_book():
    s = st.fresh()
    assert st.peek_book(s, "g")["pos"] == {} and "g" not in s["books"]
    st.book(s, "g")
    assert st.open_books(s) == {}                              # empty books are not open
