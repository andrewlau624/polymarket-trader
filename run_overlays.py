"""Compare alternative-data overlays on the multi-asset trend portfolio.

Data wired in here:
  - macro (Yahoo): dollar, yields, VIX, S&P, gold  -> src/data/macro.py
  - stablecoin supply (DefiLlama)                  -> src/data/stablecoins.py
  - Fear & Greed (alternative.me)                  -> src/data/sentiment.py

Each overlay is applied to the validated BTC+ETH trend book and scored on a
held-out test slice, so we can see honestly whether the extra data helps.
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd
import yaml

from src.backtest.costs import strategy_returns
from src.backtest.metrics import compute_metrics
from src.data import loader
from src.data.macro import load_macro, macro_features
from src.data.sentiment import load_fear_greed
from src.data.stablecoins import load_stablecoins, stablecoin_features
from src.data.timeframe import periods_per_year
from src.features.genome_features import add_genome_features
from src.research.deflated_sharpe import prob_of_deflated_sharpe
from src.strategy.genome import genome_position
from run_multitrend import design_genome, discover_assets


def build_daily_regime():
    mf = macro_features(load_macro(verbose=False))
    sc = stablecoin_features(load_stablecoins(verbose=False))
    fng = load_fear_greed(verbose=False).set_index("date")["fng"]
    reg = pd.DataFrame(index=mf.index)
    reg["risk_on"] = mf["risk_on"]
    reg["risk_off"] = 1.0 - mf["risk_on"]
    reg["sc_trend"] = sc["sc_trend"].reindex(mf.index).ffill()
    reg["fng"] = fng.reindex(mf.index).ffill()
    return reg.shift(1)  # lag a day: usable before the day starts


def main():
    ap = argparse.ArgumentParser(description="Alternative-data overlays on the trend portfolio.")
    ap.add_argument("--config", default="config_swing.yaml")
    ap.add_argument("--test-frac", type=float, default=0.27)
    args = ap.parse_args()

    config = yaml.safe_load(open(args.config))
    genome = design_genome(config)
    long_genome = {**genome, "allow_short": True}
    reg = build_daily_regime()
    tf = config.get("timeframe", "1h")
    ppy = periods_per_year(tf)

    books = {}
    for name, path in discover_assets().items():
        cfg = {**config, "symbol": name.replace("_", "/")}
        df = add_genome_features(loader.load_ohlcv(cfg), cfg)
        ret = df["close"].pct_change().fillna(0.0).to_numpy()
        pos_long = genome_position(df, genome, cfg)[0]
        pos_ls = genome_position(df, long_genome, cfg)[0]
        day = df["timestamp"].dt.normalize()
        books[name] = {
            "cfg": cfg, "ret": ret, "pos_long": pos_long, "pos_ls": pos_ls, "day": day,
        }
        print(f"  {name}: {len(df)} bars")

    n = min(len(b["ret"]) for b in books.values())
    st = int(n * 0.12)
    cut = int(n * (1 - args.test_frac))

    def series(overlay):
        accs = []
        for b in books.values():
            ret = b["ret"][:n]
            pos = b["pos_long"][:n].copy()
            if overlay == "base":
                pass
            elif overlay == "macro_gate":
                g = b["day"].map(reg["risk_on"]).ffill().fillna(0.0).to_numpy()[:n]
                pos = pos * g
            elif overlay == "sc_gate":
                g = b["day"].map(reg["sc_trend"]).ffill().fillna(0.0).to_numpy()[:n]
                pos = pos * g
            elif overlay == "anti_greed":
                f = b["day"].map(reg["fng"]).ffill().fillna(50).to_numpy()[:n]
                pos = pos * np.where(f > 75, 0.5, 1.0)
            elif overlay == "macro_short":
                g = b["day"].map(reg["risk_off"]).ffill().fillna(0).to_numpy()[:n]
                short_leg = np.where(g > 0, np.minimum(b["pos_ls"][:n], 0.0), 0.0)
                pos = pos + short_leg
            accs.append(strategy_returns(ret, pos, b["cfg"]))
        return np.mean(accs, axis=0)

    gross = np.mean([np.abs(b["pos_long"][:n]) for b in books.values()], axis=0)

    print(f"\n=== {'overlay':<16}{'full_sh':>9}{'full_cagr':>11}{'full_dd':>9}"
          f"{'test_sh':>9}{'test_cagr':>11}{'test_dd':>9}{'defl':>7}")
    for overlay in ("base", "macro_gate", "sc_gate", "anti_greed", "macro_short"):
        p = series(overlay)
        f = compute_metrics(p[st:], gross[st:], ppy)
        t = compute_metrics(p[cut:], gross[cut:], ppy)
        net = p[cut:]
        mu, sd = net.mean(), net.std(ddof=1)
        skew = float(((net - mu) ** 3).mean() / sd**3)
        kurt = float(((net - mu) ** 4).mean() / sd**4)
        psd = prob_of_deflated_sharpe(t["sharpe"] / np.sqrt(ppy), 50, len(net), skew, kurt)
        print(f"{overlay:<18}{f['sharpe']:>7.2f}{f['cagr']:>11.3f}{f['max_drawdown']:>9.3f}"
              f"{t['sharpe']:>9.2f}{t['cagr']:>11.3f}{t['max_drawdown']:>9.3f}{psd:>7.3f}")

    print("\nbase = BTC+ETH trend portfolio. defl = deflated Sharpe prob (50 trials) on test.")


if __name__ == "__main__":
    main()
