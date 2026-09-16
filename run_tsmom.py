import argparse
import glob
import os

import numpy as np
import pandas as pd

from src.backtest.metrics import compute_metrics
from src.data.universe import CACHE_DIR
from src.research.deflated_sharpe import prob_of_deflated_sharpe
from src.strategy.tsmom import tsmom_backtest

COST = 0.001

GRID = {
    "trend_fast": [24, 48, 96, 168, 240],
    "trend_slow": [96, 192, 480, 960],
    "vol_window": [30, 60],
    "long_only": [True, False],
    "rebalance": [1, 7],
}
BASE = {"max_lev": 2.0, "target_vol": 0.35}


def load_close():
    frames = {}
    for f in glob.glob(os.path.join(CACHE_DIR, "okx_*_1d.csv")):
        base = os.path.basename(f).split("_")[1]
        df = pd.read_csv(f, parse_dates=["timestamp"])
        s = df.set_index("timestamp")["close"]
        s.index = s.index.tz_localize(None).normalize()
        frames[base] = s
    if not frames:
        raise FileNotFoundError(f"no universe cache in {CACHE_DIR}; run the fetcher first")
    return pd.DataFrame(frames).sort_index()


def grid_iter():
    import itertools
    keys = list(GRID)
    for vals in itertools.product(*(GRID[k] for k in keys)):
        p = dict(zip(keys, vals))
        if p["trend_fast"] >= p["trend_slow"]:
            continue
        yield {**BASE, **p}


def deflated(net, n_trials):
    net = net[np.isfinite(net)]
    if len(net) < 3 or net.std(ddof=1) == 0:
        return None
    sr = net.mean() / net.std(ddof=1) * np.sqrt(365)
    mu, sd = net.mean(), net.std(ddof=1)
    skew = float(((net - mu) ** 3).mean() / sd**3)
    kurt = float(((net - mu) ** 4).mean() / sd**4)
    return prob_of_deflated_sharpe(sr / np.sqrt(365), n_trials, len(net), skew, kurt)


def main():
    ap = argparse.ArgumentParser(description="Diversified crypto TSMOM with a dev/test holdout.")
    ap.add_argument("--dev-frac", type=float, default=0.70)
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    close = load_close()
    n = len(close)
    cut = int(n * args.dev_frac)
    print(f"Universe {close.shape[1]} coins, {n} days {close.index[0].date()} -> {close.index[-1].date()}")
    print(f"dev: <{close.index[cut].date()}   test: >= {close.index[cut].date()}")

    results = []
    for p in grid_iter():
        net, gross, _ = tsmom_backtest(close, p, cost=COST)
        dev = compute_metrics(net.iloc[:cut].to_numpy(), gross.iloc[:cut].to_numpy(), 365)
        results.append((dev["sharpe"], p, net, gross))
    results.sort(key=lambda x: x[0], reverse=True)
    print(f"evaluated {len(results)} configurations on dev")

    def test_metrics(net, gross):
        return compute_metrics(net.iloc[cut:].to_numpy(), gross.iloc[cut:].to_numpy(), 365)

    best_dev, best_p, best_net, best_gross = results[0]
    print(f"\nbest dev params: {best_p}")
    print(f"  dev sharpe={best_dev:+.2f}")

    # ensemble of the top-K dev configs (equal-weight the return streams)
    ens_net = sum(r[2] for r in results[: args.top_k]) / args.top_k
    ens_gross = sum(r[3] for r in results[: args.top_k]) / args.top_k
    # re-scale the ensemble to the target vol like the singles
    scale = BASE["target_vol"] / (ens_net.rolling(60).std() * np.sqrt(365)).clip(lower=1e-6)
    scale = scale.clip(upper=BASE["max_lev"]).shift(1).fillna(0.0)
    ens_net = ens_net * scale
    ens_gross = ens_gross * scale

    btc = close["BTC"].pct_change().fillna(0.0)
    ew = close.pct_change().mean(axis=1).fillna(0.0)
    btc_m = compute_metrics(btc.iloc[cut:].to_numpy(), np.ones(n - cut), 365)
    ew_m = compute_metrics(ew.iloc[cut:].to_numpy(), np.ones(n - cut), 365)

    rows = {
        "tsmom_best": test_metrics(best_net, best_gross),
        f"tsmom_ens{args.top_k}": test_metrics(ens_net, ens_gross),
        "btc_buy_hold": btc_m,
        "equal_weight": ew_m,
    }
    print("\n=== TEST (holdout) ===")
    hdr = f"{'label':<16}{'CAGR':>9}{'AnnVol':>9}{'Sharpe':>9}{'Sortino':>9}{'Calmar':>9}{'MaxDD':>9}{'PF':>7}"
    print(hdr)
    for label, m in rows.items():
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        print(f"{label:<16}{m['cagr']:>9.3f}{m['ann_vol']:>9.3f}{m['sharpe']:>9.2f}"
              f"{m['sortino']:>9.2f}{m['calmar']:>9.2f}{m['max_drawdown']:>9.3f}{pf:>7}")

    n_trials = len(results) * args.top_k
    psd = deflated(ens_net.iloc[cut:].to_numpy(), n_trials)
    print(f"\nDeflated Sharpe (ensemble, {n_trials} trials): "
          f"{None if psd is None else round(psd, 3)}")


if __name__ == "__main__":
    main()
