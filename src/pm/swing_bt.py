"""Swing strategies A/B/C backtested on Polymarket trade tape.

Each tradeable is a binary contract priced 0..1 that pays $1 if its outcome
wins. We reconstruct a per-minute series from the tape and test:

  A  early momentum   - enter when a short lookback return AND signed-flow
                        imbalance agree, hold to resolution
  B  momentum ride    - enter once the move is under way, exit on a retrace
                        (else resolution)
  C  jump / news      - enter on a single-bar jump, hold to resolution

Reports are grouped per category (LoL vs others) because the user trades LoL.
"""

import numpy as np


def _entry_index(bars, cond, warmup=5):
    for i in range(warmup, len(bars)):
        if cond(bars, i):
            return i
    return None


def _cond_a(params):
    w = params.get("lookback", 5)

    def cond(bars, i):
        r = bars[f"ret{w}"].iloc[i]
        f = bars[f"flow{w}"].iloc[i]
        return (r is not None and np.isfinite(r) and r > params["thr"]
                and f is not None and np.isfinite(f) and f > params["flow"])
    return cond


def _cond_b(params):
    w = params.get("lookback", 5)

    def cond(bars, i):
        r = bars[f"ret{w}"].iloc[i]
        return r is not None and np.isfinite(r) and r > params["thr"]
    return cond


def _cond_c(params):
    def cond(bars, i):
        r = bars["ret1"].iloc[i]
        return r is not None and np.isfinite(r) and r > params["jump"]
    return cond


def _cond_d(params):
    """Contrarian value: buy once the token has fallen below max_px."""
    def cond(bars, i):
        p = bars["price"].iloc[i]
        return p is not None and np.isfinite(p) and p < params["max_px"]
    return cond


def run_token(bars, payoff, strategy, params, cost):
    """One trade per token: first signal, then manage to exit/resolution."""
    conds = {"A": _cond_a, "B": _cond_b, "C": _cond_c, "D": _cond_d}
    entry_i = _entry_index(bars, conds[strategy](params))
    if entry_i is None:
        return None
    entry = float(bars["price"].iloc[entry_i])
    if not (0.03 < entry < 0.97):
        return None

    if strategy in ("A", "C", "D"):
        proceeds, exit_kind = payoff, "resolution"
    else:
        retrace = params.get("retrace", 0.20)
        peak = entry
        proceeds, exit_kind = payoff, "resolution"
        for j in range(entry_i + 1, len(bars)):
            p = float(bars["price"].iloc[j])
            peak = max(peak, p)
            if p <= peak * (1 - retrace):
                proceeds, exit_kind = p, "market"
                break

    pnl = proceeds - entry - cost
    return {
        "entry": entry,
        "proceeds": proceeds,
        "pnl": pnl,
        "roi": pnl / entry,
        "win": 1.0 if proceeds >= 1.0 else 0.0,
        "exit": exit_kind,
    }


def calibration_obs(markets, frac=0.5):
    """(category, entry_price, outcome) sampled mid-market, for calibration.

    Efficient markets put win rate == price. A bucket where win rate differs
    from price is the only real directional edge.
    """
    obs = []
    for m in markets:
        for tok in m["tokens"]:
            bars = m["bars"].get(tok)
            if bars is None or len(bars) < 30:
                continue
            p = float(bars["price"].iloc[int(len(bars) * frac)])
            if not (0.0 < p < 1.0):
                continue
            obs.append((m["category"], p, 1.0 if tok == m["win_token"] else 0.0))
    return obs


def calibration_table(obs, edges=(0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)):
    import numpy as _np

    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        bucket = [o for o in obs if lo <= o[1] < hi]
        if not bucket:
            continue
        prices = _np.array([o[1] for o in bucket])
        wins = _np.array([o[2] for o in bucket])
        rows.append(
            {
                "bucket": f"{lo:.2f}-{hi:.2f}",
                "n": len(bucket),
                "avg_price": float(prices.mean()),
                "win_rate": float(wins.mean()),
                "edge": float(wins.mean() - prices.mean()),
            }
        )
    return rows


def backtest_market(market, strategy, params, cost):
    """Test both outcome tokens; return list of trades."""
    trades = []
    for tok in market["tokens"]:
        bars = market["bars"].get(tok)
        if bars is None:
            continue
        payoff = 1.0 if tok == market["win_token"] else 0.0
        t = run_token(bars, payoff, strategy, params, cost)
        if t:
            t.update(
                {
                    "category": market["category"],
                    "question": market["question"],
                    "outcome": market["outcomes"][market["tokens"].index(tok)]
                    if tok in market["tokens"] else "",
                }
            )
            trades.append(t)
    return trades


def summarize(trades):
    if not trades:
        return None
    roi = np.array([t["roi"] for t in trades])
    pnl = np.array([t["pnl"] for t in trades])
    win = np.array([t["win"] for t in trades])
    tstat = roi.mean() / roi.std(ddof=1) * np.sqrt(len(roi)) if roi.std(ddof=1) > 0 else 0.0
    committed = np.array([t["entry"] for t in trades])
    avg_entry = float(committed.mean())
    # capital-weighted return: total PnL / total capital staked (skew-robust)
    cap_roi = float(pnl.sum() / committed.sum()) if committed.sum() > 0 else 0.0
    return {
        "n": len(trades),
        "hit": float(win.mean()),
        "mean_roi": float(roi.mean()),
        "median_roi": float(np.median(roi)),
        "cap_roi": cap_roi,
        "mean_pnl": float(pnl.mean()),
        "sum_pnl": float(pnl.sum()),
        "tstat": float(tstat),
        "avg_entry": avg_entry,
        # calibration edge: do we win more often than the price implies?
        "edge": float(win.mean() - avg_entry),
        "pct_market_exit": float(np.mean([t["exit"] == "market" for t in trades])),
    }
