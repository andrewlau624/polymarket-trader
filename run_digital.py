"""Fair value for crypto binaries, and a calibration test of the model itself.

A market like "BTC above $X on date D" is a digital (binary) option. Unlike a
football game, its underlying is free, continuous and sub-second observable,
so fair value is computable rather than guessed:

    P(S_T > K) = N(d2),  d2 = (ln(S/K) - 0.5*sigma^2*T) / (sigma*sqrt(T))

under a zero-drift (martingale) assumption, which is the right prior for a
traded asset over short horizons.

This script does NOT backtest a trading strategy. It asks the question that
has to come first: **is the model calibrated?** For thousands of historical
(time, horizon, strike) triples it computes the model probability and checks
what actually happened. If the model is not calibrated against reality, a gap
between it and a market price tells you nothing about who is wrong.

    python run_digital.py                        # BTC, default horizons
    python run_digital.py --asset ETH --halflife 48
    python run_digital.py --horizons 24,168

Expect the tails to be overconfident: realized vol understates crypto's fat
tails, so model p=0.97 tends to realize below 0.97. That bias is the output.
"""

import argparse
import math
import os

import numpy as np
import pandas as pd

DATA = {"BTC": os.path.join("data", "ohlcv_BTC_USD_1h.csv"),
        "ETH": os.path.join("data", "ohlcv_ETH_USD_1h.csv")}
EDGES = (0.02, 0.05, 0.15, 0.30, 0.45, 0.60, 0.75, 0.85, 0.95, 0.98)


def _ncdf(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def digital_call(spot, strike, sigma_h, hours, drift_h=0.0):
    """P(S_T > K). sigma_h is per-hour log-return vol; hours is the horizon."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    s = np.asarray(sigma_h, dtype=float) * math.sqrt(hours)
    s = np.where(s <= 1e-9, 1e-9, s)
    d2 = (np.log(spot / strike) + (drift_h * hours) - 0.5 * s * s) / s
    return _ncdf(d2)


def ewma_vol(close, halflife=72):
    """Per-hour log-return volatility, EWMA. Shifted so it uses only the past."""
    r = np.log(close).diff()
    v = r.pow(2).ewm(halflife=halflife, min_periods=halflife).mean()
    return np.sqrt(v).shift(1)


def build(asset, halflife, horizons, offsets, step, drift, vol_scale=1.0):
    df = pd.read_csv(DATA[asset])
    close = df["close"].astype(float).reset_index(drop=True)
    sig = ewma_vol(close, halflife=halflife)
    n = len(close)
    rows = []
    for H in horizons:
        idx = np.arange(halflife + 1, n - H, step)
        for i in idx:
            s0, sT, sg = close[i], close[i + H], sig[i] * vol_scale
            if not np.isfinite(sg) or sg <= 0:
                continue
            for k in offsets:
                K = s0 * (1.0 + k)
                p = float(digital_call(s0, K, sg, H, drift))
                rows.append({"H": H, "k": k, "p": p,
                             "win": 1.0 if sT > K else 0.0})
    return pd.DataFrame(rows)


def calib(obs, label, boot=1500, seed=11, min_n=40):
    rng = np.random.default_rng(seed)
    print(f"\n=== {label} (n={len(obs)}) ===")
    print(f"{'bucket':>12} {'n':>6} {'model p':>8} {'actual':>7} {'error':>8} {'95% CI':>19}")
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        b = obs[(obs.p >= lo) & (obs.p < hi)]
        if len(b) < min_n:
            continue
        err = float(b.win.mean() - b.p.mean())
        draws = [float(b.win.iloc[i].mean() - b.p.iloc[i].mean())
                 for i in (rng.integers(0, len(b), len(b)) for _ in range(boot))]
        c1, c2 = np.percentile(draws, [2.5, 97.5])
        star = " *" if (c1 > 0 or c2 < 0) else ""
        print(f"{lo:.2f}-{hi:.2f}".rjust(12) + f" {len(b):>6} {b.p.mean():>8.3f} "
              f"{b.win.mean():>7.3f} {err:>+8.3f}  [{c1:+.3f},{c2:+.3f}]{star}")
    print("  error = actual - model. Negative in the high buckets means the model")
    print("  is OVERCONFIDENT: it says 0.90 and reality delivers less.")


def main():
    ap = argparse.ArgumentParser(description="Digital-option fair value + calibration.")
    ap.add_argument("--asset", default="BTC", choices=sorted(DATA))
    ap.add_argument("--halflife", type=int, default=72, help="EWMA halflife, hours")
    ap.add_argument("--horizons", default="6,24,168", help="hours to expiry, comma-separated")
    ap.add_argument("--offsets", default="-0.05,-0.02,-0.01,0,0.01,0.02,0.05",
                    help="strikes as a fraction above/below spot")
    ap.add_argument("--step", type=int, default=6, help="sample every N hours")
    ap.add_argument("--drift", type=float, default=0.0, help="per-hour log drift")
    ap.add_argument("--vol-scale", type=float, default=1.0,
                    help="multiply the EWMA vol. 0.8 flattens BTC calibration and "
                         "improves Brier, but see RESEARCH.md S7: that value was "
                         "grid-searched in-sample and is NOT validated.")
    ap.add_argument("--by-horizon", action="store_true")
    args = ap.parse_args()

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    offsets = [float(k) for k in args.offsets.split(",") if k.strip()]
    obs = build(args.asset, args.halflife, horizons, offsets, args.step, args.drift,
                vol_scale=args.vol_scale)
    if obs.empty:
        raise SystemExit("no observations - check the OHLCV file")

    brier = float(((obs.p - obs.win) ** 2).mean())
    base = float(obs.win.mean())
    ref = float(((base - obs.win) ** 2).mean())
    print(f"{args.asset}  halflife={args.halflife}h  horizons={horizons}  "
          f"n={len(obs)}")
    print(f"Brier {brier:.4f} vs always-{base:.2f} baseline {ref:.4f}  "
          f"(skill {1 - brier / ref:+.1%})")
    calib(obs, f"{args.asset}, all horizons")
    if args.by_horizon:
        for H in horizons:
            calib(obs[obs.H == H], f"{args.asset}, {H}h to expiry")


if __name__ == "__main__":
    main()
