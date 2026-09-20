"""Read research/crossmarket.jsonl: where do two books disagree on one event?

A ladder's `neg-0pt5` strike and the game's outright moneyline settle on the
same condition - the team winning. A price gap between them is the same shape
of free money as a monotonicity violation, and unlike the ladder trade it is
not restricted to college football.

    python crossmarket_report.py
"""

import collections
import json
import os
import sys

LOG = os.path.join("research", "crossmarket.jsonl")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else LOG
    if not os.path.exists(path):
        print(f"no log at {path} - run `make crossmarket-bg` first")
        return
    recs = []
    from src.pm_us.jsonlog import tail_records
    recs = tail_records(path, n=4000)
    kinds = collections.Counter(r.get("kind", "?") for r in recs)
    print(f"{len(recs)} records: " + ", ".join(f"{k}({v})" for k, v in kinds.most_common()))

    fams = [r for r in recs if r.get("kind") == "families"]
    if fams:
        f = fams[-1]
        print(f"\n== MARKET FAMILIES (as of the last run, {f['markets']} markets) ==")
        for name, n in sorted(f.get("families", {}).items(), key=lambda kv: -kv[1]):
            print(f"  {name:<28} {n:>6}")
        if f.get("sub_periods"):
            print(f"  sub-period ladders: {f['sub_periods']}")

    pairs = [r for r in recs if r.get("kind") == "pair"]
    if not pairs:
        print("\nno game had both an outright and a ladder checked yet")
        return
    latest = {}
    for r in pairs:
        latest[r["game"]] = r
    good = [r for r in latest.values() if r.get("tradeable")]
    print(f"\n== {len(latest)} games compared, {len(good)} with a tradeable gap ==")
    if good:
        print(f"  {'edge':>7} {'sh':>7} {'$':>8}  {'side':<20} game")
    for r in sorted(good, key=lambda r: -abs(r["edge"]) * r.get("shares", 0)):
        sz = r.get("shares") or 0
        print(f"  {r['edge']:>+7.3f} {sz:>7.0f} {abs(r['edge']) * sz:>8.2f}  "
              f"{r.get('side', '?'):<20} {r['game'][8:]}")
    if good:
        tot = sum(abs(r["edge"]) * (r.get("shares") or 0) for r in good)
        print(f"\n  total ${tot:.2f} across {len(good)} games.")
        print("  Both legs settle on the same event, so the gap is locked if both")
        print("  fill. Depth here is much larger than the ladder arb's 10-25 shares,")
        print("  because an outright book is the venue's most liquid market.")
        print("  UNVERIFIED: that the moneyline references the SAME team as the")
        print("  ladder. A ~0.03 gap implies it does (opposite sides would differ")
        print("  by ~0.17), but confirm on one small fill before sizing.")


if __name__ == "__main__":
    main()
