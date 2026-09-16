import argparse

import numpy as np
import yaml

from src.backtest import engine
from src.backtest.metrics import compute_metrics
from src.data import flows as flows_mod
from src.data import loader
from src.data.timeframe import days_to_bars, periods_per_year
from src.features.flow_features import add_flow_features
from src.features.swing_features import add_swing_features
from src.research.deflated_sharpe import prob_of_deflated_sharpe

DEFAULT_CONFIG = {"flow": "config.yaml", "swing": "config_swing.yaml"}


def load_config(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def main():
    ap = argparse.ArgumentParser(
        description="Walk-forward backtest for the crypto strategies."
    )
    ap.add_argument("--config", default=None, help="Config path (default depends on --strategy)")
    ap.add_argument("--strategy", choices=["flow", "swing"], default="flow")
    ap.add_argument("--refresh-ohlcv", action="store_true", help="Re-download OHLCV from exchange")
    ap.add_argument("--fetch-flows", action="store_true", help="Re-fetch ETF flow data")
    ap.add_argument("--symbol", help="Override symbol, e.g. BTC/USDT")
    ap.add_argument("--target-vol", type=float, help="Swing vol target (profit/risk knob)")
    ap.add_argument("--max-leverage", type=float, help="Swing max gross exposure")
    args = ap.parse_args()

    config_path = args.config or DEFAULT_CONFIG[args.strategy]
    config = load_config(config_path)
    if args.symbol:
        config["symbol"] = args.symbol
    config["strategy"].setdefault("name", "flow_persistence" if args.strategy == "flow" else "swing")
    if args.target_vol is not None:
        config["strategy"]["target_vol"] = args.target_vol
    if args.max_leverage is not None:
        config["strategy"]["max_leverage"] = args.max_leverage

    tf = config.get("timeframe", "1d")
    ppy = periods_per_year(tf)

    print(f"[{config['strategy']['name']}] Loading OHLCV: {config['symbol']} {tf} ...")
    ohlcv = loader.load_ohlcv(config, refresh=args.refresh_ohlcv)
    print(f"  {len(ohlcv)} bars: {ohlcv['timestamp'].iloc[0]} -> {ohlcv['timestamp'].iloc[-1]}")

    flows = None
    if config["strategy"]["name"] == "flow_persistence" or config["strategy"].get("use_flow"):
        print("Loading ETF net flows ...")
        flows = flows_mod.load_etf_flows(config, refresh=args.fetch_flows)
        print(f"  {len(flows)} flow days: {flows['date'].iloc[0]} -> {flows['date'].iloc[-1]}")

    if config["strategy"]["name"] == "flow_persistence":
        df = add_flow_features(ohlcv, flows, config)
    else:
        df = add_swing_features(ohlcv, config, flows=flows)

    returns = df["close"].pct_change().fillna(0.0).to_numpy()

    wf = engine.walk_forward_backtest(df, returns, config)
    fixed = engine.fixed_params_backtest(df, returns, config, config["strategy"])
    bh = engine.buy_and_hold(returns, config)

    start = days_to_bars(config["walk_forward"]["initial_history_days"], tf)
    region = slice(start, len(df))

    def region_metrics(res):
        return compute_metrics(res["returns"][region], res["position"][region], ppy)

    rows = {
        "buy_and_hold": region_metrics(bh),
        "fixed_params": region_metrics(fixed),
        "walk_forward": compute_metrics(wf["oos_returns"][region], wf["oos_position"][region], ppy),
    }

    print("\n=== Out-of-sample comparison ===")
    print(df["timestamp"].iloc[start].date(), "->", df["timestamp"].iloc[-1].date())
    hdr = (f"{'label':<14}{'CAGR':>9}{'AnnVol':>9}{'Sharpe':>9}{'Sortino':>9}"
           f"{'Calmar':>9}{'MaxDD':>9}{'PF':>7}{'Win':>7}{'Exp':>7}{'Trades':>8}")
    print(hdr)
    for label, m in rows.items():
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        print(
            f"{label:<14}{m['cagr']:>9.3f}{m['ann_vol']:>9.3f}{m['sharpe']:>9.2f}"
            f"{m['sortino']:>9.2f}{m['calmar']:>9.2f}{m['max_drawdown']:>9.3f}"
            f"{pf:>7}{m['win_rate']:>7.2f}{m['exposure']:>7.2f}{m['n_trades']:>8d}"
        )

    print(f"\nWalk-forward: {len(wf['window_log'])} windows, {wf['n_trials']} trials.")
    for w in wf["window_log"]:
        p = w["params"]
        if config["strategy"]["name"] == "swing":
            desc = (f"entry={p['entry_window']} atr={p['exit_atr_mult']} "
                    f"tvol={p['target_vol']}")

        else:
            desc = f"streak>={p['min_streak']} autocorr>={p['autocorr_min']}"
        print(f"  {w['date_start'].date()} -> {w['date_end'].date()}  "
              f"IS={w['in_sample_score']:+.2f}  {desc}")

    r = wf["oos_returns"][region]
    if len(r) > 2 and np.std(r, ddof=1) > 0:
        sr_bar = rows["walk_forward"]["sharpe"] / np.sqrt(ppy)
        mu, sd = r.mean(), r.std(ddof=1)
        skew = float(((r - mu) ** 3).mean() / sd**3)
        kurt = float(((r - mu) ** 4).mean() / sd**4)
        psd = prob_of_deflated_sharpe(sr_bar, wf["n_trials"], len(r), skew, kurt)
        print(f"\nDeflated Sharpe (walk-forward, {wf['n_trials']} trials): "
              f"prob = {psd:.3f}. Below ~0.95 means the OOS Sharpe is not "
              f"statistically distinguishable from luck after multiple testing.")

    print("\nDone. In-sample selection is curve-fitting by construction; "
          "the walk_forward row is the only honest number above.")


if __name__ == "__main__":
    main()
