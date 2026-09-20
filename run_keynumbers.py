"""Football margins are lumpy. A smooth pricing model is wrong at every lump.

A ladder of spread markets on one game is a discretised CDF of the margin of
victory. Two adjacent strikes isolate an exact margin:

    P(margin > k-0.5) - P(margin > k+0.5)  =  P(margin == k)

That is a two-leg position - long one strike, short the next - and unlike
YES/NO on a single market it is NOT pinned to $1, so it has real structure to
trade. What makes it interesting is that football margins are not smooth:
scores are built from 3s and 7s, so the distribution spikes on key numbers.

Anyone pricing the ladder off a smooth curve (a normal around the spread)
underprices those spikes and overprices the gaps between them. This script
measures the size of that error from historical finals.

    python fetch_scores.py --league nfl --from 2012 --to 2025
    python run_keynumbers.py --league nfl

CAVEAT on contract semantics: a strike written "5pt" may mean "wins by more
than 5" or "wins by 5 or more", which differ by exactly the point mass at 5 -
the thing being traded. Confirm against the venue's own rules before sizing.
"""

import argparse
import math
import os

import numpy as np
import pandas as pd

SCORES = os.path.join("data", "scores_{league}.csv")


def normal_pmf(k, mu, sd):
    """P(margin == k) under a smooth normal, via the half-point interval."""
    f = lambda x: 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))
    return f(k + 0.5) - f(k - 0.5)


def main():
    ap = argparse.ArgumentParser(description="Key-number structure in football margins.")
    ap.add_argument("--league", default="nfl")
    ap.add_argument("--cost", type=float, default=0.02,
                    help="round-trip cost in probability units for the two legs")
    ap.add_argument("--top", type=int, default=14)
    args = ap.parse_args()

    path = SCORES.format(league=args.league)
    if not os.path.exists(path):
        raise SystemExit(f"{path} missing - run fetch_scores.py --league {args.league}")
    df = pd.read_csv(path)
    m = df["margin"].abs()                   # margin of victory, winner's view
    m = m[m > 0]                             # drop ties
    n = len(m)
    mu, sd = float(m.mean()), float(m.std(ddof=1))
    print(f"{args.league.upper()}: {n:,} games, {df.season.min()}-{df.season.max()}  "
          f"mean margin {mu:.2f}  sd {sd:.2f}")

    counts = m.value_counts().sort_index()
    print(f"\n{'margin':>7} {'games':>7} {'actual':>8} {'smooth':>8} {'ratio':>7} "
          f"{'edge/$1':>9} {'net of cost':>12}")
    rows = []
    for k in range(1, args.top + 1):
        c = int(counts.get(k, 0))
        act = c / n
        smo = normal_pmf(k, mu, sd)
        if smo <= 0:
            continue
        edge = act - smo
        rows.append((k, c, act, smo, act / smo, edge, edge - args.cost))
    for k, c, act, smo, ratio, edge, net in rows:
        star = " *" if abs(ratio - 1) > 0.35 else ""
        print(f"{k:>7} {c:>7,} {act:>8.4f} {smo:>8.4f} {ratio:>7.2f}x "
              f"{edge:>+9.4f} {net:>+12.4f}{star}")

    print(f"\n  actual  = P(margin == k) from {n:,} historical finals")
    print(f"  smooth  = what a normal({mu:.1f}, {sd:.1f}) would say")
    print(f"  edge/$1 = what you make per $1 staked buying the k-0.5/k+0.5 vertical")
    print(f"            from a counterparty pricing it smoothly, before cost")
    print(f"  * = the smooth model is off by more than 35%")

    keys = [k for k, _c, _a, _s, r, _e, _n in rows if r > 1.35]
    gaps = [k for k, _c, _a, _s, r, _e, _n in rows if r < 0.65]
    print(f"\n  UNDERPRICED by a smooth model (buy the exact-margin vertical): {keys}")
    print(f"  OVERPRICED  by a smooth model (sell it):                        {gaps}")
    tot = sum(e for _k, _c, _a, _s, _r, e, _n in rows if e > 0)
    print(f"\n  total probability mass a smooth model misplaces: {tot:.3f}")
    print("  That is the size of the prize IF the venue prices smoothly.")
    print("  It is also the most basic concept in football handicapping, so")
    print("  assume a real bookmaker does not. The open question is this venue.")


if __name__ == "__main__":
    main()
