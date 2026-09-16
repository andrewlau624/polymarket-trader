"""Diversified multi-asset trend portfolio (the current best survivor).

Applies one validated design — long-only Donchian breakout (168h) with a slow
(~40 day) trend filter, an ATR trailing stop and volatility-targeted sizing —
to every available 1h asset, then averages the books. Diversifying the same
rule across assets is what lifts the risk-adjusted return and, crucially,
tests the design on independent markets instead of curve-fitting one.
"""

import argparse
import glob
import os

import numpy as np
import yaml

from src.backtest.costs import strategy_returns
from src.backtest.metrics import compute_metrics
from src.data import loader
from src.data.timeframe import periods_per_year
from src.features.genome_features import add_genome_features
from src.research.deflated_sharpe import prob_of_deflated_sharpe
from src.strategy.genome import genome_position


def design_genome(config):
    s = config["strategy"]
    return {
        "trend_fast": int(s["trend_fast"]),
        "trend_slow": int(s["trend_slow"]),
        "entry": "donchian",
        "entry_window": int(s["entry_window"]),
        "roc_thr": 0.0,
        "rsi_low": 30,
        "rsi_high": 70,
        "slope_bars": int(s.get("slope_bars", 0)),
        "trend_min": float(s.get("trend_min", 0.0)),
        "vol_cap": None,
        "exit": "atr",
        "atr_mult": float(s["exit_atr_mult"]),
        "exit_bars": 48,
        "allow_short": bool(s.get("allow_short", False)),
        "target_vol": float(s["target_vol"]),
        "max_leverage": float(s["max_leverage"]),
        "cooldown": int(s.get("cooldown", 72)),
        "use_flow": False,
    }


def discover_assets():
    out = {}
    for path in sorted(glob.glob(os.path.join("data", "ohlcv_*_1h.csv"))):
        if os.path.getsize(path) < 10_000:  # skip empty/aborted caches
            continue
        name = os.path.basename(path)[len("ohlcv_"):-len("_1h.csv")]
        out[name] = path
    return out


def main():
    ap = argparse.ArgumentParser(description="Multi-asset trend portfolio backtest.")
    ap.add_argument("--config", default="config_swing.yaml")
    ap.add_argument("--test-frac", type=float, default=0.27)
    args = ap.parse_args()

    config = yaml.safe_load(open(args.config))
    genome = design_genome(config)
    tf = config.get("timeframe", "1h")
    ppy = periods_per_year(tf)

    books = {}
    for name, path in discover_assets().items():
        cfg = {**config, "symbol": name.replace("_", "/")}
        df = add_genome_features(loader.load_ohlcv(cfg), cfg)
        ret = df["close"].pct_change().fillna(0.0).to_numpy()
        pos, _ = genome_position(df, genome, cfg)
        net = strategy_returns(ret, pos, cfg)
        books[name] = (df, pos, net, ret, cfg)
        print(f"  {name}: {len(df)} bars")

    n = min(len(v[2]) for v in books.values())
    port = np.mean([v[2][:n] for v in books.values()], axis=0)
    gross = np.mean([np.abs(v[1][:n]) for v in books.values()], axis=0)
    cut = int(n * (1 - args.test_frac))
    start = int(n * 0.12)

    print(f"\nportfolio: {len(books)} assets, {n} bars, "
          f"{list(books.values())[0][0]['timestamp'].iloc[0].date()} -> "
          f"{list(books.values())[0][0]['timestamp'].iloc[-1].date()}")

    rows = {
        "portfolio_full": compute_metrics(port[start:], gross[start:], ppy),
        "portfolio_test": compute_metrics(port[cut:], gross[cut:], ppy),
    }
    # benchmark: BTC buy & hold on the same test slice
    btc_ret = books["BTC_USD"][3][:n] if "BTC_USD" in books else list(books.values())[0][3][:n]
    rows["btc_bh_test"] = compute_metrics(btc_ret[cut:], np.ones(n - cut), ppy)

    print(f"\n=== {'label':<16}{'CAGR':>9}{'AnnVol':>9}{'Sharpe':>9}{'Calmar':>9}{'MaxDD':>9}{'PF':>7}")
    for label, m in rows.items():
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        print(f"    {label:<16}{m['cagr']:>9.3f}{m['ann_vol']:>9.3f}{m['sharpe']:>9.2f}"
              f"{m['calmar']:>9.2f}{m['max_drawdown']:>9.3f}{pf:>7}")

    net = port[cut:]
    mu, sd = net.mean(), net.std(ddof=1)
    skew = float(((net - mu) ** 3).mean() / sd**3)
    kurt = float(((net - mu) ** 4).mean() / sd**4)
    sr = rows["portfolio_test"]["sharpe"] / np.sqrt(ppy)
    print("\nDeflated Sharpe for the test slice:")
    for nt in (20, 50, 200):
        print(f"  n_trials={nt:>3}: prob={prob_of_deflated_sharpe(sr, nt, len(net), skew, kurt):.3f}")
    print("\nNOTE: >0.95 would mean the OOS Sharpe survives multiple-testing correction.")


if __name__ == "__main__":
    main()
