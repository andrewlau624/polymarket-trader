"""Strategy grid over the signal library, for per-match markets.

Each strategy = an entry condition (a signal threshold) + an exit rule. One
trade per token per market (first signal), so trades stay independent.
Signals are kept as numpy arrays for speed across a large grid.

Families:
  mom_follow / mom_fade      momentum up (ride) / down (fade the dump)
  flow_follow / flow_fade    aggressive-flow follow / fade
  zrev                       buy when price is stretched below its mean
  volbrk                     momentum gated by a volume spike
  capit                      fade a fast dump accompanied by heavy selling
  favorite / longshot        buy expensive / cheap contracts, hold out

capit was the only family with a consistent positive edge on match markets.
"""

import numpy as np

SIGNAL_COLS = (
    "price", "volume", "signed",
    "mom1", "mom2", "mom3", "mom5", "mom10", "mom15", "mom30",
    "flow3", "flow5", "flow15", "vol", "volz", "z", "rsi", "dd", "ru",
)


def to_arrays(bars):
    out = {}
    for c in SIGNAL_COLS:
        if c in bars.columns:
            out[c] = bars[c].to_numpy(dtype=float)
    return out


def _fin(a, i):
    v = a[i]
    return v is not None and np.isfinite(v)


def make_cond(family, k=3, thr=0.05, vz=1.0):
    if family in ("mom_follow", "mom_fade"):
        key = f"mom{k}"
        sign = 1 if family == "mom_follow" else -1
        return lambda S, i: _fin(S[key], i) and sign * S[key][i] > thr
    if family in ("flow_follow", "flow_fade"):
        key = f"flow{k}"
        sign = 1 if family == "flow_follow" else -1
        return lambda S, i: _fin(S[key], i) and sign * S[key][i] > thr
    if family == "capit":
        # fade a fast dump that comes with one-sided aggressive selling
        key, fkey = f"mom{k}", f"flow{k}"

        def cond(S, i):
            m = S[key][i]
            fl = S[fkey][i]
            return np.isfinite(m) and np.isfinite(fl) and m < -thr and fl < -vz
        return cond
    if family == "zrev":
        return lambda S, i: _fin(S["z"], i) and S["z"][i] < -thr
    if family == "volbrk":
        key = f"mom{k}"
        return lambda S, i: _fin(S[key], i) and _fin(S["volz"], i) and S[key][i] > thr and S["volz"][i] > vz
    if family == "favorite":
        return lambda S, i: _fin(S["price"], i) and S["price"][i] > thr
    if family == "longshot":
        return lambda S, i: _fin(S["price"], i) and S["price"][i] < thr
    raise ValueError(family)


def run_trade(S, payoff, cond, exit_mode, cost, warmup=5, horizon=15,
              retrace=0.3, target=0.3):
    price = S["price"]
    n = len(price)
    entry_i = None
    for i in range(warmup, n):
        if cond(S, i):
            entry_i = i
            break
    if entry_i is None:
        return None
    entry = float(price[entry_i])
    if not (0.02 < entry < 0.98):
        return None

    proceeds = payoff
    if exit_mode == "horizon":
        proceeds = float(price[min(entry_i + horizon, n - 1)])
    elif exit_mode == "retrace":
        peak = entry
        for j in range(entry_i + 1, n):
            p = float(price[j])
            peak = max(peak, p)
            if p <= peak * (1 - retrace):
                proceeds = p
                break
    elif exit_mode == "target":
        for j in range(entry_i + 1, n):
            p = float(price[j])
            if p >= entry * (1 + target):
                proceeds = p
                break
            if p <= entry * (1 - target):
                proceeds = p
                break

    pnl = proceeds - entry - cost
    return {"entry": entry, "proceeds": proceeds, "pnl": pnl,
            "roi": pnl / entry, "win": 1.0 if proceeds >= 1.0 else 0.0}


def evaluate_market(market, cond, exit_mode, cost, **kw):
    trades = []
    for tok in market["tokens"]:
        S = market["S"].get(tok)
        if S is None:
            continue
        payoff = 1.0 if tok == market["win_token"] else 0.0
        t = run_trade(S, payoff, cond, exit_mode, cost, **kw)
        if t:
            trades.append(t)
    return trades


def summarize(trades):
    if not trades:
        return None
    roi = np.array([t["roi"] for t in trades])
    pnl = np.array([t["pnl"] for t in trades])
    win = np.array([t["win"] for t in trades])
    committed = np.array([t["entry"] for t in trades])
    std = roi.std(ddof=1)
    return {
        "n": len(trades),
        "hit": float(win.mean()),
        "cap_roi": float(pnl.sum() / committed.sum()) if committed.sum() > 0 else 0.0,
        "mean_roi": float(roi.mean()),
        "median_roi": float(np.median(roi)),
        "t": float(roi.mean() / std * np.sqrt(len(roi))) if std > 0 else 0.0,
        "avg_entry": float(committed.mean()),
    }


def grid():
    """Yield (label, cond, exit_mode, kwargs) configurations."""
    exits = [
        ("res", "resolution", {}),
        ("h10", "horizon", {"horizon": 10}),
        ("h30", "horizon", {"horizon": 30}),
        ("r30", "retrace", {"retrace": 0.30}),
        ("r50", "retrace", {"retrace": 0.50}),
        ("tp30", "target", {"target": 0.30}),
    ]
    for fam in ("mom_follow", "mom_fade"):
        for k in (1, 3, 5, 15):
            for thr in (0.03, 0.05, 0.10, 0.20):
                for en, mode, kw in exits:
                    yield f"{fam}_k{k}_t{thr}_{en}", make_cond(fam, k, thr), mode, kw
    for fam in ("flow_follow", "flow_fade"):
        for k in (3, 5, 15):
            for thr in (0.2, 0.5):
                for en, mode, kw in exits:
                    yield f"{fam}_k{k}_t{thr}_{en}", make_cond(fam, k, thr), mode, kw
    for thr in (1.0, 2.0):
        for en, mode, kw in exits:
            yield f"zrev_t{thr}_{en}", make_cond("zrev", 0, thr), mode, kw
    for k in (3, 5):
        for thr in (0.05, 0.10):
            for vz in (1.0, 2.0):
                for en, mode, kw in exits:
                    yield f"volbrk_k{k}_t{thr}_v{vz}_{en}", make_cond("volbrk", k, thr, vz), mode, kw
    for k in (5, 15):
        for thr in (0.10, 0.20):
            for fthr in (0.2, 0.4, 0.6):
                for en, mode, kw in exits:
                    yield (f"capit_k{k}_t{thr}_f{fthr}_{en}",
                           make_cond("capit", k, thr, fthr), mode, kw)
    for pthr in (0.7, 0.85):
        for en, mode, kw in exits:
            yield f"favorite_p{pthr}_{en}", make_cond("favorite", 0, pthr), mode, kw
    for pthr in (0.15, 0.30):
        for en, mode, kw in exits:
            yield f"longshot_p{pthr}_{en}", make_cond("longshot", 0, pthr), mode, kw
