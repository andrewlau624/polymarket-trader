"""Does an in-play price move predict the NEXT move, net of costs?

This is the load-bearing question for day-trading event markets. Betting on
the outcome is a different trade; this one is about round-tripping the swings.

Method: rebuild size-weighted N-minute bars per outcome token from the cached
trade tape, then relate the past K-bar move to the forward H-bar move.

  * Positive relation  -> momentum: ride the move.
  * Negative relation  -> mean reversion: fade the move.
  * Nothing            -> the tape is a martingale intraday and there is no
                          swing trade here, only the bet.

BID/ASK BOUNCE is the trap. Trades print alternately at bid and ask, which
manufactures negative autocorrelation that looks exactly like mean reversion.
Two defences: bars are size-weighted averages of many prints, and a gap of
one full bar is skipped between the past window and the forward window, so
the last print of the signal is never the first print of the result.

    python run_swing_offline.py
    python run_swing_offline.py --bar 10 --fwd 6 --cost 0.02
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd

TAPE = os.path.join("data", "pm_trades")
META = os.path.join("data", "pm_market_meta.json")


def load_labels():
    if not os.path.exists(META):
        return {}
    import json
    import label_tape
    meta = json.load(open(META))
    return {c: label_tape.classify(f"{m.get('question','')} {m.get('event','')} "
                                   f"{m.get('event_slug','')}") for c, m in meta.items()}


def bars_for(df, outcome, bar_min):
    s = df[df["outcome"] == outcome].copy()
    if len(s) < 30:
        return None
    s["t"] = pd.to_datetime(s["timestamp"], unit="s")
    s["w"] = s["size"].abs().clip(lower=1e-9)
    s["pw"] = s["price"] * s["w"]
    g = s.set_index("t").resample(f"{bar_min}min").agg({"pw": "sum", "w": "sum"})
    g = g[g.w > 0]
    if len(g) < 12:
        return None
    return (g.pw / g.w).astype(float)


def collect(bar_min, past, fwd, min_trades, labels, gap=1):
    rows = []
    for fp in sorted(glob.glob(os.path.join(TAPE, "*.csv"))):
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if len(df) < min_trades or "outcome" not in df.columns:
            continue
        cat = labels.get(os.path.basename(fp)[:-4], "Unlabelled")
        for oc in df["outcome"].dropna().unique():
            p = bars_for(df, oc, bar_min)
            if p is None:
                continue
            v = p.values
            # gap of one bar between signal and result kills same-print overlap
            for i in range(past, len(v) - fwd - gap):
                p0 = v[i]
                if not (0.02 < p0 < 0.98):
                    continue
                r_past = p0 - v[i - past]
                r_fwd = v[i + gap + fwd] - v[i + gap]
                if np.isfinite(r_past) and np.isfinite(r_fwd):
                    rows.append({"cat": cat, "p": p0, "past": r_past, "fwd": r_fwd})
    return pd.DataFrame(rows)


def report(obs, label, cost, boot=1500, seed=5):
    rng = np.random.default_rng(seed)
    if len(obs) < 200:
        print(f"\n=== {label}: only {len(obs)} observations, skipped ===")
        return
    beta = np.polyfit(obs.past, obs.fwd, 1)[0]
    corr = float(np.corrcoef(obs.past, obs.fwd)[0, 1])
    print(f"\n=== {label} (n={len(obs):,}) ===")
    print(f"  slope of forward move on past move: {beta:+.4f}   corr {corr:+.4f}")
    print(f"  {'past move':>14} {'n':>7} {'mean fwd':>10} {'ride net':>11} "
          f"{'fade net':>10} {'t(fade)':>7}")
    for lo, hi, name in ((-9, -0.05, "fell >5c"), (-0.05, -0.02, "fell 2-5c"),
                         (-0.02, 0.02, "flat"), (0.02, 0.05, "rose 2-5c"),
                         (0.05, 9, "rose >5c")):
        b = obs[(obs.past >= lo) & (obs.past < hi)]
        if len(b) < 100:
            continue
        sign = 1.0 if (lo + hi) / 2 > 0 else -1.0
        mom = sign * b.fwd - cost                 # ride it
        rev = -sign * b.fwd - cost                # fade it
        def tstat(x):
            sd = x.std(ddof=1)
            return x.mean() / sd * np.sqrt(len(x)) if sd > 0 else 0.0
        print(f"  {name:>14} {len(b):>7,} {b.fwd.mean():>+10.4f} {mom.mean():>+11.4f} "
              f"{rev.mean():>+10.4f} {tstat(rev):>7.1f}")
    print(f"  costs {cost:.3f} round trip. 'fade' = trade AGAINST the past move,")
    print(f"  which is the direction a negative slope says should pay.")
    print("  Positive slope => momentum; negative => reversion; ~0 => no swing trade.")


def main():
    ap = argparse.ArgumentParser(description="In-play momentum vs reversion.")
    ap.add_argument("--bar", type=int, default=5, help="bar size in minutes")
    ap.add_argument("--past", type=int, default=3, help="signal lookback in bars")
    ap.add_argument("--fwd", type=int, default=3, help="holding period in bars")
    ap.add_argument("--cost", type=float, default=0.02,
                    help="round-trip cost in probability units (spread)")
    ap.add_argument("--min-trades", type=int, default=60)
    ap.add_argument("--gap", type=int, default=1,
                    help="bars skipped between signal and entry; raise it to test "
                         "whether an apparent reversion is just bid/ask bounce")
    ap.add_argument("--by-category", action="store_true")
    args = ap.parse_args()

    labels = load_labels()
    obs = collect(args.bar, args.past, args.fwd, args.min_trades, labels,
                  gap=args.gap)
    if obs.empty:
        raise SystemExit("no observations")
    print(f"bars {args.bar}min | signal {args.past} bars | hold {args.fwd} bars "
          f"| cost {args.cost}")
    report(obs, "all markets", args.cost)
    if args.by_category:
        for cat, n in obs.cat.value_counts().items():
            report(obs[obs.cat == cat], cat, args.cost)


if __name__ == "__main__":
    main()
