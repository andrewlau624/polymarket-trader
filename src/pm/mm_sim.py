"""Market-making simulator over the historical trade tape.

Replays a market's trades and rests a two-sided quote at mid +/- d against
them:
  - mid tracks the last trade price
  - a taker BUY at price p fills our ask when ask <= p  (we sell)
  - a taker SELL at price p fills our bid when bid >= p (we buy)
  - inventory is marked to the outcome's resolution at the end

This ignores queue position (an upper bound on fills) but measures the thing
that actually kills makers: adverse selection -- does price drift against us
right after we get filled?
"""

import numpy as np


def simulate_token(trades, token_id, payoff, d, size, max_inv=10_000,
                   warmup=20, drift_lag=10, refresh_sec=300):
    """Replay one token's tape for a resting quote at mid +/- d.

    Quotes are re-placed every ``refresh_sec``; each side can fill at most
    once per refresh window. That models the real cycle (place -> wait ->
    replace) instead of letting a single order fill on every crossing trade.
    """
    if trades is None or len(trades) == 0:
        return None
    sub = trades[trades["asset"].astype(str) == str(token_id)]
    sub = sub.sort_values("timestamp")
    if len(sub) < 40:
        return None
    px = sub["price"].to_numpy(dtype=float)
    sd = sub["side"].to_numpy()
    ts = sub["timestamp"].to_numpy(dtype=float)
    n = len(px)

    inv = 0.0
    cash = 0.0
    fills = 0
    buys = sells = 0
    gross = 0.0
    adverse = []
    quote_ts = -1e18
    bid = ask = None
    bid_live = ask_live = False

    for i in range(n):
        now = ts[i]
        if now - quote_ts >= refresh_sec:
            quote_ts = now
            bid, ask = px[i] - d, px[i] + d
            bid_live = 0.0 < bid < 1.0
            ask_live = 0.0 < ask < 1.0
        if i < warmup or not (bid_live or ask_live):
            continue
        price = px[i]
        fs = None
        if sd[i] == "BUY" and ask_live and ask <= price and inv - size >= -max_inv:
            fs, fp = "sell", ask
        elif sd[i] == "SELL" and bid_live and bid >= price and inv + size <= max_inv:
            fs, fp = "buy", bid
        if fs:
            if fs == "sell":
                inv -= size
                cash += fp * size
                sells += 1
                ask_live = False
            else:
                inv += size
                cash -= fp * size
                buys += 1
                bid_live = False
            fills += 1
            gross += size * d
            if i + drift_lag < n:
                signed = 1.0 if fs == "buy" else -1.0
                adverse.append(signed * (px[i + drift_lag] - fp))

    pnl = cash + inv * payoff
    return {
        "fills": fills,
        "buys": buys,
        "sells": sells,
        "inventory_end": inv,
        "cash": cash,
        "pnl": pnl,
        "gross_spread": gross,
        "adverse_per_fill": float(np.mean(adverse)) if adverse else 0.0,
        "n_trades": n,
    }


def simulate_market(market, d, size, max_inv=10_000):
    """Replay both outcome tokens of a market using its stored tape."""
    trades = market.get("trades")
    out = {}
    for tok in market["tokens"]:
        payoff = 1.0 if tok == market["win_token"] else 0.0
        r = simulate_token(trades, tok, payoff, d, size, max_inv=max_inv)
        if r:
            out[tok] = r
    return out


def summarize(results):
    if not results:
        return None
    pnl = np.array([r["pnl"] for r in results])
    fills = np.array([r["fills"] for r in results])
    adv = np.array([r["adverse_per_fill"] for r in results])
    gross = np.array([r["gross_spread"] for r in results])
    return {
        "n": len(results),
        "fills": float(fills.mean()),
        "pnl_mean": float(pnl.mean()),
        "pnl_total": float(pnl.sum()),
        "gross_mean": float(gross.mean()),
        "adverse_mean": float(adv.mean()),
        "pct_positive": float((pnl > 0).mean()),
    }
