import numpy as np
import pandas as pd

from src.data.timeframe import periods_per_year

EMA_SPANS = (8, 16, 24, 48, 96, 192, 480, 960)
DONCHIAN_WINDOWS = (12, 24, 48, 96, 168)
ROC_WINDOWS = (6, 12, 24, 48, 96)


def _rsi(close, window=14):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def add_genome_features(ohlcv, config, flows=None):
    s = config["strategy"]
    df = ohlcv.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.sort_values("timestamp").reset_index(drop=True)

    close, high, low = df["close"], df["high"], df["low"]
    df["ret"] = close.pct_change().fillna(0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / int(s.get("atr_window", 14)), adjust=False).mean()

    ppy = periods_per_year(config["timeframe"])
    df["realized_vol"] = df["ret"].rolling(int(s["vol_window"])).std() * np.sqrt(ppy)

    for span in EMA_SPANS:
        df[f"ema_{span}"] = close.ewm(span=span, adjust=False).mean()
    for w in DONCHIAN_WINDOWS:
        df[f"dch_{w}"] = high.rolling(w).max().shift(1)
        df[f"dcl_{w}"] = low.rolling(w).min().shift(1)
    for w in ROC_WINDOWS:
        df[f"roc_{w}"] = close.pct_change(w)
    df["rsi"] = _rsi(close, 14)

    if flows is not None:
        fs = flows.copy()
        fs["date"] = pd.to_datetime(fs["date"]).dt.tz_localize(None).dt.normalize()
        fs = fs.drop_duplicates("date").set_index("date")["net_flow"].sort_index().shift(1)
        df["net_flow"] = df["timestamp"].dt.normalize().map(fs).ffill().fillna(0.0)
        df["flow_sign"] = np.sign(df["net_flow"])
    return df
