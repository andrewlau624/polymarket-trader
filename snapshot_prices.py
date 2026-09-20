"""Record every game market's price daily, so calibration can be measured HERE.

The venue has one arbitrage structure - the CFB ladder - and 743 games that are
each a single two-sided market with nothing to relate them to. For those, the
only possible edge is DIRECTIONAL, and a directional edge needs no structure at
all: just a price that differs from the realised frequency.

Esports shows exactly that bias: favourites at 0.75-0.90 beat their price by
+0.107. Whether it exists on THIS venue has never been measured, and it cannot
be measured from history because the venue exposes settlement prices but not
the prices markets traded at beforehand.

So collect it forward. One snapshot a day of every game market, joined to
settlements once the games resolve, gives a calibration curve for Polymarket US
in a few weeks. If the bias is here, NBA is a nightly directional strategy that
needs no ladder - which is the only route to a 24/7 book on this venue.

    python snapshot_prices.py                # one snapshot of every game
    python snapshot_prices.py --settle       # fill in results for old snapshots
    python snapshot_prices.py --calibrate    # the curve, once there is data

Cron it:
    17 8 * * *  cd ~/polymarket-trader && make snapshot >> snapshot.log 2>&1
"""

import argparse
import collections
import json
import os
import re
import time
from datetime import datetime, timezone

LOG = os.path.join("research", "price_snapshots.jsonl")
GAME_RE = re.compile(r"^(?P<fam>[a-z]+-[a-z0-9]+)-.+-(?P<date>\d{4}-\d{2}-\d{2})$")
LADDER_RE = re.compile(r"-(?:pos|neg)-\d+(?:pt\d*)?$")


def is_game(slug):
    return bool(GAME_RE.match(slug or "")) and not LADDER_RE.search(slug or "")


def load(n=20000):
    """Recent records only. Slurping the whole file grew without bound."""
    from src.pm_us.jsonlog import tail_records
    return tail_records(LOG, n=n)


def write(recs):
    from src.pm_us.jsonlog import append
    for r in recs:
        append(LOG, r)


def snapshot(c, pause=0.35, limit=400):
    rows = {}
    for params in ({}, {"limit": 500}, {"limit": 1000}):
        try:
            for m in c.markets(**params):
                sl = m.get("marketSlug") or m.get("slug")
                if sl and is_game(sl):
                    rows[sl] = m
        except Exception:
            pass
        time.sleep(0.3)
    print(f"{len(rows)} game markets")
    out, ts = [], datetime.now(timezone.utc).isoformat()
    for i, (sl, m) in enumerate(list(rows.items())[:limit]):
        try:
            b, a, _s = c.book_levels(sl)
        except Exception:
            time.sleep(1.0)
            continue
        if not b or not a:
            continue
        mid = (b[0][0] + a[0][0]) / 2
        out.append({"ts": ts, "slug": sl, "bid": b[0][0], "ask": a[0][0],
                    "mid": round(mid, 4), "bid_sz": b[0][1], "ask_sz": a[0][1],
                    "closed": bool(m.get("closed")),
                    "question": (m.get("question") or "")[:90]})
        if i % 50 == 0:
            print(f"  {i}/{min(len(rows), limit)}", end="\r", flush=True)
        time.sleep(pause)
    print(f"\nsnapshotted {len(out)} two-sided markets")
    return out


def settle(c, recs, pause=0.3):
    """Attach outcomes to snapshots whose market has since closed."""
    have = {r["slug"] for r in recs if r.get("kind") == "settle"}
    want = {r["slug"] for r in recs if r.get("kind") != "settle"} - have
    rows = {}
    for params in ({"limit": 1000},):
        try:
            for m in c.markets(**params):
                sl = m.get("marketSlug") or m.get("slug")
                if sl in want:
                    rows[sl] = m
        except Exception:
            pass
    out = []
    for sl, m in rows.items():
        if not m.get("closed"):
            continue
        sides = m.get("marketSides") or []
        prices = []
        for sd in sides:
            try:
                prices.append(float(sd.get("price")))
            except (TypeError, ValueError):
                pass
        if len(prices) != 2 or max(prices) < 0.95:
            continue
        # marketSides[0] is the leg the book's price refers to
        out.append({"kind": "settle", "slug": sl, "result": float(prices[0] >= 0.95),
                    "ts": datetime.now(timezone.utc).isoformat()})
    print(f"resolved {len(out)} markets")
    return out


def calibrate(recs, edges=(0.05, 0.25, 0.45, 0.60, 0.75, 0.90, 0.98)):
    import numpy as np
    res = {r["slug"]: r["result"] for r in recs if r.get("kind") == "settle"}
    snaps = collections.defaultdict(list)
    for r in recs:
        if r.get("kind") != "settle" and r["slug"] in res:
            snaps[r["slug"]].append(r)
    obs = []
    for sl, rows in snaps.items():
        rows.sort(key=lambda r: r["ts"])
        obs.append((rows[0]["mid"], res[sl]))     # earliest price per market
    if len(obs) < 30:
        print(f"only {len(obs)} resolved observations - keep snapshotting. "
              f"~200 is where the buckets start meaning anything.")
        return
    p = np.array([o[0] for o in obs]); w = np.array([o[1] for o in obs])
    print(f"\n{len(obs)} resolved markets on THIS venue")
    print(f"{'bucket':>12} {'n':>5} {'price':>7} {'won':>6} {'edge':>8}")
    for lo, hi in zip(edges[:-1], edges[1:]):
        k = (p >= lo) & (p < hi)
        if k.sum() < 10:
            continue
        print(f"{lo:.2f}-{hi:.2f}".rjust(12) + f" {k.sum():>5} {p[k].mean():>7.3f} "
              f"{w[k].mean():>6.3f} {w[k].mean() - p[k].mean():>+8.3f}")
    print("\n  edge = realised win rate - price. Positive at the top means")
    print("  favourites are underpriced HERE, which is a nightly NBA strategy")
    print("  that needs no ladder.")


def main():
    ap = argparse.ArgumentParser(description="Daily price snapshots for calibration.")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    recs = load()
    if args.calibrate:
        calibrate(recs)
        return

    from src.pm_us.client import UsClient
    c = UsClient()
    new = settle(c, recs) if args.settle else snapshot(c, limit=args.limit)
    write(new)
    c.close()
    # `recs + new` copied the entire list just to count it
    n_snap = sum(1 for r in recs if r.get("kind") != "settle") + \
        sum(1 for r in new if r.get("kind") != "settle")
    n_res = sum(1 for r in recs if r.get("kind") == "settle") + \
        sum(1 for r in new if r.get("kind") == "settle")
    print(f"log now holds {n_snap} snapshots and {n_res} settlements")


if __name__ == "__main__":
    main()
