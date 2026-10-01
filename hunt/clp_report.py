"""T5: Czech Liga Pro - Polymarket US vs bet365 (TEST_PLAN.md).

    .venv/bin/python hunt/clp_report.py

Czech Liga Pro market slugs carry the Sportradar id that OddsPapi uses as its
fixture id, so the niche recorder's Polymarket books join the bet365 snapshots
exactly. Player names come from the daily names files (or the full fixtures
dump). Sides are matched by player NAME, never by position.
"""

import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
from hunt.clp_odds import fair                                    # noqa: E402
from src import sim                                               # noqa: E402
from xvenue_recorder import PM, get                               # noqa: E402

EDGE, FEE = 0.04, 0.0695


def toks(name):
    return set(re.sub(r"[^a-z ]", " ", str(name).lower()).split()) - {"sr", "jr"}


def same(a, b):
    ta, tb = toks(a), toks(b)
    return bool(ta and tb) and len(ta & tb) >= min(2, len(ta), len(tb))


def epoch(s):
    return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone


def main():
    names = {}
    if os.path.exists("research/clp/fixtures.json"):
        names.update({f["fixtureId"]: (f.get("participant1Name"), f.get("participant2Name"))
                      for f in json.load(open("research/clp/fixtures.json"))})
    for fp in glob.glob("research/clp/names-*.json"):
        names.update({k: tuple(v) for k, v in json.load(open(fp)).items()})
    snaps = defaultdict(list)                          # OddsPapi fixture id -> [(t, p1, p2, n1, n2)]
    for fp in sorted(glob.glob("research/clp/odds-*.json")):
        t = int(os.path.basename(fp)[5:-5])
        d = json.load(open(fp))
        for f in d if isinstance(d, list) else []:
            pr = fair(f)
            if pr:
                snaps[f["fixtureId"]].append((t, *pr, *names.get(f["fixtureId"], (None, None))))
    # Czech Liga Pro market slugs ARE the Sportradar/OddsPapi fixture id:
    # 'aec-czechligapro-id2503634975164896' <-> fixtureId 'id2503634975164896'
    fid = lambda slug: slug.split("czechligapro-", 1)[1] if "czechligapro-id" in slug else None
    quotes = {}
    for fp in glob.glob("research/niche/rec-*.jsonl"):
        for line in open(fp):
            r = json.loads(line)
            if not fid(r["slug"]) or r["q"][4] != "MARKET_STATE_OPEN" or not r.get("start"):
                continue
            if r["t"] <= epoch(r["start"]) - 60 and (r["slug"] not in quotes or r["t"] > quotes[r["slug"]]["t"]):
                quotes[r["slug"]] = r
    rows = []
    for slug, q in quotes.items():
        ss = [s for s in snaps.get(fid(slug), []) if s[0] <= q["t"] and q["t"] - s[0] <= 4 * 3600]
        if not ss:
            continue
        t, p1, p2, n1, n2 = max(ss)
        long_name = q.get("long")
        if same(long_name, n1) and not same(long_name, n2):
            pl = p1
        elif same(long_name, n2) and not same(long_name, n1):
            pl = p2
        else:
            continue                                   # ambiguous: drop, never guess
        rows.append({"slug": slug, "t": q["t"], "fair_long": pl, "q": q["q"]})
    print(f"\nT5  CZECH LIGA PRO | Polymarket quotes {len(quotes)}, with a bet365 snapshot <= 4 h before: {len(rows)}")
    if not rows:
        return 0
    cache_fp = "research/clp/settle.json"
    try:
        cache = json.load(open(cache_fp))
    except (OSError, ValueError):
        cache = {}
    for r in rows:
        if cache.get(r["slug"]) is None:
            d = get(f"{PM}/markets/{r['slug']}/settlement")
            cache[r["slug"]] = None if not d else d.get("settlement")
            time.sleep(0.25)
    json.dump(cache, open(cache_fp, "w"))
    done = [r for r in rows if cache.get(r["slug"]) is not None]
    gaps = [r["fair_long"] - (r["q"][0] + r["q"][2]) / 2 for r in rows if r["q"][0] is not None and r["q"][2] is not None]
    if gaps:
        print(f"  bet365 fair - Polymarket mid: mean {np.mean(gaps):+.3f}, mean |gap| {np.mean(np.abs(gaps)):.3f} (n={len(gaps)})")
    bm, bb, trades = [], [], []
    for r in done:
        s = float(cache[r["slug"]])
        bid, _, ask, _, _ = r["q"]
        if bid is not None and ask is not None:
            bm.append(((bid + ask) / 2 - s) ** 2)
            bb.append((r["fair_long"] - s) ** 2)
        for pf, cost, pay in ((r["fair_long"], ask, s), (1 - r["fair_long"], None if bid is None else 1 - bid, 1 - s)):
            if cost is not None and 0 < cost < 1 and pf - (cost + FEE * cost * (1 - cost)) >= EDGE:
                trades.append({"t": r["t"], "game": r["slug"], "cost": cost,
                               "pnl": pay - cost - FEE * cost * (1 - cost)})
                break
    print(f"  settled: {len(done)}")
    if bm:
        print(f"  Brier: Polymarket mid {np.mean(bm):.4f} | bet365 fair {np.mean(bb):.4f} (n={len(bm)})")
    sim.describe(f"T5 entries (bet365 edge >= {EDGE})", trades)
    return 0


if __name__ == "__main__":
    sys.exit(main())
