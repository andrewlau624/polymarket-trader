import numpy as np
import pandas as pd


def add_flow_features(ohlcv, flows, config):
    df = ohlcv.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.set_index("timestamp")

    flow_series = flows.set_index("date").sort_index()["net_flow"]
    flow_series = flow_series[~flow_series.index.duplicated(keep="last")]

    df["net_flow"] = flow_series.reindex(df.index).ffill().fillna(0.0)
    df["net_flow"] = df["net_flow"].shift(1)

    df["flow_sign"] = np.sign(df["net_flow"])

    streak = []
    current = 0
    for s in df["flow_sign"]:
        if s == 0:
            current = 0
        elif current == 0:
            current = s
        elif (current > 0) == (s > 0):
            current += s
        else:
            current = s
        streak.append(current)
    df["streak"] = streak

    window = int(config["strategy"]["flow_window"])
    df["flow_window_sum"] = df["net_flow"].rolling(window, min_periods=1).sum()

    acw = int(config["strategy"]["autocorr_window"])
    df["flow_autocorr"] = (
        df["net_flow"].rolling(acw).apply(pd.Series.autocorr, raw=False)
    )

    df = df.reset_index()
    return df