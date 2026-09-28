"""Underdog tracker and season ratings."""

from datetime import datetime, timedelta, timezone

import pytest

from src.income import dogs, favs
from src.income import ratings as R

NOW = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)
KICK = (NOW + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
SLUG = "aec-cfb-wm-duke-2026-09-26"


class Lines:
    def __init__(self, info):
        self.info = info

    def game(self, slug, need_line=True):
        return self.info


def info(ref_is_home=False):
    # W&M (first token) away at Duke; book has Duke 85%
    return {"ref_is_home": ref_is_home, "home_abbr": "DUKE", "away_abbr": "WM",
            "neutral": False, "p_home": 0.85, "p_away": 0.15}


DOC = {"leagues": {"cfb": {"r": {"DUKE": 10.0, "WM": 0.0}, "hfa": 2.0, "sigma": 16.0}}}


def test_ratings_recover_a_simple_league():
    games = [("A", "B", 14, False), ("B", "C", 7, False), ("A", "C", 21, False)] * 10
    r, hfa = R.fit(games, prior={"A": 0, "B": 0, "C": 0}, ridge=0.1, league="nfl")
    # the only consistent solution: home field 0, A-B 14, B-C 7
    assert hfa == pytest.approx(0.0, abs=0.5)
    assert r["A"] - r["B"] == pytest.approx(14.0, abs=1.0)
    assert r["B"] - r["C"] == pytest.approx(7.0, abs=1.0)
    p = R.win_prob(r, hfa, "A", "C", neutral=True, league="nfl", sigma=13.5)
    assert 0.9 < p < 0.97
    assert R.win_prob(r, hfa, "A", "ZZZ", league="nfl") is None


def test_ridge_pulls_thin_teams_to_their_prior():
    r, _ = R.fit([("A", "B", 40, True)], prior={"A": 0.0, "B": 0.0}, league="nfl")
    assert r["A"] - r["B"] < 20                   # one blowout is not a 40-point gap


def test_screen_records_model_and_book_for_the_dog():
    d = favs.fresh()
    q = {SLUG: ["moneyline", KICK, 0.14, 0.15]}           # W&M (long side) at 15c
    assert dogs.screen(d, q, Lines(info()), DOC, NOW) == 1
    p = d["open"][f"{SLUG}|long"]
    assert p["dog"] == "WM" and p["book"] == 0.15
    want = 1 - R.win_prob(DOC["leagues"]["cfb"]["r"], 2.0, "DUKE", "WM", sigma=16.0)
    assert p["model"] == pytest.approx(want, abs=1e-4)
    assert dogs.screen(d, q, Lines(info()), DOC, NOW) == 0     # once


def test_short_side_dog_is_the_other_team():
    d = favs.fresh()
    q = {SLUG: ["moneyline", KICK, 0.85, 0.86]}           # W&M favoured: Duke is the dog
    dogs.screen(d, q, Lines(info()), DOC, NOW)
    p = d["open"][f"{SLUG}|short"]
    assert p["dog"] == "DUKE" and p["book"] == 0.85 and p["px"] == pytest.approx(0.15)


def test_only_near_kickoff_cfb_nfl_moneylines():
    d = favs.fresh()
    far = (NOW + timedelta(hours=30)).isoformat()
    q = {SLUG: ["moneyline", far, 0.14, 0.15],
         "aec-mlb-sd-lad-2026-09-26": ["moneyline", KICK, 0.14, 0.15],
         "asc-cfb-wm-duke-2026-09-26-pos-3pt5": ["spreads", KICK, 0.14, 0.15]}
    assert dogs.screen(d, q, Lines(info()), DOC, NOW) == 0


def test_no_espn_match_still_counts_for_step_one():
    d = favs.fresh()
    dogs.screen(d, {SLUG: ["moneyline", KICK, 0.14, 0.15]}, Lines(None), DOC, NOW)
    p = d["open"][f"{SLUG}|long"]
    assert p["model"] is None and p["dog"] is None


def test_live_target_needs_consecutive_confirmed_polls():
    d = favs.fresh()
    dogs.screen(d, {SLUG: ["moneyline", KICK, 0.14, 0.15]}, Lines(info()), DOC, NOW)
    p = d["open"][f"{SLUG}|long"]
    for bid in (0.45, 0.45, 0.30, 0.45, 0.46):            # a dip resets the count
        dogs.mark_live(d, SLUG, {"bid": bid, "ask": bid + 0.01}, "t")
    assert "0.4" not in p["hits"]
    dogs.mark_live(d, SLUG, {"bid": 0.47, "ask": 0.48}, "t3")
    assert p["hits"]["0.4"] == "t3" and p["hit_px"]["0.4"] == 0.47
    dogs.mark_live(d, SLUG, None, "t4")                  # suspended book: no price
    assert p["run"]["0.6"] == 0
    r = favs.result(p, 0.0)                              # sold at 0.40, then lost
    assert r["x0.4"] > 0 > r["hold"] and r["x0.8"] == r["hold"]


def test_close_record_keeps_model_fields():
    d = favs.fresh()
    dogs.screen(d, {SLUG: ["moneyline", KICK, 0.14, 0.15]}, Lines(info()), DOC, NOW)
    logs = []
    favs.resolve(d, set(), lambda s: {"settlement": 1}, NOW,
                 log=lambda k, **r: logs.append(r))
    assert logs[0]["model"] is not None and logs[0]["dog"] == "WM" and logs[0]["payout"] == 1
