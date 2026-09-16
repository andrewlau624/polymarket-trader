"""Run the strategy grid on per-match Polymarket markets (LoL plus others).

Fast-resolving match markets only. Every strategy is a signal threshold plus
an exit rule. Reports the best config and the family average, with a
recent/older split so luck is visible, and cost sensitivity.
"""

import argparse
import re
import sys
import time

import numpy as np

from src.pm.history import CATEGORY_TAGS, build_market, match_markets
from src.pm.signals import add_signals
from src.pm import stratgrid as sg


def load_matches(cat, max_events, max_markets_per_event=4):
    pairs = match_markets(CATEGORY_TAGS[cat], max_events=max_events,
                          max_markets_per_event=max_markets_per_event)
    out = []
    for ev, m in pairs:
        mk = build_market(cat, m, event=ev)
        if not mk:
            continue
        for tok, b in mk["bars"].items():
            mk["bars"][tok] = add_signals(b)
        mk["S"] = {tok: sg.to_arrays(b) for tok, b in mk["bars"].items()}
        out.append(mk)
    return out


def main():
    ap = argparse.ArgumentParser(description="Strategy-grid backtest on match markets.")
    ap.add_argument("--categories", default="LoL,Valorant,Sports")
    ap.add_argument("--max-events", type=int, default=200)
    ap.add_argument("--cost", type=float, default=0.01)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-trades", type=int, default=40)
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    data = {}
    for cat in cats:
        t0 = time.time()
        data[cat] = load_matches(cat, args.max_events)
        print(f"{cat}: {len(data[cat])} match markets ready ({time.time()-t0:.0f}s)", flush=True)

    configs = list(sg.grid())
    print(f"grid: {len(configs)} configs x {sum(len(d) for d in data.values())} markets "
          f"@ cost {args.cost}\n")

    results = []
    for label, cond, mode, kw in configs:
        trades_by_cat = {c: [] for c in cats}
        for cat in cats:
            for mk in data[cat]:
                trades_by_cat[cat].extend(sg.evaluate_market(mk, cond, mode, args.cost, **kw))
        allt = [t for c in cats for t in trades_by_cat[c]]
        s = sg.summarize(allt)
        if not s or s["n"] < args.min_trades:
            continue
        results.append((label, s, trades_by_cat))

    results.sort(key=lambda r: r[1]["cap_roi"], reverse=True)

    print(f"{'config':<34}{'n':>6}{'hit':>6}{'capROI':>9}{'medROI':>9}{'t':>6}")
    for label, s, _ in results[: args.top]:
        print(f"{label:<34}{s['n']:>6}{s['hit']:>6.2f}{s['cap_roi']:>9.3f}"
              f"{s['median_roi']:>9.3f}{s['t']:>6.1f}")

    print("\n--- summary by family (configs with a positive capROI) ---")
    fams = {}
    for label, s, _ in results:
        fam = re.sub(r"_k\d+_.*", "", label)
        fams.setdefault(fam, []).append(s["cap_roi"])
    for fam, vals in sorted(fams.items(), key=lambda x: -np.mean(x[1])):
        pos = sum(1 for v in vals if v > 0)
        print(f"  {fam:<16} configs={len(vals):>4}  mean_capROI={np.mean(vals):+.3f}  "
              f"best={max(vals):+.3f}  positive={pos}/{len(vals)}")

    print("\n--- stability of the top configs (recent vs older half) ---")
    for label, s, by_cat in results[: args.top]:
        line = f"  {label:<34}"
        for cat in cats:
            mk = data[cat]
            mid = len(mk) // 2
            # rebuild trades split by market halves
            cond = None
            for lb, c, mode, kw in configs:
                if lb == label:
                    cond, mode, kw = c, mode, kw
                    break
            for lbl, half in (("rec", mk[:mid]), ("old", mk[mid:])):
                tr = []
                for m in half:
                    tr.extend(sg.evaluate_market(m, cond, mode, args.cost, **kw))
                summ = sg.summarize(tr)
                line += f" {cat[:5]}/{lbl}={summ['cap_roi']:+.2f}" if summ else f" {cat[:5]}/{lbl}=  -  "
        print(line)

    print("\ncapROI = total PnL / total staked. A config is real only if it holds "
          "across both halves AND survives costs.")


if __name__ == "__main__":
    main()
