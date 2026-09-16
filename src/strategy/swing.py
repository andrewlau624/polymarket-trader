import numpy as np


def swing_position(df, params, config, initial_dir=0.0):
    """Volatility-targeted Donchian breakout with an ATR trailing stop.

    Long when price breaks the N-bar high and the fast EMA is above the slow
    EMA; short on the mirror condition (if enabled). Position size scales with
    ``target_vol / realized_vol`` and is capped at ``max_leverage``.
    """
    s = config["strategy"]
    n = len(df)
    pos = np.zeros(n, dtype=float)

    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    vol = df["realized_vol"].to_numpy(dtype=float)
    ema_f = df["ema_fast"].to_numpy(dtype=float)
    ema_s = df["ema_slow"].to_numpy(dtype=float)
    dch = df[f"dch_{int(params['entry_window'])}"].to_numpy(dtype=float)
    dcl = df[f"dcl_{int(params['entry_window'])}"].to_numpy(dtype=float)

    allow_short = bool(params.get("allow_short", s.get("allow_short", True)))
    use_flow = bool(s.get("use_flow", False))
    flow = df["flow_sign"].to_numpy(dtype=float) if use_flow and "flow_sign" in df else None
    max_lev = float(s["max_leverage"])
    target_vol = float(params.get("target_vol", s["target_vol"]))
    mult = float(params["exit_atr_mult"])
    exit_on_cross = bool(params.get("exit_on_cross", s.get("exit_on_cross", True)))
    cooldown = int(params.get("cooldown", s.get("cooldown", 0)))
    require_breakout = bool(params.get("require_breakout", s.get("require_breakout", True)))
    trend_min = float(params.get("trend_min", s.get("trend_min", 0.0)))
    slope_bars = int(params.get("slope_bars", s.get("slope_bars", 0)))

    direction = float(initial_dir)
    stop = 0.0
    bars_since_exit = 10**9

    for i in range(n):
        px = close[i]
        if not np.isfinite(atr[i]) or not np.isfinite(vol[i]) or vol[i] <= 0:
            pos[i] = 0.0
            continue

        if direction == 0.0:
            trend_up = ema_f[i] > ema_s[i]
            trend_dn = ema_f[i] < ema_s[i]
            long_ok = not use_flow or flow[i] >= 0
            short_ok = not use_flow or flow[i] <= 0
            can_enter = bars_since_exit >= cooldown
            sep = (ema_f[i] - ema_s[i]) / ema_s[i] if ema_s[i] else 0.0
            strength_ok = np.isfinite(sep) and abs(sep) >= trend_min
            if slope_bars > 0 and i >= slope_bars:
                slope_up = ema_s[i] > ema_s[i - slope_bars]
                slope_dn = ema_s[i] < ema_s[i - slope_bars]
            else:
                slope_up = slope_dn = True
            long_sig = (trend_up and strength_ok and slope_up
                        and (not require_breakout or (np.isfinite(dch[i]) and px > dch[i])))
            short_sig = (trend_dn and strength_ok and slope_dn
                         and (not require_breakout or (np.isfinite(dcl[i]) and px < dcl[i])))
            if can_enter and long_sig and long_ok:
                direction = 1.0
                stop = px - mult * atr[i]
            elif can_enter and allow_short and short_sig and short_ok:
                direction = -1.0
                stop = px + mult * atr[i]
        else:
            exited = False
            if direction > 0:
                stop = max(stop, px - mult * atr[i])
                exited = px < stop or (exit_on_cross and ema_f[i] < ema_s[i])
            else:
                stop = min(stop, px + mult * atr[i])
                exited = px > stop or (exit_on_cross and ema_f[i] > ema_s[i])
            if exited:
                direction = 0.0
                bars_since_exit = 0

        bars_since_exit += 1

        if direction != 0.0:
            pos[i] = direction * min(target_vol / vol[i], max_lev)
        else:
            pos[i] = 0.0

    return pos, direction
