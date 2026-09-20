"""Trade the SHAPE of a spread ladder, not the outcome of the game.

A game's spread markets (…-pos-3pt5, …-neg-4pt, …-pos-7pt) are a discretised
CDF of the margin of victory. Two things can be wrong with it, and neither
requires predicting who wins:

  1. MONOTONICITY. P(margin > 7) can never exceed P(margin > 6). A violation
     is risk-free: buy the cheap high strike, sell the dear low one. The
     research graveyard buried this - on the GLOBAL venue. This venue is
     newer and thinner and has never been checked.

  2. KEY NUMBERS. Adjacent strikes isolate an exact margin:
     P(>k-0.5) - P(>k+0.5) = P(margin == k). Football margins spike on 3 and
     7 (NFL: 14.5% land on 3 against 2.9% under a smooth normal). A ladder
     priced off a smooth curve underprices those and overprices 9/11/12/13.

Both are two-leg positions - long one strike, short the next - which is the
"two opposing positions" idea in the one structure where it has real content.
YES/NO on a single market is pinned to $1 and cannot work; two strikes on a
ladder are not pinned to anything.

    python run_ladder.py --slug-prefix asc-cfb-clmsn-cah-2026-09-25
    python run_ladder.py --list

CONFIRM THE CONTRACT SEMANTICS FIRST. A strike written "5pt" may mean "wins
by more than 5" or "by 5 or more". Those differ by exactly the point mass at
5, which is the thing being traded. The script assumes strict '>' and says so
in every line it prints.
"""

import argparse
import os
import re
from collections import defaultdict

import pandas as pd

from src.pm_us.client import UsClient, px

STRIKE = re.compile(r"^(?P<base>.+?)-(?P<sign>pos|neg)-(?P<num>\d+)(?:pt(?P<frac>\d+)?)?$")
SCORES = os.path.join("data", "scores_{league}.csv")


def parse_strike(slug):
    """('asc-cfb-clmsn-cah-2026-09-25', +3.5) from '…-pos-3pt5'."""
    m = STRIKE.match(slug or "")
    if not m:
        return None, None
    num = float(m.group("num"))
    frac = m.group("frac")
    if frac:
        num += float(f"0.{frac}")
    return m.group("base"), (num if m.group("sign") == "pos" else -num)


def empirical_pmf(league):
    path = SCORES.format(league=league)
    if not os.path.exists(path):
        return None
    m = pd.read_csv(path)["margin"].abs()
    m = m[m > 0]
    return (m.value_counts() / len(m)).sort_index(), len(m)


def main():
    ap = argparse.ArgumentParser(description="Spread-ladder shape analysis.")
    ap.add_argument("--slug-prefix", default="", help="one game's ladder")
    ap.add_argument("--list", action="store_true", help="list games that have a ladder")
    ap.add_argument("--league", default="cfb", choices=("cfb", "nfl"))
    ap.add_argument("--min-strikes", type=int, default=3)
    ap.add_argument("--cost", type=float, default=0.02, help="round-trip, both legs")
    args = ap.parse_args()

    c = UsClient()
    try:
        progs = c.all_programs()
    except Exception as e:
        raise SystemExit(f"program list failed: {type(e).__name__} {e}")

    ladders = defaultdict(dict)
    for p in progs:
        base, k = parse_strike(p.get("slug"))
        if base is not None:
            ladders[base][k] = p["slug"]

    if args.list or not args.slug_prefix:
        print(f"{len(ladders)} games expose a spread ladder:")
        for base, ks in sorted(ladders.items(), key=lambda kv: -len(kv[1])):
            if len(ks) >= args.min_strikes:
                print(f"  {len(ks):>3} strikes  {base}   "
                      f"{sorted(ks)[:8]}{' …' if len(ks) > 8 else ''}")
        if not args.slug_prefix:
            print("\npass --slug-prefix <base> to analyse one.")
            return

    ks = ladders.get(args.slug_prefix)
    if not ks:
        raise SystemExit(f"no ladder found for {args.slug_prefix!r} (try --list)")

    print(f"\n{args.slug_prefix}: {len(ks)} strikes. Prices are mid; '>' semantics assumed.")
    quotes = {}
    for k in sorted(ks):
        try:
            bids, asks, _state = c.book_levels(ks[k])
        except Exception as e:
            print(f"  {k:>+7.1f}  book failed: {type(e).__name__}")
            continue
        b = bids[0][0] if bids else None
        a = asks[0][0] if asks else None
        mid = (b + a) / 2 if (b is not None and a is not None) else (b or a)
        quotes[k] = {"bid": b, "ask": a, "mid": mid}
        sp = (a - b) if (b is not None and a is not None) else float("nan")
        print(f"  {k:>+7.1f}  bid {str(b):>6}  ask {str(a):>6}  mid "
              f"{'n/a' if mid is None else f'{mid:.3f}'}  spread {sp:.3f}")

    live = {k: q["mid"] for k, q in quotes.items() if q["mid"] is not None}
    if len(live) < 2:
        raise SystemExit("\nnot enough two-sided strikes to compare.")

    # --- 1. monotonicity: P(> k) must fall as k rises -----------------------
    print("\n== MONOTONICITY (a violation is risk-free) ==")
    order = sorted(live)
    bad = 0
    for lo, hi in zip(order[:-1], order[1:]):
        if live[hi] > live[lo] + 1e-9:
            gap = live[hi] - live[lo]
            bad += 1
            print(f"  ! P(>{hi:+.1f})={live[hi]:.3f} EXCEEDS P(>{lo:+.1f})={live[lo]:.3f} "
                  f"by {gap:.3f}")
            print(f"    sell the {hi:+.1f}, buy the {lo:+.1f}: locks {gap:.3f}/share "
                  f"minus {args.cost:.3f} cost = {gap - args.cost:+.3f}")
    if not bad:
        print("  none - the ladder is internally consistent.")

    # --- 2. key numbers -----------------------------------------------------
    emp = empirical_pmf(args.league)
    if emp is None:
        print(f"\n(no data/scores_{args.league}.csv - run fetch_scores.py for the "
              f"key-number comparison)")
        return
    pmf, n = emp
    print(f"\n== KEY NUMBERS (vs {n:,} historical {args.league.upper()} finals) ==")
    print(f"  {'margin':>7} {'implied':>9} {'actual':>8} {'edge':>8} {'net':>8}  action")
    found = 0
    for lo, hi in zip(order[:-1], order[1:]):
        # a strike pair straddling exactly one integer isolates that margin
        span = [k for k in range(int(lo) - 1, int(hi) + 2) if lo < k < hi]
        if len(span) != 1:
            continue
        k = span[0]
        implied = live[lo] - live[hi]
        actual = float(pmf.get(abs(k), 0.0))
        edge = actual - implied
        net = abs(edge) - args.cost
        if net <= 0:
            continue
        found += 1
        act = "BUY the vertical" if edge > 0 else "SELL the vertical"
        print(f"  {k:>7} {implied:>9.3f} {actual:>8.3f} {edge:>+8.3f} {net:>+8.3f}  {act}")
    if not found:
        print("  no adjacent pair isolates a single margin with an edge over cost.")
    print("\n  implied = price(lower strike) - price(upper strike), i.e. the market's")
    print("  P(margin == k). actual = how often it really lands there. This is a")
    print("  relative-value trade between two strikes: it does not care who wins.")


if __name__ == "__main__":
    main()
