import numpy as np


def flow_persistence_position(df, params, strength_min_usd, initial_long=False):
    n = len(df)
    pos = np.zeros(n, dtype=float)
    long = bool(initial_long)
    for i in range(n):
        streak = df["streak"].iloc[i]
        flow_sum = df["flow_window_sum"].iloc[i]
        autocorr = df["flow_autocorr"].iloc[i]
        if autocorr is None or not np.isfinite(autocorr):
            autocorr = -1.0

        if not long:
            if (
                streak >= params["min_streak"]
                and flow_sum >= strength_min_usd
                and autocorr >= params["autocorr_min"]
            ):
                long = True
        else:
            if (
                streak <= 0
                or autocorr < params["exit_autocorr"]
                or flow_sum < params["exit_flow_usd"]
            ):
                long = False
        pos[i] = 1.0 if long else 0.0
    return pos, long