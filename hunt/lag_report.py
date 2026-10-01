"""S4: does the international Polymarket book lead Polymarket US? (TEST_PLAN.md)

    .venv/bin/python hunt/lag_report.py
    .venv/bin/python hunt/lag_report.py --no-settle

Lead-lag   for consecutive observations of a game, corr(next US move, this
           international move) against corr(next international move, this US
           move). If the international book leads, the first is larger.
Entries    buy team X on US at the ask when intl_mid(X) - (us_ask(X) + fee) >= 1c,
           the international book is tight (<= 2c) and >= $500 deep at the touch,
           and the snapshots are < 1 s apart. One per (game, team) per 10 min.
Scored     (a) markout: sell into the US bid 60 s / 5 min later, fee paid again
           (b) hold to the US settlement
"""

import argparse
import glob
import json
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
from src.xvenue import core                                        # noqa: E402
from xvenue_recorder import PM, get                                # noqa: E402

REC = os.path.join("research", "lag", "rec-*.jsonl")
SETTLE = os.path.join("research", "lag", "settle.json")
EDGE, MAX_SPREAD, MIN_USD, MAX_SKEW, COOLDOWN = 0.010, 0.02, 500.0, 1.0, 600
HORIZONS = (60, 300)


def load():
    rows = []
    for fp in sorted(glob.glob(REC)):
        for line in open(fp):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    rows.sort(key=lambda r: r["t"])
    return rows


def mids(r):
    u, i = r["us"], r["intl"]
    if None in (u[0], u[2], i[0], i[2]):
        return None, None
    return (u[0] + u[2]) / 2, (i[0] + i[2]) / 2


def tradable(r):
    u, i = r["us"], r["intl"]
    if u[4] != "MARKET_STATE_OPEN" or None in (u[0], u[2], i[0], i[2]):
        return False
    if abs(r["ti"] - r["tu"]) >= MAX_SKEW or i[2] - i[0] > MAX_SPREAD + 1e-9:
        return False
    return min(i[0] * i[1], i[2] * i[3]) >= MIN_USD


def entries(rows):
    last, out = {}, []
    for r in rows:
        if not tradable(r):
            continue
        u, i = r["us"], r["intl"]
        im = (i[0] + i[2]) / 2
        for team, cost, fair in (("L", u[2], im), ("S", 1 - u[0], 1 - im)):
            if fair - (cost + core.pm_fee(cost)) < EDGE:
                continue
            k = (r["us_slug"], team)
            if r["t"] - last.get(k, -1e18) < COOLDOWN:
                continue
            last[k] = r["t"]
            out.append({"t": r["t"], "game": r["us_slug"], "team": team, "cost": cost,
                        "fair": fair, "gap": fair - cost})
    return out


def markout(e, by_game, h):
    for r in by_game[e["game"]]:
        if r["t"] >= e["t"] + h:
            if r["t"] > e["t"] + h + 120 or r["us"][0] is None or r["us"][2] is None:
                return None
            exit_px = r["us"][0] if e["team"] == "L" else 1 - r["us"][2]
            return exit_px - e["cost"] - core.pm_fee(e["cost"]) - core.pm_fee(exit_px)
    return None


def lead_lag(rows):
    by = defaultdict(list)
    for r in rows:
        m = mids(r)
        if m[0] is not None and abs(r["ti"] - r["tu"]) < MAX_SKEW:
            by[r["us_slug"]].append((r["t"], *m))
    du_next, di_now, di_next, du_now = [], [], [], []
    for obs in by.values():
        for a, b, c in zip(obs, obs[1:], obs[2:]):
            if c[0] - a[0] > 60:
                continue
            di_now.append(b[2] - a[2])
            du_now.append(b[1] - a[1])
            du_next.append(c[1] - b[1])
            di_next.append(c[2] - b[2])
    if len(du_next) < 30:
        return None
    cc = lambda x, y: float(np.corrcoef(x, y)[0, 1]) if np.std(x) and np.std(y) else float("nan")
    return len(du_next), cc(du_next, di_now), cc(di_next, du_now), cc(du_now, di_now)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-settle", action="store_true")
    a = ap.parse_args(argv)
    rows = load()
    if not rows:
        print("no recordings yet")
        return 0
    games = {r["us_slug"] for r in rows}
    hrs = (rows[-1]["t"] - rows[0]["t"]) / 3600
    print(f"\nS4  INTERNATIONAL BOOK -> POLYMARKET US | {len(rows)} obs, {len(games)} games, {hrs:.1f} h")
    gaps = [abs(m[0] - m[1]) for m in map(mids, rows) if m[0] is not None]
    if gaps:
        gaps.sort()
        print(f"  |US mid - intl mid|: median {gaps[len(gaps) // 2]:.4f}, p90 {gaps[int(.9 * len(gaps))]:.4f}, "
              f"max {gaps[-1]:.4f}")
    ll = lead_lag(rows)
    if ll:
        n, us_follows, intl_follows, same = ll
        print(f"  lead-lag over {n} step pairs: corr(next US move, intl move now) {us_follows:+.3f} | "
              f"corr(next intl move, US move now) {intl_follows:+.3f} | same-step {same:+.3f}")
        print("  -> " + ("international LEADS" if us_follows > intl_follows + 0.05 else
                         "US LEADS" if intl_follows > us_follows + 0.05 else "no clear leader"))
    ents = entries(rows)
    print(f"  entries: {len(ents)} on {len({e['game'] for e in ents})} games")
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["us_slug"]].append(r)
    for h in HORIZONS:
        mk = [m for m in (markout(e, by_game, h) for e in ents) if m is not None]
        if mk:
            print(f"  markout {h:>3}s: n={len(mk)} mean {statistics.mean(mk):+.4f} median "
                  f"{statistics.median(mk):+.4f} win {sum(m > 0 for m in mk) / len(mk):.0%}")
    if a.no_settle or not ents:
        return 0
    try:
        cache = json.load(open(SETTLE))
    except (OSError, ValueError):
        cache = {}
    for g in {e["game"] for e in ents} - set(cache):
        d = get(f"{PM}/markets/{g}/settlement")
        cache[g] = core.f((d or {}).get("settlement"))
        time.sleep(0.25)
    json.dump(cache, open(SETTLE, "w"))
    held = []
    for e in ents:
        s = cache.get(e["game"])
        if s is None:
            continue
        pay = s if e["team"] == "L" else 1 - s
        held.append({"game": e["game"], "pnl": pay - e["cost"] - core.pm_fee(e["cost"]), "cost": e["cost"]})
    if held:
        grp = defaultdict(list)
        for x in held:
            grp[x["game"]].append(x)
        per = sum(x["pnl"] for x in held) / sum(x["cost"] for x in held)
        rng = np.random.default_rng(5)
        gs = list(grp.values())
        boots = []
        for _ in range(1000):
            pick = [gs[j] for j in rng.integers(0, len(gs), len(gs))]
            boots.append(sum(x["pnl"] for g in pick for x in g) / sum(x["cost"] for g in pick for x in g))
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"  held to settlement: n={len(held)} on {len(grp)} games, {per:+.2%} per $ "
              f"[{lo:+.2%}, {hi:+.2%}]")
    else:
        print("  held to settlement: nothing settled yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
