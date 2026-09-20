"""Read a log_edge.py log and answer: is there a latency or divergence edge?

    python analyze_lag.py research/us_edge_log.jsonl

Three sections, each of which can kill the idea on its own:

  FEED STALENESS - how old a play already is when the ESPN API hands it to us.
    This is the ceiling on everything. If plays arrive 20s after they happen,
    no amount of fast code beats a market watching the broadcast. Only plays
    discovered AFTER the logger started are counted; the first poll pulls the
    whole game's history and would otherwise look absurdly stale.

  REPRICING LAG - time from a scoring play to the first book move of >= 1 tick.
    Negative means the book moved before our feed delivered the play, i.e. we
    are structurally late and this feed cannot be traded.

  DIVERGENCE - market mid vs ESPN live win probability, and whether the gap
    predicts the next move. Reported with the sign convention inferred from
    the correlation, since a slug does not say which side YES is.
"""

import argparse
import json
import sys
from collections import defaultdict

import numpy as np

from src.pm_us.feed import iso_to_epoch

TICK = 0.01


def load(path):
    recs = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return recs


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def main():
    ap = argparse.ArgumentParser(description="Latency / divergence analysis.")
    ap.add_argument("log", nargs="?", default="research/us_edge_log.jsonl")
    ap.add_argument("--move", type=float, default=TICK,
                    help="book move counted as a reprice (default 1 tick)")
    ap.add_argument("--window", type=float, default=120.0,
                    help="seconds after a play to look for the reprice")
    args = ap.parse_args()

    recs = load(args.log)
    if not recs:
        sys.exit(f"no records in {args.log}")
    start = min(r["ts"] for r in recs)
    plays = [r for r in recs if r.get("kind") == "play"]
    books = [r for r in recs if r.get("kind") == "book"]
    print(f"{len(recs)} records | {len(plays)} plays | {len(books)} book samples | "
          f"{(max(r['ts'] for r in recs) - start) / 60:.1f} min")

    # ---- 1. feed staleness ------------------------------------------------
    fresh = []
    for p in plays:
        wc = iso_to_epoch(p.get("wallclock"))
        if wc is None or wc < start:      # historical backfill from the first poll
            continue
        fresh.append(p["ts"] - wc)
    print("\n== FEED STALENESS (how old a play is when we receive it) ==")
    if len(fresh) < 5:
        print(f"  only {len(fresh)} live-discovered plays - run longer during a game")
    else:
        a = np.array(fresh)
        print(f"  n={len(a)}  median {np.median(a):.1f}s  p25 {pct(a,25):.1f}s  "
              f"p75 {pct(a,75):.1f}s  p90 {pct(a,90):.1f}s  max {a.max():.1f}s")
        print(f"  (includes our poll interval; the floor is ESPN's own publish delay)")
        if np.median(a) > 15:
            print("  VERDICT: too stale to race the book. A market watching the")
            print("  broadcast sees this many seconds before we do.")

    if not books:
        print("\n(no book samples in this log - rerun without --espn-only for the rest)")
        return

    by_slug = defaultdict(list)
    for b in books:
        if b.get("bid") is not None and b.get("ask") is not None:
            by_slug[b["slug"]].append((b["ts"], (b["bid"] + b["ask"]) / 2.0,
                                       b["ask"] - b["bid"]))
    for s in by_slug:
        by_slug[s].sort()

    print("\n== BOOK COVERAGE ==")
    for s, rows in by_slug.items():
        sp = np.array([r[2] for r in rows])
        print(f"  {s[:44]:<44} {len(rows):>6} samples  median spread {np.median(sp):.3f}")

    # ---- 2. repricing lag -------------------------------------------------
    print("\n== REPRICING LAG after a scoring play ==")
    lags, misses = [], 0
    for p in plays:
        if not p.get("scoring"):
            continue
        wc = iso_to_epoch(p.get("wallclock"))
        rows = by_slug.get(p.get("slug")) or []
        if wc is None or not rows:
            continue
        base = [m for (t, m, _s) in rows if wc - 30 <= t <= wc]
        after = [(t, m) for (t, m, _s) in rows if wc < t <= wc + args.window]
        if not base or not after:
            continue
        ref = base[-1]
        hit = next((t for t, m in after if abs(m - ref) >= args.move), None)
        if hit is None:
            misses += 1
        else:
            lags.append(hit - wc)
    if lags:
        a = np.array(lags)
        print(f"  n={len(a)} scoring plays repriced ({misses} never moved {args.move:.3f})")
        print(f"  median {np.median(a):+.1f}s  p25 {pct(a,25):+.1f}s  p75 {pct(a,75):+.1f}s")
        print(f"  moved before our feed delivered the play: "
              f"{float((a < 0).mean()):.0%}")
        print("  A positive median LARGER than feed staleness is the only case where")
        print("  this feed could be raced. Compare the two numbers above.")
    else:
        print(f"  no scoring play had book coverage on both sides of it "
              f"({misses} with no qualifying move)")

    # ---- 3. divergence vs ESPN win probability ----------------------------
    print("\n== MARKET MID vs ESPN WIN PROBABILITY ==")
    for slug, rows in by_slug.items():
        wps = [(iso_to_epoch(p["wallclock"]), p["home_wp"]) for p in plays
               if p.get("slug") == slug and p.get("home_wp") is not None
               and iso_to_epoch(p.get("wallclock")) is not None]
        if len(wps) < 10:
            print(f"  {slug[:44]}: only {len(wps)} win-prob points")
            continue
        wps.sort()
        wt = np.array([w[0] for w in wps]); wv = np.array([w[1] for w in wps])
        bt = np.array([r[0] for r in rows]); bm = np.array([r[1] for r in rows])
        interp = np.interp(wt, bt, bm)
        corr = float(np.corrcoef(interp, wv)[0, 1]) if len(wt) > 2 else float("nan")
        side = "YES = home" if corr >= 0 else "YES = away"
        aligned = interp if corr >= 0 else 1.0 - interp
        gap = aligned - wv
        print(f"  {slug[:44]}")
        print(f"    corr {corr:+.2f} -> {side} | mean gap {gap.mean():+.3f}  "
              f"abs median {np.median(np.abs(gap)):.3f}  p90 {pct(np.abs(gap),90):.3f}")
        print(f"    a gap that is persistently larger than the spread is the")
        print(f"    tradeable version; a gap inside the spread is not.")


if __name__ == "__main__":
    main()
