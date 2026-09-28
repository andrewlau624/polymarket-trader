"""Underdogs: buy 5-20% dogs pre-game, sell if they reach 40/60/80% in-game?

    python dog_backtest.py                  # cfb + nfl, 2025 and 2026 so far
    python dog_backtest.py --league nfl --seasons 2025

Two questions, answered on the same games:

  step 1  Buy EVERY pre-game underdog priced 5-20%. Hold, or sell the first
          time it reaches 40 / 60 / 80%. Does any exit pay after fees?
  step 2  Buy only when a season-stats rating (src/income/ratings.py) says the
          dog is at least 5 points better than its price. Do those picks beat
          step 1's? If not, the model adds nothing.

Data, all ESPN, free: DraftKings' pre-game moneyline (de-vigged) as the price,
ESPN's play-by-play win probability as the in-game price path, final scores
for ratings. Ratings for a game use ONLY games from earlier weeks.

What this cannot tell you, so the forward paper test still has to:
  * DraftKings is not Polymarket US. Entry is the de-vigged price + 1c (a
    venue ask above fair); --slip changes it.
  * ESPN's win probability is a model, not a bid. A target counts as hit only
    if TWO consecutive plays reach it, and the sale is booked 1c below it.
"""

import argparse
import json
import math
import os
import random
import time

import requests

from run_bookline import devig
from src.income import ratings as R
from src.pm_us.fees import taker_fee

SITE = "https://site.api.espn.com/apis/site/v2/sports"
PATHS = {"nfl": "football/nfl", "cfb": "football/college-football"}
WEEKS = {"nfl": 18, "cfb": 15}
CACHE = os.path.join("data", "espn")
LO, HI = 0.05, 0.20
TARGETS = (0.40, 0.60, 0.80)
EDGE = 0.05


def _get(url, params, pause=0.2):
    for i in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                time.sleep(pause)
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def season_events(league, season, final=True):
    """Completed regular-season games: id, week, date, teams, scores."""
    os.makedirs(CACHE, exist_ok=True)
    out = []
    for week in range(1, WEEKS[league] + 1):
        fp = os.path.join(CACHE, f"{league}_{season}_w{week}.json")
        if final and os.path.exists(fp):
            out += json.load(open(fp))
            continue
        q = {"dates": season, "seasontype": 2, "week": week, "limit": 400}
        if league == "cfb":
            q["groups"] = 80
        js = _get(f"{SITE}/{PATHS[league]}/scoreboard", q) or {}
        rows = []
        for e in js.get("events", []):
            c = (e.get("competitions") or [{}])[0]
            st = (e.get("status") or {}).get("type", {})
            comp = {x.get("homeAway"): x for x in c.get("competitors") or []}
            if not st.get("completed") or set(comp) != {"home", "away"}:
                continue
            try:
                hs, as_ = int(comp["home"]["score"]), int(comp["away"]["score"])
            except (TypeError, ValueError, KeyError):
                continue
            rows.append({"id": e["id"], "week": week, "date": e.get("date"),
                         "home": comp["home"]["team"].get("abbreviation"),
                         "away": comp["away"]["team"].get("abbreviation"),
                         "hs": hs, "as": as_, "neutral": bool(c.get("neutralSite"))})
        if final and rows:
            json.dump(rows, open(fp, "w"))
        out += rows
    return out


def summary(league, eid):
    """{ml_home, ml_away, wp: [home win prob per play]} from ESPN, cached."""
    d = os.path.join(CACHE, "sum")
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, f"{eid}.json")
    if os.path.exists(fp):
        return json.load(open(fp))
    js = _get(f"{SITE}/{PATHS[league]}/summary", {"event": eid})
    if js is None:
        return None
    pc = (js.get("pickcenter") or [{}])[0] if js.get("pickcenter") else {}
    rec = {"ml_home": (pc.get("homeTeamOdds") or {}).get("moneyLine"),
           "ml_away": (pc.get("awayTeamOdds") or {}).get("moneyLine"),
           "provider": (pc.get("provider") or {}).get("name"),
           "wp": [w.get("homeWinPercentage") for w in js.get("winprobability") or []]}
    json.dump(rec, open(fp, "w"))
    return rec


def games_of(events):
    return [(e["home"], e["away"], e["hs"] - e["as"], e["neutral"]) for e in events]


def season_final(league, season):
    """Final ratings of a completed season, itself fitted on a carried prior."""
    prior = None
    for s in (season - 1, season):
        ev = season_events(league, s)
        r, _ = R.fit(games_of(ev), prior, league=league)
        prior = R.carry(r)
    return r


def measure_sigma(league, seasons):
    """Spread of real margins around the walk-forward prediction."""
    res = []
    for s in seasons:
        prior = R.carry(season_final(league, s - 1))
        ev = season_events(league, s)
        for w in range(2, WEEKS[league] + 1):
            past = [e for e in ev if e["week"] < w]
            now = [e for e in ev if e["week"] == w]
            if not now:
                continue
            r, hfa = R.fit(games_of(past), prior, league=league)
            for e in now:
                if e["home"] in r and e["away"] in r:
                    mu = r[e["home"]] - r[e["away"]] + (0 if e["neutral"] else hfa)
                    res.append(e["hs"] - e["as"] - mu)
    return math.sqrt(sum(x * x for x in res) / len(res)), len(res)


def hit(path, t, need=2):
    run = 0
    for p in path:
        run = run + 1 if p is not None and p >= t else 0
        if run >= need:
            return True
    return False


def dogs(league, season, sigma, slip, final):
    prior = R.carry(season_final(league, season - 1))
    ev = season_events(league, season, final=final)
    out = []
    for w in sorted({e["week"] for e in ev}):
        past = [e for e in ev if e["week"] < w]
        r, hfa = R.fit(games_of(past), prior, league=league)
        for e in [e for e in ev if e["week"] == w]:
            sm = summary(league, e["id"])
            if not sm:
                continue
            ph, pa = devig(sm["ml_home"], sm["ml_away"])
            if ph is None:
                continue
            for dog, p, home_dog in ((e["home"], ph, True), (e["away"], pa, False)):
                if not LO <= p <= HI:
                    continue
                mh = R.win_prob(r, hfa, e["home"], e["away"], e["neutral"], league, sigma)
                model = None if mh is None else (mh if home_dog else 1 - mh)
                won = (e["hs"] > e["as"]) == home_dog
                path = [(x if home_dog else 1 - x) for x in sm["wp"] if x is not None]
                entry = min(p + slip, 0.99)
                row = {"league": league, "season": season, "week": w, "id": e["id"],
                       "dog": dog, "p": round(p, 4), "entry": round(entry, 4),
                       "model": None if model is None else round(model, 4),
                       "won": int(won), "wp_n": len(path),
                       "max_wp": round(max(path), 4) if path else None}
                row["hold"] = round((1.0 if won else 0.0) - entry - taker_fee(entry), 5)
                for t in TARGETS:
                    sell = t - 0.01
                    row[f"x{t}"] = round(sell - entry - taker_fee(entry) - taker_fee(sell), 5) \
                        if path and hit(path, t) else row["hold"]
                out.append(row)
    return out


def ci(vals, reps=4000, seed=11):
    rnd = random.Random(seed)
    n = len(vals)
    ms = sorted(sum(rnd.choice(vals) for _ in range(n)) / n for _ in range(reps))
    return ms[int(0.025 * reps)], ms[int(0.975 * reps)]


def report(title, rows):
    print(f"\n  {title}: n={len(rows)}")
    if len(rows) < 5:
        return
    n = len(rows)
    ent = sum(r["entry"] for r in rows) / n
    win = sum(r["won"] for r in rows) / n
    print(f"    avg entry {ent:.3f}  win rate {win:.3f}  edge {win - ent:+.3f}")
    for k in ["hold"] + [f"x{t}" for t in TARGETS]:
        v = [r[k] for r in rows]
        lo, hi = ci(v)
        hits = "" if k == "hold" else \
            f"   reached {sum(1 for r in rows if r[k] != r['hold'] or (r['max_wp'] or 0) >= float(k[1:])) / n:.0%}"
        print(f"    {k:<6} {sum(v) / n:+.4f}/share  95% CI [{lo:+.4f}, {hi:+.4f}]{hits}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", default="cfb,nfl")
    ap.add_argument("--seasons", default="2025,2026")
    ap.add_argument("--slip", type=float, default=0.01, help="entry above de-vigged price")
    ap.add_argument("--out", default=os.path.join("data", "dog_backtest.json"))
    a = ap.parse_args()
    this_year = time.gmtime().tm_year
    rows = []
    for lg in a.league.split(","):
        sigma, n = measure_sigma(lg, (2022, 2023, 2024))
        print(f"{lg}: rating sigma {sigma:.1f} pts (walk-forward, 2022-24, {n} games)")
        for s in (int(x) for x in a.seasons.split(",")):
            got = dogs(lg, s, sigma, a.slip, final=s < this_year)
            print(f"  {lg} {s}: {len(got)} dogs priced {LO:.0%}-{HI:.0%}")
            rows += got
    json.dump(rows, open(a.out, "w"))

    print("\n== STEP 1: every underdog, no model ==")
    report("all", rows)
    for lg in a.league.split(","):
        report(lg, [r for r in rows if r["league"] == lg])

    rated = [r for r in rows if r["model"] is not None]
    print("\n== STEP 2: season-stats model ==")
    if rated:
        # is the model even in the market's neighbourhood?
        bs_m = sum((r["model"] - r["won"]) ** 2 for r in rated) / len(rated)
        bs_p = sum((r["p"] - r["won"]) ** 2 for r in rated) / len(rated)
        print(f"  Brier on these dogs: model {bs_m:.4f} vs market {bs_p:.4f} "
              f"({'model worse' if bs_m > bs_p else 'model better'}; lower is better)")
        print(f"  mean model {sum(r['model'] for r in rated) / len(rated):.3f} vs "
              f"mean price {sum(r['p'] for r in rated) / len(rated):.3f} vs "
              f"win rate {sum(r['won'] for r in rated) / len(rated):.3f}")
    picks = [r for r in rated if r["model"] - r["entry"] >= EDGE]
    rest = [r for r in rated if r["model"] - r["entry"] < EDGE]
    report(f"model picks (model >= entry + {EDGE:.2f})", picks)
    report("model passes", rest)


if __name__ == "__main__":
    main()
