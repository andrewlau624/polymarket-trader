import itertools

import numpy as np

from src.backtest.costs import strategy_returns
from src.backtest.metrics import compute_metrics
from src.data.timeframe import days_to_bars, periods_per_year
from src.strategy.flow_persistence import flow_persistence_position
from src.strategy.swing import swing_position

FLOW_GRID = {
    "min_streak": [2, 3, 4, 5],
    "autocorr_min": [0.1, 0.3, 0.5],
}

SWING_GRID = {
    "entry_window": [96, 168, 240],
    "exit_atr_mult": [2.0, 3.0, 4.0],
    "target_vol": [0.6, 0.8, 1.0],
}


def strategy_name(config):
    return config["strategy"].get("name", "flow_persistence")


def _ppy(config):
    return periods_per_year(config.get("timeframe", "1d"))


def _all_param_combos(config):
    if strategy_name(config) == "swing":
        keys = list(SWING_GRID.keys())
        return [dict(zip(keys, vals)) for vals in itertools.product(*(SWING_GRID[k] for k in keys))]

    base = {
        "exit_autocorr": float(config["strategy"]["exit_autocorr"]),
        "exit_flow_usd": float(config["strategy"]["exit_flow_usd"]),
    }
    combos = []
    for ms in FLOW_GRID["min_streak"]:
        for ac in FLOW_GRID["autocorr_min"]:
            combos.append({"min_streak": ms, "autocorr_min": ac, **base})
    return combos


def run_position(df, params, config, initial=0):
    if strategy_name(config) == "swing":
        return swing_position(df, params, config, initial_dir=float(initial))
    strength = float(config["flows"]["strength_min_usd"])
    return flow_persistence_position(df, params, strength, initial_long=bool(initial))


def _walk_forward(df, returns, config, train_bars, step_bars, start, best_fn):
    n = len(df)
    ppy = _ppy(config)
    oos_pos = np.zeros(n, dtype=float)
    window_log = []
    trials = 0
    state = 0
    lo = start
    while lo < n:
        hi = min(lo + step_bars, n)
        train_lo = max(0, lo - train_bars)
        train = slice(train_lo, lo)

        best = None
        best_score = -np.inf
        for params in _all_param_combos(config):
            trials += 1
            tr_pos, _ = run_position(df.iloc[train], params, config, initial=0)
            tr_ret = strategy_returns(returns[train], tr_pos, config)
            m = compute_metrics(tr_ret, tr_pos, ppy)
            score = best_fn(m)
            if score > best_score:
                best_score = score
                best = params

        te_pos, state = run_position(df.iloc[lo:hi], best, config, initial=state)
        oos_pos[lo:hi] = te_pos
        window_log.append(
            {
                "window_start": lo,
                "window_end": hi,
                "date_start": df["timestamp"].iloc[lo],
                "date_end": df["timestamp"].iloc[hi - 1],
                "params": best,
                "in_sample_score": best_score,
            }
        )
        lo = hi

    oos_ret = strategy_returns(returns, oos_pos, config)
    return {
        "metrics": compute_metrics(oos_ret, oos_pos, ppy),
        "window_log": window_log,
        "oos_position": oos_pos,
        "oos_returns": oos_ret,
        "n_trials": trials,
    }


def walk_forward_backtest(df, returns, config, best_fn=None):
    if best_fn is None:
        best_fn = lambda m: m["sharpe"]  # noqa: E731
    wf = config["walk_forward"]
    tf = config.get("timeframe", "1d")
    train_bars = days_to_bars(wf["train_days"], tf)
    step_bars = days_to_bars(wf["step_days"], tf)
    start = days_to_bars(wf["initial_history_days"], tf)
    return _walk_forward(df, returns, config, train_bars, step_bars, start, best_fn)


def fixed_params_backtest(df, returns, config, params):
    pos, _ = run_position(df, params, config, initial=0)
    ret = strategy_returns(returns, pos, config)
    return {"metrics": compute_metrics(ret, pos, _ppy(config)), "position": pos, "returns": ret}


def buy_and_hold(returns, config=None):
    pos = np.ones(len(returns), dtype=float)
    ret = returns.copy()
    ppy = _ppy(config) if config else 365
    return {"metrics": compute_metrics(ret, pos, ppy), "position": pos, "returns": ret}
