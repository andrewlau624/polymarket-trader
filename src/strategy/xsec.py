"""Cross-sectional momentum across a crypto universe.

Rank every coin by its trailing return, go long the winners and short the
losers, risk-weight each leg, then scale the whole book to a target
volatility. This is a different return source from single-asset trend: it is
(roughly) market-neutral, so it does not depend on crypto going up.
"""

import numpy as np
import pandas as pd

from src.data.timeframe import periods_per_year


def _target_weights(close, params):
    """Raw long/short weights (gross 1 per leg) before leverage."""
    ret = close.pct_change()
    vol_window = int(params["vol_window"])
    vol = ret.rolling(vol_window).std() * np.sqrt(periods_per_year("1d"))
    lookback = int(params["lookback"])
    skip = int(params["skip"])
    sig = close.shift(skip) / close.shift(skip + lookback) - 1.0
    frac = float(params["frac"])
    long_only = bool(params.get("long_only", False))

    w = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    cols = np.array(close.columns)
    sig_v = sig.to_numpy()
    vol_v = vol.to_numpy()
    for t in range(len(close)):
        s = sig_v[t]
        v = vol_v[t]
        valid = np.isfinite(s) & np.isfinite(v) & (v > 0)
        n = int(valid.sum())
        if n < 4:
            continue
        k = max(1, int(round(n * frac)))
        if k * 2 > n:
            k = max(1, n // 3)
        idx = np.where(valid)[0]
        order = idx[np.argsort(s[idx])[::-1]]
        longs = order[:k]
        shorts = order[-k:] if not long_only else np.array([], dtype=int)

        inv = 1.0 / v
        lw = inv[longs]
        lw = lw / lw.sum()
        row = np.zeros(len(cols))
        row[longs] = lw
        if len(shorts) > 0:
            sw = inv[shorts]
            sw = sw / sw.sum()
            row[shorts] = -sw
        w.iloc[t] = row
    return w


def xsec_backtest(close, params, cost=0.001):
    """Return (net_returns, gross_leverage, target_weights) series/frame."""
    ret = close.pct_change()
    w = _target_weights(close, params)

    rebalance = int(params.get("rebalance", 1))
    if rebalance > 1:
        keep = pd.Series(False, index=w.index)
        keep.iloc[::rebalance] = True
        w = w.where(keep, other=np.nan).ffill().fillna(0.0)

    target_vol = float(params["target_vol"])
    max_lev = float(params.get("max_lev", 3.0))
    vol_window = int(params["vol_window"])

    # raw strategy return to estimate trailing vol for leverage scaling
    raw = (w.shift(1) * ret).sum(axis=1)
    trailing = raw.rolling(vol_window).std() * np.sqrt(periods_per_year("1d"))
    lev = (target_vol / trailing).clip(upper=max_lev).shift(1).fillna(0.0)

    w_lev = w.mul(lev, axis=0)
    gross = (w_lev.shift(1) * ret).sum(axis=1)
    turnover = (w_lev - w_lev.shift(1)).abs().sum(axis=1)
    net = gross - turnover * cost
    return net.fillna(0.0), w_lev.abs().sum(axis=1), w_lev


def equal_weight_reference(close):
    ret = close.pct_change()
    w = close.notna().astype(float)
    w = w.div(w.sum(axis=1), axis=0)
    return (w.shift(1) * ret).sum(axis=1).fillna(0.0)
