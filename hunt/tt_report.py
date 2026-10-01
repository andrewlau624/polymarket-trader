"""T3: table tennis in play - model price vs the Polymarket US book (TEST_PLAN.md).

    .venv/bin/python hunt/tt_report.py
    .venv/bin/python hunt/tt_report.py --no-settle

Model: best of 5 games, each to 11 won by 2. One per-point probability q for the
LONG player, solved so the match probability at 0-0 equals the pre-start mid p0.
Serve is ignored (in the 11-point game it does not change game probability).

Score strings are 'a-b, a-b, ...' in the event's team order; the book is quoted
in the long team. Both are read from the record, never assumed.
"""

import argparse
import calendar
import glob
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from functools import lru_cache

import numpy as np

sys.path.insert(0, ".")
from src import sim                                                # noqa: E402
from src.xvenue import core                                        # noqa: E402
from xvenue_recorder import PM, get                                # noqa: E402

REC = os.path.join("research", "ttlive", "rec-*.jsonl")
NICHE = os.path.join("research", "niche", "rec-*.jsonl")
SETTLE = os.path.join("research", "ttlive", "settle.json")
EDGE, FEE, MIN_ASK_SZ = 0.05, 0.0695, 10
T6_START = 1790879715          # TEST_PLAN.md T6: only rows recorded after registration count
T6_JUMP, T6_STILL, T6_HOLD, T6_GAP = 0.04, 0.005, 30, 6
GAMES_TO_WIN = 3


# --- the model -------------------------------------------------------------
@lru_cache(maxsize=None)
def p_game(q, a, b):
    """P(long player wins the game | long has a points, other has b), to 11 win by 2."""
    if a >= 11 and a - b >= 2:
        return 1.0
    if b >= 11 and b - a >= 2:
        return 0.0
    if a >= 10 and b >= 10:                     # deuce: win two in a row before losing two
        if a == b:
            return q * q / (q * q + (1 - q) * (1 - q))
        return q + (1 - q) * p_game(q, b, b) if a > b else q * p_game(q, a, a)
    return q * p_game(q, a + 1, b) + (1 - q) * p_game(q, a, b + 1)


@lru_cache(maxsize=None)
def p_match(q, ga, gb, a=0, b=0):
    """P(long wins the match | games ga-gb, current game a-b)."""
    if ga >= GAMES_TO_WIN:
        return 1.0
    if gb >= GAMES_TO_WIN:
        return 0.0
    g = p_game(q, a, b)
    return g * p_match(q, ga + 1, gb) + (1 - g) * p_match(q, ga, gb + 1)


def solve_q(p0):
    lo, hi = 0.01, 0.99
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_match(round(mid, 6), 0, 0) < p0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 6)


def parse(score, flip):
    """'11-7, 11-5, 3-2' -> (games long, games other, points long, points other)."""
    ga = gb = 0
    a = b = 0
    parts = [p.strip() for p in str(score or "").split(",") if "-" in p]
    for i, p in enumerate(parts):
        try:
            x, y = (int(v) for v in p.split("-"))
        except ValueError:
            return None
        if flip:
            x, y = y, x
        done = (x >= 11 or y >= 11) and abs(x - y) >= 2
        if done:
            if x > y:
                ga += 1
            else:
                gb += 1
            a = b = 0
        else:
            a, b = x, y
    return ga, gb, a, b


# --- data ------------------------------------------------------------------
def load():
    by = defaultdict(list)
    for fp in sorted(glob.glob(REC)):
        for line in open(fp):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("q") and len(r.get("teams") or []) == 2 and r.get("long_id") in r["teams"]:
                by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["t"])
    return by


def prestart_mids():
    out = {}
    for fp in glob.glob(NICHE):
        for line in open(fp):
            r = json.loads(line)
            q = r["q"]
            if q[0] is None or q[2] is None or q[4] != "MARKET_STATE_OPEN":
                continue
            try:
                st = calendar.timegm(time.strptime(r["start"][:19], "%Y-%m-%dT%H:%M:%S"))
            except (TypeError, ValueError):
                continue
            if r["t"] < st and (r["slug"] not in out or r["t"] > out[r["slug"]][0]):
                out[r["slug"]] = (r["t"], (q[0] + q[2]) / 2)
    return {k: v[1] for k, v in out.items()}


def p0_for(slug, rows, pre):
    if slug in pre:
        return pre[slug]
    r = rows[0]
    st = parse(r["score"], r["long_id"] != r["teams"][0])
    q = r["q"]
    if st == (0, 0, 0, 0) and q[0] is not None and q[2] is not None:
        return (q[0] + q[2]) / 2
    return None


def mid(q):
    return (q[0] + q[2]) / 2 if q and q[0] is not None and q[2] is not None else None


# --- T6: buy the scorer while the book lags, sell 30 s later ----------------
def t6_trades(by, pre):
    out = []
    for slug, rows in by.items():
        rows = [r for r in rows if r["t"] >= T6_START]
        if len(rows) < 3:
            continue
        p0 = p0_for(slug, by[slug], pre)
        if p0 is None or not 0.03 < p0 < 0.97:
            continue
        q = solve_q(p0)
        busy_until = 0.0
        for i in range(1, len(rows)):
            a, b = rows[i - 1], rows[i]
            if b["t"] - a["t"] > T6_GAP or b["t"] < busy_until:
                continue
            flip = b["long_id"] != b["teams"][0]
            sa, sb = parse(a["score"], flip), parse(b["score"], flip)
            ma, mb = mid(a["q"]), mid(b["q"])
            if None in (sa, sb, ma, mb) or sa == sb or abs(mb - ma) >= T6_STILL:
                continue
            jump = p_match(q, *sb) - p_match(q, *sa)
            if abs(jump) < T6_JUMP or b["q"][4] != "MARKET_STATE_OPEN":
                continue
            bid, bsz, ask, asz, _ = b["q"]
            long_side = jump > 0
            cost, size = (ask, asz) if long_side else ((None if bid is None else 1 - bid), bsz)
            if cost is None or size < MIN_ASK_SZ or not 0 < cost < 1:
                continue
            ex = next((x for x in rows[i + 1:] if x["t"] >= b["t"] + T6_HOLD), None)
            if not ex or ex["t"] > b["t"] + T6_HOLD + 30:
                continue
            xq = ex["q"]
            px = xq[0] if long_side else (None if xq[2] is None else 1 - xq[2])
            if px is None:
                continue
            out.append({"t": b["t"], "game": slug, "cost": cost,
                        "pnl": px - cost - FEE * cost * (1 - cost) - FEE * px * (1 - px)})
            busy_until = ex["t"]
    return out


def t6_report(by, pre):
    tr = t6_trades(by, pre)
    print(f"
T6  SCORER WHILE THE BOOK LAGS, EXIT 30 s | rows from t >= {T6_START} only")
    if not tr:
        print("  no trades yet")
        return
    grp = defaultdict(list)
    for x in tr:
        grp[x["game"]].append(x["pnl"])
    gs = list(grp.values())
    rng = np.random.default_rng(6)
    boots = [np.mean([v for g in (gs[j] for j in rng.integers(0, len(gs), len(gs))) for v in g]) for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    tr.sort(key=lambda x: x["t"])
    h = len(tr) // 2
    print(f"  {len(tr)} trades on {len(gs)} matches | mean {np.mean([x['pnl'] for x in tr]):+.4f}/share "
          f"[{lo:+.4f}, {hi:+.4f}] | win {np.mean([x['pnl'] > 0 for x in tr]):.0%} | halves "
          f"{np.mean([x['pnl'] for x in tr[:h]] or [0]):+.4f} / {np.mean([x['pnl'] for x in tr[h:]] or [0]):+.4f}")
    print(f"  bar: >= 300 trades, >= 100 matches, CI above 0, both halves > 0 -> "
          f"{'PASS' if len(tr) >= 300 and len(gs) >= 100 and lo > 0 and min(np.mean([x['pnl'] for x in tr[:h]]), np.mean([x['pnl'] for x in tr[h:]])) > 0 else 'not passed'}")


# --- report ----------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-settle", action="store_true")
    a = ap.parse_args(argv)
    by, pre = load(), prestart_mids()
    lead_pts, lead_games, gaps, ents = [], [], [], []
    used = 0
    for slug, rows in by.items():
        p0 = p0_for(slug, rows, pre)
        if p0 is None or not 0.03 < p0 < 0.97:
            continue
        used += 1
        q = solve_q(p0)
        prev = None
        taken = set()
        for i, r in enumerate(rows):
            flip = r["long_id"] != r["teams"][0]
            st = parse(r["score"], flip)
            m = mid(r["q"])
            if st is None or m is None:
                continue
            model = p_match(q, *st)
            gaps.append(model - m)
            if prev and st != prev[0]:
                game_change = st[:2] != prev[0][:2]
                future = [mid(x["q"]) for x in rows[i + 1:i + 4] if mid(x["q"]) is not None]
                if future:
                    total = future[-1] - prev[1]
                    if abs(total) >= 0.01:
                        already = (m - prev[1]) / total
                        (lead_games if game_change else lead_pts).append(already > 0.5)
                # entry, at most one per (match, game number)
                gno = st[0] + st[1]
                if (slug, gno) not in taken and r["q"][4] == "MARKET_STATE_OPEN":
                    bid, bsz, ask, asz, _ = r["q"]
                    for team, pm_, cost, sz in (("L", model, ask, asz), ("S", 1 - model, None if bid is None else 1 - bid, bsz)):
                        if cost is None or sz < MIN_ASK_SZ or not 0 < cost < 1:
                            continue
                        if pm_ - (cost + FEE * cost * (1 - cost)) >= EDGE:
                            ents.append({"t": r["t"], "slug": slug, "team": team, "cost": cost,
                                         "model": pm_, "i": i})
                            taken.add((slug, gno))
                            break
            prev = (st, m)
    print(f"\nT3  TABLE TENNIS IN PLAY | {sum(len(v) for v in by.values())} polls, {len(by)} matches, "
          f"{used} with a starting price")
    if gaps:
        g = sorted(abs(x) for x in gaps)
        print(f"  |model - book mid|: median {g[len(g) // 2]:.3f}, p90 {g[int(.9 * len(g))]:.3f}")
    for name, xs in (("point", lead_pts), ("game", lead_games)):
        if xs:
            print(f"  {name} changes: book had already moved > half its move when the score showed it "
                  f"in {sum(xs) / len(xs):.0%} of {len(xs)}")
    print(f"  entries (model - ask - fee >= {EDGE}): {len(ents)} on {len({e['slug'] for e in ents})} matches")
    for h in (30, 120):
        mk = []
        for e in ents:
            rows = by[e["slug"]]
            for x in rows[e["i"] + 1:]:
                if x["t"] >= e["t"] + h:
                    q_ = x["q"]
                    px = q_[0] if e["team"] == "L" else (None if q_[2] is None else 1 - q_[2])
                    if px is not None and x["t"] <= e["t"] + h + 60:
                        mk.append(px - e["cost"] - FEE * e["cost"] * (1 - e["cost"]) - FEE * px * (1 - px))
                    break
        if mk:
            print(f"  markout {h:>3}s: n={len(mk)} mean {statistics.mean(mk):+.4f} win {sum(m > 0 for m in mk) / len(mk):.0%}")
    t6_report(by, pre)
    if a.no_settle or not ents:
        return 0
    try:
        cache = json.load(open(SETTLE))
    except (OSError, ValueError):
        cache = {}
    for s in {e["slug"] for e in ents} - set(k for k, v in cache.items() if v is not None):
        d = get(f"{PM}/markets/{s}/settlement")
        cache[s] = core.f((d or {}).get("settlement"))
        time.sleep(0.25)
    json.dump(cache, open(SETTLE, "w"))
    trades = []
    for e in ents:
        s = cache.get(e["slug"])
        if s is None:
            continue
        pay = s if e["team"] == "L" else 1 - s
        trades.append({"t": e["t"], "game": e["slug"], "cost": e["cost"],
                       "pnl": pay - e["cost"] - FEE * e["cost"] * (1 - e["cost"])})
    if trades:
        sim.describe("T3 held to settlement", trades)
        grp = defaultdict(list)
        for x in trades:
            grp[x["game"]].append(x)
        gs = list(grp.values())
        rng = np.random.default_rng(3)
        boots = [sum(x["pnl"] for g in pick for x in g) / sum(x["cost"] for g in pick for x in g)
                 for pick in ([gs[j] for j in rng.integers(0, len(gs), len(gs))] for _ in range(2000))]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"  per $: [{lo:+.2%}, {hi:+.2%}] over {len(gs)} matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
