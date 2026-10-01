"""Kalshi vs Polymarket US: pairing, sides, quotes, fees, persistence, grading.

Fixtures are copied from the live listings on 2026-09-30 (both venues' rules
text was read the same day; see TEST_PLAN.md)."""

import pytest

import xvenue_report as rep
from src.xvenue import core


def pm_side(desc, long, abbr, name, alias=None, safe=None, disp=None):
    return {"description": desc, "long": long,
            "team": {"abbreviation": abbr, "name": name, "alias": alias or desc,
                     "safeName": safe or name, "displayAbbreviation": disp or abbr.upper()}}


def pm_game(slug, sides, start="2026-10-06T00:15:00Z"):
    return {"slug": slug, "marketType": "moneyline", "gameStartTime": start, "marketSides": sides}


def k_mkt(ticker, title, yb="0.50", ya="0.51"):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "yes_sub_title": title,
            "yes_bid_dollars": yb, "yes_ask_dollars": ya, "yes_bid_size_fp": "100",
            "yes_ask_size_fp": "100", "status": "active"}


# Monday-night game: both venues name it by its ET date, 2026-10-05, though it
# starts 00:15Z on the 6th. Polymarket's LONG side is the away team (Falcons).
ATL_NO = pm_game("aec-nfl-atl-no-2026-10-05", [
    pm_side("Falcons", True, "atl", "Atlanta Falcons"),
    pm_side("Saints", False, "no", "New Orleans Saints")])
K_ATL_NO = [k_mkt("KXNFLGAME-26OCT05ATLNO-NO", "New Orleans"),
            k_mkt("KXNFLGAME-26OCT05ATLNO-ATL", "Atlanta")]


def test_kalshi_ticker_dates_and_mlb_time():
    assert core.kalshi_event("KXNFLGAME-26OCT05ATLNO-NO") == ("KXNFLGAME", "2026-10-05", "NO")
    assert core.kalshi_event("KXMLBGAME-26OCT011400PHIATL-PHI") == ("KXMLBGAME", "2026-10-01", "PHI")
    assert core.kalshi_event("KXNFLGAME-26XYZ05ATLNO-NO") is None


def test_pairing_follows_long_flag_not_ticker_order():
    g = core.pair_games([ATL_NO], K_ATL_NO)
    assert len(g) == 1
    g = g[0]
    assert g["long"] == "Falcons" and g["k_long"].endswith("-ATL")
    assert g["short"] == "Saints" and g["k_short"].endswith("-NO")
    assert g["date"] == "2026-10-05" and g["game"] == "KXNFLGAME-26OCT05ATLNO"


def test_pairing_by_name_when_codes_differ():
    # 'Oregon St.' vs 'Colorado State' shares the word 'state' - must not be ambiguous
    pm = pm_game("aec-cfb-oregst-colst-2026-10-03", [
        pm_side("Beavers", True, "oregst", "Oregon State", disp="ORST"),
        pm_side("Rams", False, "colst", "Colorado State", disp="CSU")])
    ks = [k_mkt("KXNCAAFGAME-26OCT03ORSTCSU-ORST", "Oregon St."),
          k_mkt("KXNCAAFGAME-26OCT03ORSTCSU-CSU", "Colorado St.")]
    g = core.pair_games([pm], ks)[0]
    assert g["k_long"].endswith("-ORST") and g["k_short"].endswith("-CSU")
    # UFC: Kalshi carries the fuller name
    pm = pm_game("aec-ufc-imarod-aldcor-2026-10-03", [
        pm_side("Imanol Rodriguez", True, "imarod", "Imanol Rodriguez", disp="Rodriguez"),
        pm_side("Alden Coria", False, "aldcor", "Alden Coria", disp="Coria")])
    ks = [k_mkt("KXUFCFIGHT-26OCT03RODCOR-ROD", "Imanol Rodriguez Pillado"),
          k_mkt("KXUFCFIGHT-26OCT03RODCOR-COR", "Alden Coria")]
    g = core.pair_games([pm], ks)[0]
    assert g["k_long"].endswith("-ROD") and g["k_short"].endswith("-COR")


def test_pairing_drops_wrong_date_and_ambiguous():
    moved = dict(ATL_NO, slug="aec-nfl-atl-no-2026-10-06")
    assert core.pair_games([moved], K_ATL_NO) == []
    twin = [dict(m, ticker=m["ticker"].replace("ATLNO", "ATLNOG2"),
                 event_ticker="KXNFLGAME-26OCT05ATLNOG2") for m in K_ATL_NO]
    assert core.pair_games([ATL_NO], K_ATL_NO + twin) == []      # doubleheader-style clash


def test_pm_quote_is_long_side_best_levels():
    book = {"marketData": {"state": "MARKET_STATE_OPEN",
                           "bids": [{"px": {"value": "0.43"}, "qty": "10"},
                                    {"px": {"value": "0.435"}, "qty": "5"}],
                           "offers": [{"px": {"value": "0.445"}, "qty": "7"},
                                      {"px": {"value": "0.44"}, "qty": "3"}]}}
    assert core.pm_quote(book) == [0.435, 5.0, 0.44, 3.0, "MARKET_STATE_OPEN"]


def test_k_quote_empty_sides():
    q = core.k_quote(k_mkt("X-26OCT05AB-A", "A", yb="0.0000", ya="1.0000"))
    assert q[0] is None and q[2] is None


def test_fees():
    assert core.k_fee(0.5, 1) == pytest.approx(0.02)            # 0.0175 rounds UP
    assert core.k_fee(0.5, 100) == pytest.approx(1.75)
    assert core.pm_fee(0.5, 1) == pytest.approx(0.017375)


def row(pm, kl, ks):
    return {"pm": pm, "kl": kl, "ks": ks}


def test_buys_routes_each_team_both_ways():
    r = row([0.40, 50, 0.42, 50, "MARKET_STATE_OPEN"],
            [0.44, 50, 0.45, 50, "active"], [0.55, 50, 0.57, 50, "active"])
    b = core.buys(r)
    assert ("pm", "long", 0.42, 50) in b["L"]
    assert ("pm", "short", 0.60, 50) in b["S"]                   # 1 - long bid
    assert ("k", "yes", 0.45, 50) in b["L"] and ("k", "no", 0.45, 50) in b["L"]
    assert ("k", "yes", 0.57, 50) in b["S"] and ("k", "no", 0.56, 50) in b["S"]


def test_locked_pair_net_after_fees():
    # long team 0.42 on Polymarket, other team 0.50 on Kalshi -> 0.92 + fees
    r = row([0.40, 50, 0.42, 50, "MARKET_STATE_OPEN"],
            [0.49, 50, 0.52, 50, "active"], [0.48, 50, 0.50, 50, "active"])
    best = {p["dir"]: p for p in core.locked_pairs(r)}["L@pm"]
    want = 1 - (0.42 + 0.50 + (core.pm_fee(0.42, 50) + core.k_fee(0.50, 50)) / 50)
    assert best["net"] == pytest.approx(want, abs=1e-5) and best["c"] == 50
    closed = dict(r, pm=[0.40, 50, 0.42, 50, "MARKET_STATE_CLOSED"])
    assert all("pm" not in str(p["legs"]) for p in core.locked_pairs(closed))


def test_cheap_entry_uses_other_venues_mid():
    # Kalshi mid for the long team 0.50 (spread 2c); Polymarket ask 0.47
    r = row([0.46, 50, 0.47, 50, "MARKET_STATE_OPEN"],
            [0.49, 50, 0.51, 50, "active"], [0.49, 50, 0.51, 50, "active"])
    e = core.cheap_entries(r)
    assert ("L", "pm", "long", 0.47, 0.5, 50) in e
    wide = dict(r, kl=[0.40, 50, 0.60, 50, "active"])            # 20c spread: no fair value
    assert not any(x[:2] == ("L", "pm") for x in core.cheap_entries(wide))


def test_s1_needs_two_consecutive_observations():
    arb = row([0.40, 50, 0.42, 50, "MARKET_STATE_OPEN"],
              [0.49, 50, 0.52, 50, "active"], [0.48, 50, 0.50, 50, "active"])
    flat = row([0.49, 50, 0.51, 50, "MARKET_STATE_OPEN"],
               [0.49, 50, 0.51, 50, "active"], [0.49, 50, 0.51, 50, "active"])
    mk = lambda t, r: dict(r, t=t, game="G")
    assert rep.s1_episodes([mk(0, arb), mk(60, flat)]) == []
    assert len(rep.s1_episodes([mk(0, arb), mk(60, arb)])) == 1
    assert rep.s1_episodes([mk(0, arb), mk(60 + rep.GAP_S + 1, arb)]) == []


def test_grade_sides():
    e = {"t": 0, "game": "G", "start": "s", "venue": "pm", "team": "S", "px": 0.40, "c": 10,
         "pm_slug": "x", "ticker": "T"}
    t = rep.grade(e, {"pm:x": 1.0})                                  # long won -> short loses
    assert t["pnl"] == pytest.approx(-0.40 - core.pm_fee(0.40))
    t = rep.grade(dict(e, team="L"), {"pm:x": 0.5})                  # NFL tie pays 0.50
    assert t["pnl"] == pytest.approx(0.10 - core.pm_fee(0.40))
    t = rep.grade(dict(e, venue="k"), {"k:T": 1.0})
    assert t["pnl"] == pytest.approx(0.60 - core.k_fee(0.40, 10) / 10)
