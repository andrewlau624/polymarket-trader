"""Detect when the venue starts listing a NEW kind of market.

The monotonicity arb needs a strike LADDER. Today the only ladders are college
football, which plays Thursday to Saturday - so capital turns over weekly, and
no amount of uptime changes that.

What would change it is the venue listing ladders on a sport that plays every
night. Basketball and hockey start in late October, baseball's postseason runs
through it, and all three are spread/handicap sports. Whether this venue lists
ladders for them is unknown and will simply appear one day.

So instead of guessing, watch for it. Each run records the family inventory and
reports anything that was not there before.

    python watch_families.py            # snapshot, diff against history
    python watch_families.py --history  # what has ever been seen

Pair it with cron for a standing answer:
    7 9 * * *  cd ~/polymarket-trader && make families-watch >> families.log 2>&1
"""

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone

from run_crossmarket import family_of
from run_ladder import parse_strike

LOG = os.path.join("research", "families.jsonl")


def snapshot(c):
    slugs = sorted({p["slug"] for p in c.all_programs() if p.get("slug")})
    try:
        for m in c.markets():
            sl = m.get("marketSlug") or m.get("slug")
            if sl:
                slugs.append(sl)
    except Exception:
        pass
    fam = Counter()
    for sl in set(slugs):
        f, sp = family_of(sl)
        _b, k = parse_strike(sl)
        fam[f"{f}-{sp}-{'ladder' if k is not None else 'outright'}"] += 1
    return dict(fam), len(set(slugs))


def history():
    seen = {}
    if not os.path.exists(LOG):
        return seen
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name, n in (r.get("families") or {}).items():
            if name not in seen:
                seen[name] = (r.get("ts", "?"), n)
    return seen


def main():
    ap = argparse.ArgumentParser(description="Watch for new market families.")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    past = history()
    if args.history:
        if not past:
            raise SystemExit(f"nothing recorded yet in {LOG}")
        print(f"families ever seen ({len(past)}):")
        for name, (ts, n) in sorted(past.items()):
            print(f"  {name:<28} first seen {ts[:10]}  ({n} markets)")
        return

    from src.pm_us.client import UsClient
    c = UsClient()
    fam, total = snapshot(c)
    c.close()

    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                             "markets": total, "families": fam}) + "\n")

    print(f"{total} markets, {len(fam)} families")
    new = [k for k in fam if k not in past]
    for name, n in sorted(fam.items(), key=lambda kv: -kv[1]):
        tag = "  <-- NEW" if name in new else ""
        print(f"  {name:<28} {n:>6}{tag}")

    ladders = {k: v for k, v in fam.items() if k.endswith("-ladder")}
    print(f"\nladder families: {', '.join(sorted(ladders)) or 'none'}")
    new_ladders = [k for k in new if k.endswith("-ladder")]
    if new_ladders:
        print(f"\n*** NEW LADDER FAMILY: {', '.join(new_ladders)} ***")
        print("A ladder on a sport that plays nightly is the one thing that")
        print("turns this from a weekly strategy into a daily one. Scan it.")
    elif not new:
        print("nothing new since the last run.")
    print("\nSports that would matter, and when they start:")
    print("  NBA / NHL   late October   nightly, spread & puckline")
    print("  MLB         postseason through October, then April")
    print("  soccer      year round, handicap markets")
    print("  esports     not listed on this venue at all")


if __name__ == "__main__":
    main()
