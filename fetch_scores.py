"""Cache historical final scores from ESPN, for the margin distribution.

Free, keyless. Used by run_keynumbers.py to measure how often a game lands on
each exact margin - which is what a ladder of spread markets is really pricing.

    python fetch_scores.py --league nfl --from 2015 --to 2025
    python fetch_scores.py --league cfb --from 2018 --to 2025
"""

import argparse
import os
import time

import pandas as pd
import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports"
PATHS = {"nfl": "football/nfl", "cfb": "football/college-football"}
OUT = os.path.join("data", "scores_{league}.csv")


def week_scores(path, season, week, seasontype, groups=None):
    q = {"dates": season, "seasontype": seasontype, "week": week}
    if groups:
        q["groups"] = groups
    r = requests.get(f"{SITE}/{path}/scoreboard", params=q, timeout=30)
    r.raise_for_status()
    rows = []
    for e in r.json().get("events", []):
        if (e.get("status") or {}).get("type", {}).get("state") != "post":
            continue
        c = (e.get("competitions") or [{}])[0]
        comp = c.get("competitors") or []
        if len(comp) != 2:
            continue
        try:
            scores = {x.get("homeAway"): int(x.get("score")) for x in comp}
            names = {x.get("homeAway"): (x.get("team") or {}).get("abbreviation")
                     for x in comp}
        except (TypeError, ValueError):
            continue
        if "home" not in scores or "away" not in scores:
            continue
        rows.append({"season": season, "week": week, "date": e.get("date"),
                     "home": names["home"], "away": names["away"],
                     "home_score": scores["home"], "away_score": scores["away"],
                     "margin": scores["home"] - scores["away"],
                     "total": scores["home"] + scores["away"]})
    return rows


def main():
    ap = argparse.ArgumentParser(description="Cache historical final scores.")
    ap.add_argument("--league", default="nfl", choices=sorted(PATHS))
    ap.add_argument("--from", dest="y0", type=int, default=2015)
    ap.add_argument("--to", dest="y1", type=int, default=2025)
    ap.add_argument("--pause", type=float, default=0.25)
    args = ap.parse_args()

    path = PATHS[args.league]
    weeks = range(1, 19) if args.league == "nfl" else range(1, 17)
    groups = "80" if args.league == "cfb" else None   # FBS only
    rows = []
    for season in range(args.y0, args.y1 + 1):
        for st in (2, 3):                              # regular, postseason
            for w in (weeks if st == 2 else range(1, 6)):
                try:
                    got = week_scores(path, season, w, st, groups)
                except Exception as e:
                    print(f"  {season} st{st} wk{w}: {type(e).__name__}")
                    time.sleep(1.0)
                    continue
                rows.extend(got)
                time.sleep(args.pause)
        print(f"  {season}: {len(rows):,} games cumulative", end="\r", flush=True)
    df = pd.DataFrame(rows).drop_duplicates(subset=["date", "home", "away"])
    out = OUT.format(league=args.league)
    os.makedirs("data", exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nwrote {out}: {len(df):,} games, "
          f"{df.season.min()}-{df.season.max()}")


if __name__ == "__main__":
    main()
