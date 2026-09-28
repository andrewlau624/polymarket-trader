"""Can a LoL swing bot work on this venue? Answers from lol_recorder.py's file.

    python lol_report.py
    python lol_report.py --obs research/lol_obs.jsonl

  1. coverage   how often the books are open, two-sided, and how wide
  2. latency    after the feed DECIDES first blood / a kills line, how long the
                venue still sells the winning side cheap, and at what size
  3. lead-lag   does the game market move after the feed's swings, or with them
  4. swings     paper: buy the side the feed just swung toward, at the ask;
                sell at the bid 30 / 60 / 120 s later; fees in
  5. structure  series arbitrage net of fees, how often and for how long

Nothing here trades. A swing bot is worth building only if 2 or 4 shows money
after fees on enough games (the verdict line says what enough is).
"""

import argparse
import math
import random
from collections import defaultdict
from datetime import datetime

from src.pm_us.fees import taker_fee
from src.pm_us.jsonlog import iter_records

OBS = "research/lol_obs.jsonl"
JUMP, JUMP_S = 0.08, 15.0        # a feed swing: model moves >= 8 pts within 15 s
HOLDS = (30, 60, 120)
STALE = 0.95                     # a decided winner still offered at or under this
MIN_GAMES = 20


def _t(s):
    return datetime.fromisoformat(s).timestamp()


def _open(x):
    return x and x[4] in (None, "MARKET_STATE_OPEN")


def _mid(x):
    if not _open(x) or x[0] is None or x[2] is None:
        return None
    return (x[0] + x[2]) / 2


def ci(vals, groups, reps=2000, seed=5):
    by = defaultdict(list)
    for v, g in zip(vals, groups):
        by[g].append(v)
    gs = list(by.values())
    if len(gs) < 2:
        return None, None
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        pick = [x for _ in gs for x in rnd.choice(gs)]
        ms.append(sum(pick) / len(pick))
    ms.sort()
    return ms[int(0.025 * reps)], ms[int(0.975 * reps)]


def coverage(recs):
    print("\n1. COVERAGE")
    ev = {r["event"] for r in recs}
    games = {(r["event"], r["game"]) for r in recs if r.get("feed")}
    delays = sorted(r["feed"]["delay_s"] for r in recs
                    if r.get("feed") and r["feed"].get("delay_s") is not None)
    print(f"  events {len(ev)} | games with feed {len(games)} | polls {len(recs)}"
          + (f" | feed trails real time by median {delays[len(delays) // 2]:.0f}s"
             if delays else ""))
    kinds = defaultdict(lambda: [0, 0, []])
    for r in recs:
        for k, x in r["q"].items():
            s = kinds[k.split(":")[0]]
            s[0] += 1
            if _open(x) and x[0] is not None and x[2] is not None:
                s[1] += 1
                s[2].append(x[2] - x[0])
    for k, (n, ok, sp) in sorted(kinds.items()):
        sp.sort()
        print(f"  {k:<6} quotes {n:>6}  open+two-sided {ok / n:>5.0%}"
              + (f"  median spread {sp[len(sp) // 2]:.3f}" if sp else ""))


def latency(recs):
    """For each decided prop: the first poll the feed decided it, and whether
    the venue still sold the winner cheap then - and for how long after."""
    print("\n2. LATENCY: props the feed has already decided")
    seen = {}
    rows = []
    for r in recs:
        t = _t(r["ts"])
        for k, v in (r.get("decided") or {}).items():
            key = (r["event"], k)
            x = r["q"].get(k)
            cost, size = None, 0
            if _open(x):
                cost = x[2] if v == 1.0 else (None if x[0] is None else 1 - x[0])
                size = x[3] if v == 1.0 else x[1]
            if key not in seen:
                seen[key] = {"t0": t, "cost0": cost, "size0": size if cost else 0,
                             "stale_until": t if cost is not None and cost <= STALE else None,
                             "event": r["event"]}
            elif cost is not None and cost <= STALE and seen[key]["stale_until"] is not None:
                seen[key]["stale_until"] = t
    for key, s in seen.items():
        rows.append(s)
    if not rows:
        print("  none decided yet")
        return
    stale = [s for s in rows if s["cost0"] is not None and s["cost0"] <= STALE]
    print(f"  decided props {len(rows)} | winner offered at <= {STALE} when the feed "
          f"decided it: {len(stale)}")
    if stale:
        pnl = [1 - s["cost0"] - taker_fee(s["cost0"]) for s in stale]
        lasted = sorted(s["stale_until"] - s["t0"] for s in stale)
        lo, hi = ci(pnl, [s["event"] for s in stale])
        print(f"    paper profit/share {sum(pnl) / len(pnl):+.4f}"
              + (f" CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None else "")
              + f" | median size at that price {sorted(s['size0'] for s in stale)[len(stale) // 2]:.0f}"
              + f" | stayed stale median {lasted[len(lasted) // 2]:.0f}s")


def series_by_game(recs):
    out = defaultdict(list)
    for r in recs:
        # in a best-of-1 the match market IS the game market
        k = "match" if r.get("best_of") == 1 or f"map:{r['game']}" not in r["q"] \
            and r.get("best_of") in (None, 1) else f"map:{r['game']}"
        m = _mid(r["q"].get(k))
        if r.get("model_p") is None or m is None:
            continue
        out[(r["event"], r["game"])].append((_t(r["ts"]), r["model_p"], m, r["q"][k]))
    return out


def leadlag(recs, bin_s=10, lags=range(-6, 13)):
    print("\n3. LEAD-LAG: feed model vs game-market mid (10 s bins)")
    pairs = defaultdict(list)
    for pts in series_by_game(recs).values():
        bins = {}
        for t, p, m, _ in pts:
            bins[int(t // bin_s)] = (p, m)
        ks = sorted(bins)
        dp = {k: bins[k][0] - bins[k - 1][0] for k in ks if k - 1 in bins}
        dm = {k: bins[k][1] - bins[k - 1][1] for k in ks if k - 1 in bins}
        for lag in lags:
            for k, v in dp.items():
                if k + lag in dm:
                    pairs[lag].append((v, dm[k + lag]))
    if not pairs:
        print("  no game with both a feed and an open game market yet")
        return
    best = None
    for lag in lags:
        xy = pairs.get(lag) or []
        if len(xy) < 20:
            continue
        mx = sum(x for x, _ in xy) / len(xy)
        my = sum(y for _, y in xy) / len(xy)
        sxy = sum((x - mx) * (y - my) for x, y in xy)
        sx = math.sqrt(sum((x - mx) ** 2 for x, _ in xy))
        sy = math.sqrt(sum((y - my) ** 2 for _, y in xy))
        c = sxy / (sx * sy) if sx and sy else 0.0
        print(f"  market {lag * bin_s:+4d}s after feed: corr {c:+.3f}  (n={len(xy)})")
        if best is None or c > best[1]:
            best = (lag, c)
    if best:
        verdict = ("market LAGS the feed" if best[0] > 0 else
                   "market moves WITH the feed" if best[0] == 0 else "market LEADS the feed")
        print(f"  peak at {best[0] * bin_s:+d}s: {verdict}")


def swings(recs):
    print(f"\n4. SWINGS (paper): feed moves >= {JUMP:.0%} in {JUMP_S:.0f}s -> buy that side")
    trades = []
    for (ev, g), pts in series_by_game(recs).items():
        last_entry = -1e9
        for i, (t, p, _m, x) in enumerate(pts):
            prev = [q for q in pts[:i] if t - q[0] <= JUMP_S]
            if not prev or t - last_entry < 60:
                continue
            move = p - prev[0][1]
            if abs(move) < JUMP:
                continue
            up = move > 0                       # buy A if the feed swung to A
            cost = x[2] if up else (None if x[0] is None else 1 - x[0])
            if cost is None:
                continue
            last_entry = t
            row = {"game": f"{ev}#{g}", "cost": cost}
            for h in HOLDS:
                later = next((q for q in pts[i:] if q[0] - t >= h), None)
                if later is None:
                    break
                y = later[3]
                out = y[0] if up else (None if y[2] is None else 1 - y[2])
                if out is None:
                    break
                row[h] = out - cost - taker_fee(cost) - taker_fee(out)
            if all(h in row for h in HOLDS):
                trades.append(row)
    games = {t["game"] for t in trades}
    print(f"  trades {len(trades)} across {len(games)} games")
    for h in HOLDS if trades else ():
        v = [t[h] for t in trades]
        lo, hi = ci(v, [t["game"] for t in trades])
        print(f"    sell after {h:>3}s: {sum(v) / len(v):+.4f}/share"
              + (f"  CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None else ""))
    return trades, games


def structure(recs):
    print("\n5. STRUCTURE: series arbitrage net of fees")
    hits = [r for r in recs if r.get("arb")]
    if not hits:
        print("  none")
        return
    eps, last = 0, {}
    for r in hits:
        t = _t(r["ts"])
        if t - last.get(r["event"], -1e9) > 30:
            eps += 1
        last[r["event"]] = t
    best = max(hits, key=lambda r: r["arb"]["net"])
    print(f"  polls with an arb {len(hits)} | episodes {eps} | best net "
          f"${best['arb']['net']:.2f} ({best['event']}): {best['arb']['legs']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--obs", default=OBS)
    a = ap.parse_args()
    recs = list(iter_records(a.obs))
    print(f"== LoL recorder | {a.obs} | {len(recs)} polls ==")
    if not recs:
        print("  nothing recorded yet: it only records while the venue lists a live LoL match")
        return
    coverage(recs)
    latency(recs)
    leadlag(recs)
    trades, games = swings(recs)
    structure(recs)
    print(f"\n  verdict needs >= {MIN_GAMES} games; have "
          f"{len({(r['event'], r['game']) for r in recs if r.get('feed')})}. "
          "Build a trader only if 2 or 4 is positive with its CI above 0.")


if __name__ == "__main__":
    main()
