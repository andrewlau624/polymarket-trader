import numpy as np
import pandas as pd

from src.data.timeframe import periods_per_year

DONCHIAN_WINDOWS = (12, 24, 48, 72, 96, 120, 168, 240)


def _atr(high, low, close, window):
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def add_swing_features(ohlcv, config, flows=None):
    s = config["strategy"]
    df = ohlcv.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.sort_values("timestamp").reset_index(drop=True)

    close, high, low = df["close"], df["high"], df["low"]
    df["ret"] = close.pct_change().fillna(0.0)

    df["atr"] = _atr(high, low, close, int(s.get("atr_window", 14)))

    vol_window = int(s["vol_window"])
    ppy = periods_per_year(config["timeframe"])
    df["realized_vol"] = df["ret"].rolling(vol_window).std() * np.sqrt(ppy)

    df["ema_fast"] = close.ewm(span=int(s["trend_fast"]), adjust=False).mean()
    df["ema_slow"] = close.ewm(span=int(s["trend_slow"]), adjust=False).mean()

    # Donchian channels, shifted by one bar so a breakout uses only closed bars.
    for w in DONCHIAN_WINDOWS:
        df[f"dch_{w}"] = high.rolling(w).max().shift(1)
        df[f"dcl_{w}"] = low.rolling(w).min().shift(1)

    if flows is not None:
        fs = flows.copy()
        fs["date"] = pd.to_datetime(fs["date"]).dt.tz_localize(None).dt.normalize()
        fs = fs.drop_duplicates("date").set_index("date")["net_flow"].sort_index()
        # only past flow observations are usable at bar time
        fs = fs.shift(1)
        day = df["timestamp"].dt.normalize()
        df["net_flow"] = day.map(fs).ffill().fillna(0.0)
        df["flow_sign"] = np.sign(df["net_flow"])
    return df
