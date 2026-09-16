"""Optimize the market maker's quote distance using the historical tape.

For each match market we replay the tape with a resting two-sided quote at
mid +/- d and measure fills, spread capture and adverse selection. The goal
is the distance that maximizes net PnL after adverse selection.
"""

import argparse
import time

import numpy as np

from src.pm import mm_sim
from src.pm.history import CATEGORY_TAGS, build_market, fetch_trades, match_markets

D_GRID = (0.005, 0.010, 0.015, 0.020, 0.030, 0.045)


def load(cat, max_events, size_hint):
    out = []
    for ev, m in match_markets(CATEGORY_TAGS[cat], max_events=max_events, max_markets_per_event=4):
        mk = build_market(cat, m, event=ev)
        if not mk:
            continue
        try:
            mk["trades"] = fetch_trades(mk["condition_id"])
        except Exception:
            continue
        if mk["trades"] is None or len(mk["trades"]) < 60:
            continue
        out.append(mk)
    return out


def main():
    ap = argparse.ArgumentParser(description="Optimize MM quote distance on the tape.")
    ap.add_argument("--categories", default="LoL,Valorant")
    ap.add_argument("--max-events", type=int, default=150)
    ap.add_argument("--size", type=float, default=100.0)
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    data = {}
    for cat in cats:
        t0 = time.time()
        data[cat] = load(cat, args.max_events, args.size)
        print(f"{cat}: {len(data[cat])} markets ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n{'d(cents)':>9} " + "".join(f"{c:>26}" for c in cats))
    print(f"{'':>9} " + "".join(f"{'fills':>7}{'pnl/mkt':>9}{'adv/fill':>10}" for _ in cats))
    for d in D_GRID:
        line = f"{d*100:>9.2f} "
        for cat in cats:
            results = []
            for mk in data[cat]:
                r = mm_sim.simulate_market(mk, d, args.size)
                results.extend(r.values())
            s = mm_sim.summarize(results)
            if s:
                line += f"{s['fills']:>7.1f}{s['pnl_mean']:>9.2f}{s['adverse_mean']:>10.4f}"
            else:
                line += f"{'-':>26}"
        print(line)

    print("\nadv/fill = avg signed price move 10 trades after a fill (+ = favourable).")
    print("pnl/mkt  = cash + inventory marked to resolution, per market (shares held).")


if __name__ == "__main__":
    main()
