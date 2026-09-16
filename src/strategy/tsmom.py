"""Diversified time-series momentum (trend) across a crypto universe.

Each coin gets its own trend signal and volatility-scaled position; the book
is the equal-weighted average. Diversifying the same trend rule across many
coins cuts idiosyncratic risk, which is the classic managed-futures result.
"""

import numpy as np
import pandas as pd

from src.data.timeframe import periods_per_year


def tsmom_backtest(close, params, cost=0.001):
    ret = close.pct_change()
    ppy = periods_per_year("1d")

    fast = int(params["trend_fast"])
    slow = int(params["trend_slow"])
    vol_window = int(params["vol_window"])
    cap = float(params.get("max_lev", 3.0))
    long_only = bool(params.get("long_only", False))

    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    trend = np.sign(ema_f - ema_s)
    if long_only:
        trend = trend.clip(lower=0.0)

    vol = ret.rolling(vol_window).std() * np.sqrt(ppy)
    # risk-parity weights (1/vol), normalised to gross 1 across active coins
    inv = (1.0 / vol).where(vol > 0)
    raw = trend * inv
    raw = raw.where(inv.notna())
    gross_w = raw.abs().sum(axis=1).replace(0.0, np.nan)
    w = raw.div(gross_w, axis=0).fillna(0.0)

    rebalance = int(params.get("rebalance", 1))
    if rebalance > 1:
        keep = pd.Series(False, index=w.index)
        keep.iloc[::rebalance] = True
        w = w.where(keep, other=np.nan).ffill().fillna(0.0)

    # scale the whole book to the target vol using its own trailing vol
    raw_ret = (w.shift(1) * ret).sum(axis=1)
    trailing = raw_ret.rolling(vol_window).std() * np.sqrt(ppy)
    lev = (float(params["target_vol"]) / trailing).clip(upper=cap).shift(1).fillna(0.0)
    w = w.mul(lev, axis=0)

    gross = (w.shift(1) * ret).sum(axis=1)
    turnover = (w - w.shift(1)).abs().sum(axis=1)
    net = gross - turnover * cost
    return net.fillna(0.0), w.abs().sum(axis=1), w
