"""Are prices calibrated? Realized win rate vs price, from the cached trade tape.

Efficient markets put win rate == price. A bucket where they differ is the only
directional edge that survives when every logical-arbitrage relation is already
in the graveyard.

    python run_calibration.py                    # headline table
    python run_calibration.py --both-legs        # (biased) both outcomes/market
    python run_calibration.py --cut 0.5          # sample earlier in the tape
    python run_calibration.py --split-half       # stability check

CAVEATS, because this method can manufacture a result if you let it:

  * Resolution is INFERRED from terminal tape prices (one side -> ~1, the other
    -> ~0). A market whose price is already decided at the sample point is
    therefore near-deterministic by construction. --cut controls how much of
    the tail to exclude; the edge should survive raising it.
  * Both legs of one market are perfectly anti-correlated. Sampling both halves
    the effective sample size and narrows the CI dishonestly, so one leg is the
    default and --both-legs is offered only to show the difference.
  * Only markets that resolved cleanly and traded enough are kept, which is a
    survivorship filter toward decisive outcomes.
  * This tape is Polymarket GLOBAL (esports/politics/crypto). Transfer to the
    US sports venue is an assumption, not a result.

Treat a surviving bucket as a candidate to validate forward, never as a signal
to size up on.
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd

TAPE = os.path.join("data", "pm_trades")
DEFAULT_EDGES = (0.05, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 0.95)


def load_observations(cut=0.30, one_leg=True, min_trades=40, tape=TAPE):
    """(market, sampled price, realized win) per outcome token."""
    rows = []
    for fp in sorted(glob.glob(os.path.join(tape, "*.csv"))):
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if len(df) < min_trades or "outcome" not in df.columns:
            continue
        df = df.sort_values("timestamp")
        outs = list(df["outcome"].dropna().unique())
        if len(outs) != 2:
            continue

        # infer the winner from where the tape ends up
        term = {}
        for o in outs:
            s = df[df["outcome"] == o]
            if len(s) < 3:
                term = {}
                break
            term[o] = float(s["price"].tail(3).mean())
        if len(term) != 2:
            continue
        hi, lo = max(term, key=term.get), min(term, key=term.get)
        if not (term[hi] >= 0.95 and term[lo] <= 0.05):
            continue

        t0, t1 = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
        if t1 <= t0:
            continue
        cutoff = t1 - cut * (t1 - t0)

        legs = ["Yes"] if (one_leg and "Yes" in outs) else outs
        for o in legs:
            s = df[(df["outcome"] == o) & (df["timestamp"] <= cutoff)]
            if len(s) < 8:
                continue
            # average a few prints around the midpoint to damp bid/ask bounce
            mid = s.iloc[max(len(s) // 2 - 3, 0): len(s) // 2 + 4]
            p = float(mid["price"].mean())
            if 0.0 < p < 1.0:
                rows.append({"mkt": os.path.basename(fp), "p": p,
                             "win": 1.0 if o == hi else 0.0})
    return pd.DataFrame(rows)


def table(obs, edges=DEFAULT_EDGES, boot=2000, seed=7, min_n=25):
    rng = np.random.default_rng(seed)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        b = obs[(obs.p >= lo) & (obs.p < hi)]
        if len(b) < min_n:
            continue
        edge = float(b.win.mean() - b.p.mean())
        draws = [float(b.win.iloc[i].mean() - b.p.iloc[i].mean())
                 for i in (rng.integers(0, len(b), len(b)) for _ in range(boot))]
        c1, c2 = np.percentile(draws, [2.5, 97.5])
        out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "n": len(b),
                    "avg_px": float(b.p.mean()), "win": float(b.win.mean()),
                    "edge": edge, "lo": float(c1), "hi": float(c2),
                    "sig": bool(c1 > 0 or c2 < 0)})
    return out


def show(rows, label):
    print(f"\n=== {label} ===")
    if not rows:
        print("  (no bucket had enough observations)")
        return
    print(f"{'bucket':>12} {'n':>5} {'avg px':>7} {'win':>6} {'edge':>8} {'95% CI':>19}")
    for r in rows:
        star = " *" if r["sig"] else ""
        print(f"{r['bucket']:>12} {r['n']:>5} {r['avg_px']:>7.3f} {r['win']:>6.3f} "
              f"{r['edge']:>+8.3f}  [{r['lo']:+.3f},{r['hi']:+.3f}]{star}")
    print("  edge = realized win rate - price. * = bootstrap CI excludes zero.")
    print("  A 2-5c edge is inside the spread on a thin book and is not harvestable")
    print("  by crossing; it only pays if you are the maker.")


def main():
    ap = argparse.ArgumentParser(description="Price calibration on the cached tape.")
    ap.add_argument("--cut", type=float, default=0.30,
                    help="fraction of each tape's tail to exclude when sampling")
    ap.add_argument("--both-legs", action="store_true",
                    help="sample both outcomes per market (correlated; narrows CIs dishonestly)")
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--split-half", action="store_true",
                    help="also report the edge on two disjoint halves of the markets")
    ap.add_argument("--tape", default=TAPE)
    args = ap.parse_args()

    obs = load_observations(cut=args.cut, one_leg=not args.both_legs,
                            min_trades=args.min_trades, tape=args.tape)
    if obs.empty:
        raise SystemExit(f"no usable observations under {args.tape}")
    legs = "both legs" if args.both_legs else "one leg"
    show(table(obs), f"{legs} per market, sampled before the last {args.cut:.0%} "
                     f"(n={len(obs)}, {obs.mkt.nunique()} markets)")

    if args.split_half:
        h = obs.mkt.map(lambda s: hash(s) % 2)
        for half in (0, 1):
            sub = obs[h == half]
            for name, lo, hi in (("longshots", 0.05, 0.45), ("favorites", 0.60, 0.95)):
                b = sub[(sub.p >= lo) & (sub.p < hi)]
                if len(b) >= 25:
                    print(f"  split {half} {name:<10} {lo:.2f}-{hi:.2f}: "
                          f"edge {b.win.mean() - b.p.mean():+.3f} (n={len(b)})")
        print("\n  Same sign in both halves is the minimum bar. Different magnitudes")
        print("  mean the point estimate is not stable - size on the weaker one.")


if __name__ == "__main__":
    main()
