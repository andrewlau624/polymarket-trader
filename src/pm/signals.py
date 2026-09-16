"""Signal library for per-match Polymarket bars.

Everything is computed from the per-minute trade tape: price momentum over
several horizons, signed-flow imbalance, volatility/volume regimes, z-score,
RSI and run-up/drawdown. Strategies in stratgrid.py are threshold rules over
these columns.
"""

import numpy as np
import pandas as pd

MOM_WINDOWS = (1, 2, 3, 5, 10, 15, 30)
FLOW_WINDOWS = (3, 5, 15)


def _rsi(price, window=14):
    delta = price.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / window, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def add_signals(bars):
    p = bars["price"]
    for k in MOM_WINDOWS:
        bars[f"mom{k}"] = p.pct_change(k)
    for k in FLOW_WINDOWS:
        vol = bars["volume"].rolling(k).sum().replace(0, np.nan)
        bars[f"flow{k}"] = bars["signed"].rolling(k).sum() / vol
    r1 = p.pct_change()
    bars["vol"] = r1.rolling(15).std()
    v_mean = bars["volume"].rolling(30).mean()
    v_std = bars["volume"].rolling(30).std().replace(0, np.nan)
    bars["volz"] = (bars["volume"] - v_mean) / v_std
    ma = p.rolling(20).mean()
    sd = p.rolling(20).std().replace(0, np.nan)
    bars["z"] = (p - ma) / sd
    bars["rsi"] = _rsi(p, 14)
    bars["dd"] = p / p.rolling(30).max() - 1.0
    bars["ru"] = p / p.rolling(30).min() - 1.0
    bars["ready"] = np.isfinite(bars["vol"]) & np.isfinite(bars["z"])
    return bars
