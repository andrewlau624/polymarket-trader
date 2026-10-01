"""T2: table-tennis Elo from Polymarket US's own settled matches (TEST_PLAN.md).

    .venv/bin/python hunt/ttelo.py --fit      # choose K on matches before 2026-09-01
    .venv/bin/python hunt/ttelo.py --grade    # ratings vs recorded prices (research/niche)

A match is (league, time, player A, player B, A won). Polymarket US lists one book
per match; marketSides[].long names the player the book is quoted in.
"""

import argparse
import glob
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, ".")
LEAGUES = {"setkameua", "setkamecz", "setkamemd", "czechligapro", "ttelite", "ttcup", "wtt"}
FIT_END = "2026-09-01"
MIN_N = 20
EDGE = 0.05
FEE = 0.0695


def matches(path="research/us_closed.jsonl"):
    seen, out = set(), []
    for line in open(path):
        m = json.loads(line)
        s = m["slug"].split("-")
        if m["slug"] in seen or len(s) < 2 or s[1] not in LEAGUES or m.get("marketType") != "moneyline":
            continue
        seen.add(m["slug"])
        sides = m.get("sides") or []
        if len(sides) != 2:
            continue
        px = [str(x.get("price")) for x in sides]
        if sorted(px) not in (["0", "1"], ["0.0", "1.0"], ["0.0000", "1.0000"]):
            continue                                     # 0.50 voids and unsettled
        lo = [x for x in sides if x.get("long")]
        if len(lo) != 1:
            continue
        a = lo[0]
        b = [x for x in sides if not x.get("long")][0]
        out.append({"lg": s[1], "t": m.get("gameStartTime") or "", "slug": m["slug"],
                    "a": f"{s[1]}:{a['d']}", "b": f"{s[1]}:{b['d']}",
                    "a_won": int(str(a.get("price")).startswith("1"))})
    out.sort(key=lambda x: x["t"])
    return out


def expect(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def run(ms, k):
    """Walk forward: each match is predicted from earlier matches only."""
    r, n, preds = defaultdict(lambda: 1500.0), defaultdict(int), []
    for m in ms:
        p = expect(r[m["a"]], r[m["b"]])
        preds.append((m, p, min(n[m["a"]], n[m["b"]])))
        d = k * (m["a_won"] - p)
        r[m["a"]] += d
        r[m["b"]] -= d
        n[m["a"]] += 1
        n[m["b"]] += 1
    return preds, r, n


def brier(preds):
    xs = [(p - m["a_won"]) ** 2 for m, p, nn in preds if nn >= MIN_N]
    return sum(xs) / len(xs) if xs else float("nan"), len(xs)


def fit(ms):
    train = [m for m in ms if m["t"] < FIT_END]
    best = None
    for k in (8, 12, 16, 20, 24, 32, 40, 48):
        b, n = brier(run(train, k)[0])
        print(f"  K={k:<3} train Brier {b:.4f} on {n} matches (both players >= {MIN_N} prior)")
        if best is None or b < best[1]:
            best = (k, b)
    k = best[0]
    preds = run(ms, k)[0]
    test = [x for x in preds if x[0]["t"] >= FIT_END]
    b, n = brier(test)
    print(f"chosen K={k}; after {FIT_END}: Elo Brier {b:.4f} on {n} matches (coin flip 0.2500)")
    bins = defaultdict(lambda: [0, 0])
    for m, p, nn in test:
        if nn >= MIN_N:
            fav, won = (p, m["a_won"]) if p >= 0.5 else (1 - p, 1 - m["a_won"])
            bins[min(int(fav * 10), 9)][0] += 1
            bins[min(int(fav * 10), 9)][1] += won
    for b_, (cnt, w) in sorted(bins.items()):
        print(f"    Elo favourite {b_ / 10:.1f}-{(b_ + 1) / 10:.1f}: n={cnt:<5} won {w / cnt:.3f}")
    json.dump({"k": k}, open("hunt/ttelo_k.json", "w"))


def last_quotes(cutoff_s=60):
    """Last OPEN snapshot >= cutoff_s before the scheduled start, per slug."""
    best = {}
    for fp in glob.glob("research/niche/rec-*.jsonl"):
        for line in open(fp):
            r = json.loads(line)
            st = r.get("start")
            if not st:
                continue
            t0 = time.mktime(time.strptime(st[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            if r["t"] <= t0 - cutoff_s and r["q"][4] == "MARKET_STATE_OPEN":
                if r["slug"] not in best or r["t"] > best[r["slug"]]["t"]:
                    best[r["slug"]] = r
    return best


def grade(ms):
    from src import sim
    k = json.load(open("hunt/ttelo_k.json"))["k"]
    preds = {m["slug"]: (p, nn, m) for m, p, nn in run(ms, k)[0]}
    q = last_quotes()
    both, trades, bm, be = 0, [], [], []
    for slug, r in q.items():
        if slug not in preds:
            continue
        p, nn, m = preds[slug]
        if nn < MIN_N:
            continue
        bid, _, ask, _, _ = r["q"]
        both += 1
        if bid is not None and ask is not None:
            bm.append(((bid + ask) / 2 - m["a_won"]) ** 2)
            be.append((p - m["a_won"]) ** 2)
        for side, pp, cost, won in (("a", p, ask, m["a_won"]), ("b", 1 - p, None if bid is None else 1 - bid, 1 - m["a_won"])):
            if cost is None or not 0 < cost < 1:
                continue
            if pp - (cost + FEE * cost * (1 - cost)) >= EDGE:
                trades.append({"t": r["t"], "game": slug, "cost": cost,
                               "pnl": won - cost - FEE * cost * (1 - cost)})
                break
    print(f"settled matches with a pre-start quote and rated players: {both}")
    if bm:
        print(f"  Brier on {len(bm)}: Polymarket mid {sum(bm) / len(bm):.4f} | Elo {sum(be) / len(be):.4f}")
    sim.describe(f"T2 entries (Elo edge >= {EDGE})", trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--grade", action="store_true")
    a = ap.parse_args()
    ms = matches()
    print(f"{len(ms)} settled table-tennis matches, {ms[0]['t'][:10]} .. {ms[-1]['t'][:10]}")
    if a.fit:
        fit(ms)
    if a.grade:
        grade(ms)


if __name__ == "__main__":
    main()
