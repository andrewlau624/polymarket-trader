"""Order sizing under capital and book-depth constraints.

Capital alone does not set your order size — the order book does. You can
have $1M and still only rest a few hundred dollars in a thin market before
your own quote becomes the book (and gets run over). So size is the *minimum*
of:

  - what your per-order capital budget allows
  - a fraction of the visible qualifying depth (don't dominate the book)
and must be at least the market's rewards min_size to qualify at all.
"""


def order_size(price, depth, min_size, order_budget, max_depth_frac=0.25):
    """Return (size_shares, notional, binding_constraint)."""
    price = float(price)
    if price <= 0 or price >= 1:
        return 0.0, 0.0, "bad_price"
    by_budget = max(order_budget, 0.0) / price
    by_depth = max(depth, 0.0) * max_depth_frac
    if by_depth <= 0:
        return 0.0, 0.0, "no_depth"
    size = min(by_budget, by_depth)
    binding = "budget" if by_budget <= by_depth else "depth"
    if size < min_size:
        return 0.0, 0.0, "below_min_size"
    return size, size * price, binding


def per_order_budget(bankroll, n_markets, sides=2, util=0.8):
    if n_markets <= 0:
        return 0.0
    return bankroll * util / (n_markets * sides)


def plan(bankroll, markets, books, util=0.8, max_depth_frac=0.25, size_mult=1.0):
    """Per-market sizing plan given live books.

    books: {token_id: (bids, asks)} with best-first (price, size) levels.
    Returns list of rows with size/notional and which constraint binds.
    """
    n = len(markets)
    budget = per_order_budget(bankroll, n, util=util)
    rows = []
    for m in markets:
        for tok in m["tokens"][:2]:
            bid_ask = books.get(tok["token_id"])
            if not bid_ask or not bid_ask[0] or not bid_ask[1]:
                continue
            bids, asks = bid_ask
            mid = (bids[0][0] + asks[0][0]) / 2.0
            depth = sum(s for _, s in asks[:5])  # qualifying asks within touch ladder
            min_size = max(m["min_size"], m["min_order_size"]) * size_mult
            size, notional, binding = order_size(
                mid, depth, min_size, budget, max_depth_frac
            )
            rows.append(
                {
                    "market": m["question"][:46],
                    "outcome": tok.get("outcome"),
                    "mid": mid,
                    "book_depth": depth,
                    "min_size": min_size,
                    "size": size,
                    "notional": notional,
                    "binding": binding,
                }
            )
    return budget, rows
