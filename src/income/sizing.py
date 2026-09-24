"""How many shares. Arbs are capacity-limited; bets are Kelly-limited.

The old allocator ranked bets and arbs on one EV-per-share axis and gave bets
the same base size as risk-free pairs. Kelly was computed and never used.

Capital: the venue does NOT net legs. A long ties up its price; a short ties
up (1 - price) as collateral. So a pair ties up ~$1 per share.
"""


def capital_long(px):
    return max(float(px), 0.0)


def capital_short(px):
    return max(1.0 - float(px), 0.0)


def pair_capital(sell_px, buy_px):
    return max(capital_long(buy_px) + capital_short(sell_px), 0.01)


def kelly_binary(p_win, cost):
    """Kelly fraction of bankroll staked on a $1 binary bought for `cost`."""
    if cost <= 0 or cost >= 1 or p_win <= cost:
        return 0.0
    return (p_win - cost) / (1.0 - cost)


def value_shares(sig, bankroll, kelly_frac=0.25, max_order_usd=5.0, depth=None):
    """Shares for a value take. For a sell, the bet is the NO side: win prob
    1-fair, cost (1-px) + fee - i.e. the collateral actually posted."""
    from src.pm_us.fees import taker_fee
    px, f = sig["px"], sig["fair"]
    fee = taker_fee(px)
    if sig["side"] == "buy":
        p, cost = f, px + fee
    else:
        p, cost = 1.0 - f, (1.0 - px) + fee
    k = kelly_binary(p, cost) * kelly_frac
    stake = min(k * max(bankroll, 0.0), max_order_usd)
    shares = int(stake / max(cost, 0.01))
    if depth is not None:
        shares = min(shares, int(depth))
    return max(shares, 0)


def arb_shares(sig, free_capital, max_shares=25):
    """Pair shares: bounded by the hedge's visible depth, capital, and a cap."""
    if sig["kind"] == "rest_hedge":
        rest_sells = sig["rest_side"] == "sell"
        sell_px = sig["rest_px"] if rest_sells else sig["hedge_px"]
        buy_px = sig["hedge_px"] if rest_sells else sig["rest_px"]
    else:
        sell_px, buy_px = sig["sell_px"], sig["buy_px"]
    by_cap = int(max(free_capital, 0.0) / pair_capital(sell_px, buy_px))
    return max(min(int(sig.get("size", 0)), by_cap, max_shares), 0)
