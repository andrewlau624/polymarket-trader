"""Polymarket liquidity-rewards discovery and scoring.

Every market on /sampling-markets carries a rewards config:
    rates: [{asset_address, rewards_daily_rate}]   # USDC/day pool
    min_size:  minimum resting order size (shares)
    max_spread: max distance from the midpoint, in cents, to qualify

A resting order earns a share of the daily pool proportional to its score:
    score = size * (max_spread - distance_from_mid) / max_spread
orders must sit within max_spread and be >= min_size. Quoting both sides gets
a bonus. The bot races to hold the best-scoring quotes.

The score model here is a documented approximation (Polymarket does not
publish the exact scoring function); it is used for ranking and share
estimation, not as an accounting truth.
"""

import requests

CLOB_BASE = "https://clob.polymarket.com"


def sampling_markets(max_markets=2000, timeout=20):
    out = []
    cursor = ""
    while len(out) < max_markets:
        r = requests.get(
            f"{CLOB_BASE}/sampling-markets", params={"next_cursor": cursor}, timeout=timeout
        )
        r.raise_for_status()
        j = r.json()
        data = j.get("data", [])
        out.extend(data)
        cursor = j.get("next_cursor", "")
        if not data or not cursor or cursor == "LTE=":
            break
    return out


def _tokens(m):
    toks = m.get("tokens") or []
    out = []
    for t in toks:
        out.append(
            {
                "token_id": t.get("token_id"),
                "outcome": t.get("outcome"),
                "price": float(t["price"]) if t.get("price") is not None else None,
            }
        )
    return [t for t in out if t["token_id"]]


def reward_markets(min_daily_rate=1.0, limit=100, only_active=True):
    """Markets paying liquidity rewards, richest pool first."""
    rows = []
    for m in sampling_markets():
        if only_active and (not m.get("active") or m.get("closed")):
            continue
        rw = m.get("rewards") or {}
        rates = rw.get("rates") or []
        if not rates:
            continue
        daily = sum(float(x.get("rewards_daily_rate") or 0.0) for x in rates)
        if daily < min_daily_rate:
            continue
        tokens = _tokens(m)
        if len(tokens) < 2:
            continue
        rows.append(
            {
                "condition_id": m.get("condition_id"),
                "question": m.get("question"),
                "slug": m.get("market_slug"),
                "daily_rate": daily,
                "min_size": float(rw.get("min_size") or 0.0),
                "max_spread_cents": float(rw.get("max_spread") or 0.0),
                "tick": float(m.get("minimum_tick_size") or 0.001),
                "min_order_size": float(m.get("minimum_order_size") or 0.0),
                "neg_risk": bool(m.get("neg_risk")),
                "end_date": m.get("end_date_iso"),
                "tokens": tokens,
            }
        )
    rows = [r for r in rows if r["min_size"] > 0 and r["max_spread_cents"] > 0]
    rows.sort(key=lambda r: r["daily_rate"], reverse=True)
    return rows[:limit]


def order_score(size, price, mid, max_spread_cents):
    """Proximity-weighted size for one resting order."""
    if size <= 0 or mid is None or price is None:
        return 0.0
    distance = abs(price - mid) * 100.0
    if distance > max_spread_cents:
        return 0.0
    return size * (max_spread_cents - distance) / max_spread_cents


def side_score(levels, mid, max_spread_cents, min_size):
    """Sum of qualifying order scores on one book side."""
    total = 0.0
    for price, size in levels:
        if size < min_size:
            continue
        total += order_score(size, price, mid, max_spread_cents)
    return total


def book_scores(bids, asks, mid, max_spread_cents, min_size):
    return (
        side_score(bids, mid, max_spread_cents, min_size),
        side_score(asks, mid, max_spread_cents, min_size),
    )


def our_score(quote_size, quote_price, mid, max_spread_cents, both_sides=True, bonus=1.5):
    """Score for our own two-sided quote."""
    one = order_score(quote_size, quote_price, mid, max_spread_cents)
    if both_sides:
        return one * (1.0 + bonus)
    return one


def estimate_share(our, competing):
    return our / (our + competing) if (our + competing) > 0 else 0.0
