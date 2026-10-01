"""Kalshi vs Polymarket US: grade the recordings against TEST_PLAN.md.

    python xvenue_report.py                 # S1, S2, lead-lag, in-play, verdict
    python xvenue_report.py --no-settle     # skip settlement lookups (S1 only)

S1 locked pair  - pre-game observations where buying team X on one venue and
                  the other team on the other venue costs < $1 after both fees.
                  An EPISODE is a run of consecutive observations of the same
                  game and direction with net >= +0.005 and >= 10 pairs; it
                  counts only if it lasts >= 2 observations (TEST_PLAN.md).
S2 cheaper venue - first pre-game time each (game, team, venue) ask + fee sat
                  >= 1c below the other venue's mid; held to that venue's own
                  settlement. Bootstrapped over games, sized through src/sim.py.
"""

import argparse
import glob
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict

from src import sim
from src.xvenue import core
from xvenue_recorder import KAL, PM, get

REC = os.path.join("research", "xvenue", "rec-*.jsonl")
SETTLE = os.path.join("research", "xvenue", "settle.json")
GAP_S = 600            # observations further apart than this are not "consecutive"
S1_MIN_NET, S1_MIN_C = 0.005, 10
S2_EDGE = 0.010


def load(pattern=REC):
    rows = []
    for fp in sorted(glob.glob(pattern)):
        with open(fp) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    rows.sort(key=lambda r: r["t"])
    return rows


def ci(xs, stat, reps=2000, seed=7):
    """95% bootstrap CI of stat over resampled groups (xs is a list of groups)."""
    if len(xs) < 5:
        return None
    rnd = random.Random(seed)
    vals = sorted(stat([rnd.choice(xs) for _ in xs]) for _ in range(reps))
    return vals[int(0.025 * reps)], vals[int(0.975 * reps)]


# --- S1 ------------------------------------------------------------------
def s1_episodes(rows):
    """[{game, dir, t, net, c, len}] for runs that pass the persistence rule."""
    runs = defaultdict(list)                       # (game, dir) -> current run
    last_t = {}
    eps = []

    def close(k):
        r = runs.pop(k, [])
        if len(r) >= 2:
            eps.append({"game": k[0], "dir": k[1], "t": r[0]["t"], "net": r[0]["net"],
                        "c": r[0]["c"], "len": len(r), "dur": r[-1]["t"] - r[0]["t"]})

    for r in rows:
        g = r["game"]
        if g in last_t and r["t"] - last_t[g] > GAP_S:
            for k in [k for k in runs if k[0] == g]:
                close(k)
        last_t[g] = r["t"]
        hits = {p["dir"]: p for p in core.locked_pairs(r)
                if p["net"] >= S1_MIN_NET and p["c"] >= S1_MIN_C}
        for k in [k for k in runs if k[0] == g and k[1] not in hits]:
            close(k)
        for d, p in hits.items():
            runs.setdefault((g, d), []).append({"t": r["t"], "net": p["net"], "c": p["c"]})
    for k in list(runs):
        close(k)
    return eps


def best_net(rows):
    out = []
    for r in rows:
        ps = core.locked_pairs(r)
        if ps:
            out.append(max(p["net"] for p in ps))
    return out


# --- S2 ------------------------------------------------------------------
def s2_entries(rows):
    seen, out = set(), []
    for r in rows:
        for team, venue, how, px, fv, c in core.cheap_entries(r, S2_EDGE):
            k = (r["game"], team, venue)
            if k in seen:
                continue
            seen.add(k)
            out.append({"t": r["t"], "game": r["game"], "start": r["start"], "team": team,
                        "venue": venue, "how": how, "px": px, "fair": fv, "c": c,
                        "pm_slug": r["pm_slug"],
                        "ticker": r["k_long"] if team == "L" else r["k_short"]})
    return out


def settlements(entries, cache, budget=200):
    """Fill cache {key: value or None}. Value is the bought team's payout basis:
    'pm:<slug>' = Polymarket settlement of the LONG side, 'k:<ticker>' = Kalshi
    settlement of that team's YES."""
    asked = 0
    for e in entries:
        k = f"pm:{e['pm_slug']}" if e["venue"] == "pm" else f"k:{e['ticker']}"
        if cache.get(k) is not None or asked >= budget:
            continue
        asked += 1
        try:
            if e["venue"] == "pm":
                d = get(f"{PM}/markets/{e['pm_slug']}/settlement")
                v = core.f((d or {}).get("settlement"))
            else:
                d = (get(f"{KAL}/markets/{e['ticker']}") or {}).get("market") or {}
                v = core.f(d.get("settlement_value_dollars")) \
                    if d.get("status") in ("finalized", "settled") else None
        except Exception:
            v = None
        cache[k] = v
        time.sleep(0.25)
    return cache


def grade(e, cache):
    if e["venue"] == "pm":
        s = cache.get(f"pm:{e['pm_slug']}")
        if s is None:
            return None
        payout = s if e["team"] == "L" else 1.0 - s
    else:
        payout = cache.get(f"k:{e['ticker']}")
        if payout is None:
            return None
    fee = core.fee(e["venue"], e["px"], e["c"]) / e["c"]
    return {"t": e["t"], "game": e["game"], "start": e["start"], "cost": e["px"],
            "pnl": payout - e["px"] - fee, "venue": e["venue"]}


def per_dollar(trades):
    staked = sum(t["cost"] for t in trades)
    return sum(t["pnl"] for t in trades) / staked if staked else 0.0


# --- lead-lag ------------------------------------------------------------
def lead_lag(rows):
    """For pre-game gaps >= 2c, how much of the gap each venue closed by the
    game's next observation. Returns (n, pm share, kalshi share, gap closed)."""
    prev = {}
    pm_move = k_move = closed = 0.0
    n = 0
    for r in rows:
        if r["live"]:
            continue
        mp, _ = core.mid(r["pm"]) if r["pm"] else (None, None)
        mk, _ = core.mid(r["kl"]) if r["kl"] else (None, None)
        if mp is None or mk is None:
            continue
        g = r["game"]
        if g in prev and r["t"] - prev[g][0] <= GAP_S:
            t0, p0, k0 = prev[g]
            gap = k0 - p0
            if abs(gap) >= 0.02:
                sgn = 1 if gap > 0 else -1
                pm_move += sgn * (mp - p0)
                k_move += -sgn * (mk - k0)
                closed += abs(gap) - abs(mk - mp)
                n += 1
        prev[g] = (r["t"], mp, mk)
    tot = pm_move + k_move
    return n, (pm_move / tot if tot else None), (k_move / tot if tot else None), \
        (closed / n if n else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-settle", action="store_true")
    ap.add_argument("--bankroll", type=float, default=200.0)
    a = ap.parse_args(argv)
    rows = load()
    if not rows:
        print("no recordings yet (research/xvenue/rec-*.jsonl)")
        return 0
    pre = [r for r in rows if not r["live"]]
    live = [r for r in rows if r["live"]]
    days = max((rows[-1]["t"] - rows[0]["t"]) / 86400.0, 1e-9)
    games = {r["game"] for r in rows}
    print(f"\nKALSHI vs POLYMARKET US | {len(rows)} observations, {len(games)} games, "
          f"{days:.2f} days recorded ({len(pre)} pre-game, {len(live)} in play)")
    skew = [abs(r["tk"] - r["tp"]) for r in rows if r.get("tk")]
    if skew:
        print(f"  venues fetched {statistics.median(skew):.2f}s apart (median), "
              f"max {max(skew):.1f}s")

    print("\nS1  LOCKED PAIR (pre-game, taker both legs, both fees)")
    bn = sorted(best_net(pre))
    if bn:
        q = lambda p: bn[min(int(p * len(bn)), len(bn) - 1)]
        print(f"  best pair per observation, net per $1: median {q(0.5):+.4f}, "
              f"95th pct {q(0.95):+.4f}, max {bn[-1]:+.4f} (n={len(bn)})")
        print(f"  observations with any net > 0: {sum(x > 0 for x in bn)} "
              f"({sum(x > 0 for x in bn) / len(bn):.1%})")
    eps = s1_episodes(pre)
    eg = {e["game"] for e in eps}
    cap = sum(e["net"] * e["c"] for e in eps)
    print(f"  counted episodes (net >= {S1_MIN_NET}, >= {S1_MIN_C} pairs, >= 2 obs): "
          f"{len(eps)} on {len(eg)} games")
    if eps:
        print(f"  median net {statistics.median(e['net'] for e in eps):+.4f}/pair | "
              f"capturable ${cap:.2f} total = ${cap / days:.2f}/day")
        for e in sorted(eps, key=lambda e: -e["net"] * e["c"])[:8]:
            print(f"    {e['game']:<34} {e['dir']:<5} net {e['net']:+.4f} x {e['c']:>3} "
                  f"lasted {e['len']} obs / {e['dur'] / 60:.0f} min")
    s1_pass = len(eg) >= 20 and eps and statistics.median(e["net"] for e in eps) >= 0.005 \
        and cap / max(days, 14.0) >= 5.0
    print(f"  bar: >= 20 games, median net >= 0.005, >= $5/day over 14 days -> "
          f"{'PASS' if s1_pass else 'not passed'}"
          + ("" if days >= 14 else f" (only {days:.1f} of 14 days recorded)"))
    if days >= 7 and len(eps) < 5:
        print("  KILL: fewer than 5 counted episodes after 7 days")

    print("\nLEAD-LAG (diagnostic, pre-game gaps >= 2c between mids)")
    n, sp, sk, cl = lead_lag(rows)
    if n:
        print(f"  {n} gaps: Polymarket did {sp:.0%} of the closing, Kalshi {sk:.0%}; "
              f"gap shrank {cl:+.4f} on average by the next observation")
    else:
        print("  no gaps >= 2c yet")

    print("\nS2  BUY THE CHEAPER VENUE, HOLD (pre-game)")
    ents = s2_entries(pre)
    by_v = defaultdict(int)
    for e in ents:
        by_v[e["venue"]] += 1
    print(f"  {len(ents)} entries ({dict(by_v)}) on {len({e['game'] for e in ents})} games")
    trades = []
    if not a.no_settle and ents:
        try:
            with open(SETTLE) as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
        settlements(ents, cache)
        with open(SETTLE, "w") as fh:
            json.dump(cache, fh)
        trades = [t for t in (grade(e, cache) for e in ents) if t]
    print(f"  settled: {len(trades)} on {len({t['game'] for t in trades})} games")
    s2_pass = False
    if trades:
        sim.describe("S2 trades", trades, a.bankroll)
        grp = defaultdict(list)
        for t in trades:
            grp[t["game"]].append(t)
        c = ci(list(grp.values()), lambda gs: per_dollar([t for g in gs for t in g]))
        order = sorted(grp, key=lambda g: grp[g][0]["start"])
        h1 = [t for g in order[:len(order) // 2] for t in grp[g]]
        h2 = [t for g in order[len(order) // 2:] for t in grp[g]]
        print(f"  expectancy per $: {per_dollar(trades):+.2%}"
              + (f", 95% CI [{c[0]:+.2%}, {c[1]:+.2%}] over {len(grp)} games" if c else ""))
        print(f"  halves by game start: {per_dollar(h1):+.2%} | {per_dollar(h2):+.2%}")
        for v in ("pm", "k"):
            tv = [t for t in trades if t["venue"] == v]
            if tv:
                print(f"    bought on {v}: n={len(tv)} {per_dollar(tv):+.2%} per $")
        mc = sim.monte_carlo(trades, a.bankroll)
        s2_pass = (len(trades) >= 100 and len(grp) >= 20 and c and c[0] > 0
                   and per_dollar(h1) > 0 and per_dollar(h2) > 0
                   and mc and mc["p_half"] < 0.10)
        if len(trades) >= 50 and len(grp) >= 10:
            if statistics.mean(t["pnl"] for t in trades) < -0.02:
                print("  KILL: expectancy below -2c/share after 50 entries")
    print(f"  bar: >= 100 entries, >= 20 games, CI above 0, both halves > 0, "
          f"P(50% drawdown) < 10% -> {'PASS' if s2_pass else 'not passed'}")

    print("\nIN PLAY (displayed in-play prices are not fillable; cannot pass)")
    bl = sorted(best_net(live))
    if bl:
        print(f"  best pair net: median {bl[len(bl) // 2]:+.4f}, max {bl[-1]:+.4f}; "
              f"episodes {len(s1_episodes(live))} (n={len(bl)})")

    print("\nVERDICT: " + ("S1 PASSED" if s1_pass else "S1 not passed") + " | "
          + ("S2 PASSED" if s2_pass else "S2 not passed")
          + ". Nothing trades until TEST_PLAN.md's live steps are done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
