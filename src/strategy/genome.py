"""Composable strategy genomes.

A genome is a plain dict of genes. ``genome_position`` evaluates any genome
into a position series, so a search loop can breed/cross/mutate genomes and
score them without new code per idea.
"""

import hashlib
import json

import numpy as np

ENTRY_TYPES = ("donchian", "roc", "cross", "rsi")
EXIT_TYPES = ("atr", "bars", "opposite")
TREND_SPANS = (8, 16, 24, 48, 96, 192)
DONCHIAN_WINDOWS = (12, 24, 48, 96, 168)
ROC_WINDOWS = (6, 12, 24, 48, 96)
SLOPE_BARS = (0, 12, 24, 72, 168)


def genome_id(genome):
    blob = json.dumps(genome, sort_keys=True).encode()
    return hashlib.md5(blob).hexdigest()[:10]


def genome_position(df, genome, config, initial_dir=0.0):
    g = genome
    close = df["close"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    vol = df["realized_vol"].to_numpy(dtype=float)
    ema_f = df[f"ema_{g['trend_fast']}"].to_numpy(dtype=float)
    ema_s = df[f"ema_{g['trend_slow']}"].to_numpy(dtype=float)

    entry = g["entry"]
    w = int(g["entry_window"])
    if entry == "donchian":
        sig_up = df[f"dch_{w}"].to_numpy(dtype=float)
        sig_dn = df[f"dcl_{w}"].to_numpy(dtype=float)
    elif entry == "roc":
        sig_up = df[f"roc_{w}"].to_numpy(dtype=float)
        sig_dn = sig_up
    else:
        sig_up = sig_dn = None
    rsi = df["rsi"].to_numpy(dtype=float) if entry == "rsi" else None

    allow_short = bool(g.get("allow_short", True))
    target_vol = float(g["target_vol"])
    max_lev = float(g["max_leverage"])
    cooldown = int(g.get("cooldown", 0))
    trend_min = float(g.get("trend_min", 0.0))
    slope_bars = int(g.get("slope_bars", 0))
    vol_cap = g.get("vol_cap")
    vol_cap = float(vol_cap) if vol_cap else None
    roc_thr = float(g.get("roc_thr", 0.0))
    rsi_low = float(g.get("rsi_low", 30.0))
    rsi_high = float(g.get("rsi_high", 70.0))
    exit_kind = g["exit"]
    atr_mult = float(g.get("atr_mult", 3.0))
    exit_bars = int(g.get("exit_bars", 48))
    use_trend = entry in ("donchian", "roc", "cross")
    use_flow = bool(g.get("use_flow", False))
    flow = df["flow_sign"].to_numpy(dtype=float) if use_flow and "flow_sign" in df else None

    n = len(df)
    pos = np.zeros(n, dtype=float)
    direction = float(initial_dir)
    stop = 0.0
    bars_in = 0
    bars_since_exit = 10**9
    prev_up = None

    for i in range(n):
        px = close[i]
        if not np.isfinite(atr[i]) or not np.isfinite(vol[i]) or vol[i] <= 0:
            pos[i] = 0.0
            continue

        trend_up = ema_f[i] > ema_s[i]
        trend_dn = ema_f[i] < ema_s[i]
        sep = (ema_f[i] - ema_s[i]) / ema_s[i] if ema_s[i] else 0.0
        strength_ok = np.isfinite(sep) and abs(sep) >= trend_min
        if slope_bars > 0 and i >= slope_bars:
            slope_up = ema_s[i] > ema_s[i - slope_bars]
            slope_dn = ema_s[i] < ema_s[i - slope_bars]
        else:
            slope_up = slope_dn = True
        regime_ok = strength_ok and (vol_cap is None or vol[i] <= vol_cap)

        # entry trigger
        trigger = 0
        if entry == "donchian":
            if np.isfinite(sig_up[i]) and px > sig_up[i]:
                trigger = 1
            elif np.isfinite(sig_dn[i]) and px < sig_dn[i]:
                trigger = -1
        elif entry == "roc":
            if sig_up[i] > roc_thr:
                trigger = 1
            elif sig_up[i] < -roc_thr:
                trigger = -1
        elif entry == "cross":
            if prev_up is not None:
                if trend_up and not prev_up:
                    trigger = 1
                elif trend_dn and prev_up:
                    trigger = -1
        elif entry == "rsi":
            if np.isfinite(rsi[i]):
                if rsi[i] < rsi_low:
                    trigger = 1
                elif rsi[i] > rsi_high:
                    trigger = -1
        prev_up = trend_up

        long_ok = (not use_flow) or flow[i] >= 0
        short_ok = (not use_flow) or flow[i] <= 0
        if use_trend:
            long_sig = trigger > 0 and trend_up and slope_up and regime_ok and long_ok
            short_sig = trigger < 0 and trend_dn and slope_dn and regime_ok and short_ok
        else:
            long_sig = trigger > 0 and regime_ok and long_ok
            short_sig = trigger < 0 and regime_ok and short_ok

        if direction == 0.0:
            if bars_since_exit >= cooldown:
                if long_sig:
                    direction, stop, bars_in = 1.0, px - atr_mult * atr[i], 0
                elif allow_short and short_sig:
                    direction, stop, bars_in = -1.0, px + atr_mult * atr[i], 0
        else:
            bars_in += 1
            exited = False
            if exit_kind == "atr":
                if direction > 0:
                    stop = max(stop, px - atr_mult * atr[i])
                    exited = px < stop
                else:
                    stop = min(stop, px + atr_mult * atr[i])
                    exited = px > stop
            elif exit_kind == "bars":
                exited = bars_in >= exit_bars
            elif exit_kind == "opposite":
                if direction > 0:
                    stop = max(stop, px - atr_mult * atr[i])
                    exited = (px < stop) or trend_dn
                else:
                    stop = min(stop, px + atr_mult * atr[i])
                    exited = (px > stop) or trend_up
            if exited:
                direction = 0.0
                bars_since_exit = 0
                bars_in = 0

        bars_since_exit += 1
        if direction != 0.0:
            pos[i] = direction * min(target_vol / vol[i], max_lev)
        else:
            pos[i] = 0.0

    return pos, direction
