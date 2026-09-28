"""LoL recorder: slug roles, side mapping, feed-decided props, series arb, report."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import lol_recorder as rec
import lol_report as rep
from src.esports import lolrec as L
from src.esports.noarb import payoff


def test_parse_every_slug_shape_the_venue_uses():
    P = lambda s: L.parse(s)[3]
    assert L.parse("aec-lol-fly-sr-2026-09-25")[:3] == ("lol-fly-sr-2026-09-25", "fly", "sr")
    assert P("aec-lol-fly-sr-2026-09-25") == ("match",)
    assert P("astatc-lol-fly-sr-2026-09-25-game3") == ("map", 3)
    assert P("tsc-lol-fly-sr-2026-09-25-tot-2pt5") == ("over", 2.5)
    assert P("asc-lol-fly-sr-2026-09-25-hcap-neg-1pt5") == ("hcap", -1.5)
    assert P("astatc-lol-fly-sr-2026-09-25-g1fb-sr") == ("fb", 1, "sr")
    assert P("tsc-lol-fly-sr-2026-09-25-g2tk-24pt5") == ("kills", 2, 24.5)
    assert P("astatc-lol-fly-sr-2026-09-25-g2oe") == ("odd", 2)
    assert L.parse("aec-cs2-gl-k27-2026-09-25") is None


def test_hcap_payoff():
    assert payoff("hcap", -1.5, "AA") == 1.0          # 2-0 covers -1.5
    assert payoff("hcap", -1.5, "ABA") == 0.0
    assert payoff("hcap", 1.5, "ABB") == 1.0          # +1.5 survives a 1-2 loss
    assert payoff("hcap", 1.5, "BB") == 0.0


def test_feed_state_is_from_team_a_whichever_side_it_plays():
    fr = {"rfc460Timestamp": "2026-09-28T10:10:00.000Z", "gameState": "in_game",
          "blueTeam": {"totalGold": 30000, "totalKills": 5, "towers": 2, "dragons": ["x"],
                       "barons": 0, "inhibitors": 0},
          "redTeam": {"totalGold": 33000, "totalKills": 9, "towers": 4, "dragons": [],
                      "barons": 1, "inhibitors": 0}}
    meta = {"blueTeamMetadata": {"esportsTeamId": "1"}, "redTeamMetadata": {"esportsTeamId": "2"}}
    now = datetime(2026, 9, 28, 10, 10, 30, tzinfo=timezone.utc)
    a_red = L.state_for(fr, meta, "2", "2026-09-28T09:50:00.000Z", now)
    assert a_red["gold"] == 3000 and a_red["kills"] == [9, 5] and a_red["barons"] == 1
    assert a_red["minute"] == 20.0 and a_red["delay_s"] == 30.0
    a_blue = L.state_for(fr, meta, "1", "2026-09-28T09:50:00.000Z", now)
    assert a_blue["gold"] == -3000 and a_blue["dragons"] == 1
    assert L.model_p(a_red) > 0.5 > L.model_p(a_blue)


def test_props_the_feed_decides():
    st = {"kills": [14, 11], "state": "in_game"}
    assert L.decided(("kills", 1, 24.5), st, "fly", None) == 1.0
    assert L.decided(("kills", 1, 25.5), st, "fly", None) is None
    assert L.decided(("kills", 1, 25.5), {**st, "state": "finished"}, "fly", None) == 0.0
    assert L.decided(("fb", 1, "sr"), st, "fly", "sr") == 1.0
    assert L.decided(("fb", 1, "fly"), st, "fly", "sr") == 0.0
    assert L.decided(("fb", 1, "fly"), st, "fly", None) is None


def test_b_side_market_is_restated_on_a():
    assert rec.flipped([0.30, 10, 0.34, 7, "MARKET_STATE_OPEN"]) == \
        [0.66, 7, 0.70, 10, "MARKET_STATE_OPEN"]


def test_series_arb_is_net_of_fees():
    roles = [("match", None), ("map", 1), ("map", 2), ("over", 2.5)]
    fair = [(0.60, 50, 0.61, 50), (0.55, 50, 0.56, 50), (0.55, 50, 0.56, 50),
            (0.49, 50, 0.50, 50)]
    assert L.arb(roles, fair, 3, 0, 0) is None
    # after A takes game 1: A to win game 2 IS "A wins 2-0" = NOT over 2.5.
    # Game 2 at 0.40 ask while under (1 - over bid 0.50) is worth 0.50: free money.
    after = [(0.80, 50, 0.81, 50), (0.40, 50, 0.40, 50), (0.49, 50, 0.50, 50)]
    res = L.arb([("match", None), ("map", 2), ("over", 2.5)], after, 3, 1, 0)
    assert res and res["profit_net"] > 0


def test_venue_events_groups_markets_by_series():
    evs = rec.venue_events(["aec-lol-fly-sr-2026-09-25", "astatc-lol-fly-sr-2026-09-25-game1",
                            "aec-lol-t1-geng-2026-09-01", "aec-nfl-kc-lv-2026-09-25"],
                           {"2026-09-25"})
    assert list(evs) == ["lol-fly-sr-2026-09-25"]
    assert set(evs["lol-fly-sr-2026-09-25"]["markets"]) == {("match",), ("map", 1)}


def test_pair_event_by_team_code():
    d = {"teams": [{"id": "9", "code": "sr", "name": "Shopify Rebellion", "wins": 0},
                   {"id": "8", "code": "fly", "name": "FlyQuest", "wins": 1}]}
    got, a, b = L.pair_event("fly", "sr", [d])
    assert a["name"] == "FlyQuest" and b["id"] == "9"
    assert L.pair_event("t1", "geng", [d]) is None


def _obs(tmp_path):
    """A synthetic game: the feed swings to A at t=100 s, the market follows
    30 s later; kills pass 24.5 at t=200 s and the over stays at 0.60 until t=206 s."""
    t0 = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    rows = []
    for i in range(0, 400, 3):
        p = 0.50 if i < 100 else 0.70
        m = 0.50 if i < 130 else 0.70
        kills_q = [0.58, 20, 0.60, 30, "MARKET_STATE_OPEN"] if i < 209 else \
            [0.97, 20, 0.99, 30, "MARKET_STATE_OPEN"]
        rows.append({"ts": (t0 + timedelta(seconds=i)).isoformat(), "event": "lol-a-b-x",
                     "game": 1, "wins": [0, 0], "best_of": 3, "A": "a",
                     "feed": {"delay_s": 25.0, "kills": [13, 12] if i >= 200 else [10, 10]},
                     "model_p": p,
                     "q": {"map:1": [round(m - 0.01, 3), 50, round(m + 0.01, 3), 50,
                                     "MARKET_STATE_OPEN"],
                           "kills:1:24.5": kills_q},
                     "decided": {"kills:1:24.5": 1.0} if i >= 200 else {}, "arb": None})
    fp = tmp_path / "obs.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in rows))
    return str(fp), rows


def test_report_finds_the_stale_prop_the_lag_and_the_swing(tmp_path, capsys):
    fp, rows = _obs(tmp_path)
    recs = list(rep.iter_records(fp))
    rep.latency(recs)
    rep.leadlag(recs)
    trades, games = rep.swings(recs)
    out = capsys.readouterr().out
    assert "winner offered at <= 0.95 when the feed decided it: 1" in out
    assert "stayed stale median 6s" in out          # polls at 200, 203, 206
    assert "market LAGS the feed" in out
    assert trades and trades[0][60] > 0                 # bought 0.51, sold 0.69
