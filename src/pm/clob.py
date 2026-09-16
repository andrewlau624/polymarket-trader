import requests

CLOB_BASE = "https://clob.polymarket.com"
TIMEOUT = 15


def get_book(token_id):
    r = requests.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_price(token_id, side):
    r = requests.get(
        f"{CLOB_BASE}/price",
        params={"token_id": token_id, "side": side},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("price")


def _levels(raw, side):
    """Return book levels sorted best-first.

    Polymarket returns bids ascending (0.001 -> best) and asks descending
    (0.999 -> best), so the *last* element is the touch. Normalize to a
    descending-bids / ascending-asks layout and sort explicitly so callers
    never depend on the API's ordering.
    """
    rows = []
    for r in raw or []:
        try:
            price = float(r["price"])
            size = float(r.get("size", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        rows.append((price, size))
    # best bid = highest price, best ask = lowest price
    rows.sort(key=lambda x: x[0], reverse=(side == "bid"))
    return rows


def top_of_book(token_id):
    """Best executable prices and touch sizes for one outcome token."""
    book = get_book(token_id)
    bids = _levels(book.get("bids"), "bid")
    asks = _levels(book.get("asks"), "ask")
    return {
        "token_id": token_id,
        "bid": bids[0][0] if bids else None,
        "ask": asks[0][0] if asks else None,
        "bid_size": bids[0][1] if bids else 0.0,
        "ask_size": asks[0][1] if asks else 0.0,
    }


def mid_price(token_id):
    book = get_book(token_id)
    bids = _levels(book.get("bids"), "bid")
    asks = _levels(book.get("asks"), "ask")
    if not bids and not asks:
        return None
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    if best_bid is None:
        return best_ask
    if best_ask is None:
        return best_bid
    return (best_bid + best_ask) / 2.0


def book_depth(token_id, side="buy", levels=5, book=None):
    """Cumulative size available on one side.

    side='buy' consumes asks (what you pay to buy); side='sell' consumes bids.
    """
    book = book if book is not None else get_book(token_id)
    parsed = _levels(book.get("asks") if side == "buy" else book.get("bids"),
                     "ask" if side == "buy" else "bid")
    return sum(size for _, size in parsed[:levels])


def vwap_buy(token_id, shares, book=None):
    """Average execution price to buy `shares` walking the ask ladder.

    Returns (vwap, filled_shares). vwap is None when no liquidity.
    """
    book = book if book is not None else get_book(token_id)
    asks = _levels(book.get("asks"), "ask")
    return _vwap(asks, shares)


def vwap_sell(token_id, shares, book=None):
    """Average execution price to sell `shares` walking the bid ladder."""
    book = book if book is not None else get_book(token_id)
    bids = _levels(book.get("bids"), "bid")
    return _vwap(bids, shares)


def _vwap(rows, shares):
    remaining = float(shares)
    cost = 0.0
    filled = 0.0
    for price, size in rows:
        take = min(size, remaining)
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled <= 0:
        return None, 0.0
    return cost / filled, filled


def get_prices_history(token_id, interval="max", fidelity=1440, start_ts=None, end_ts=None):
    params = {"market": token_id, "interval": interval, "fidelity": fidelity}
    if start_ts:
        params["startTs"] = start_ts
    if end_ts:
        params["endTs"] = end_ts
    r = requests.get(f"{CLOB_BASE}/prices-history", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("history", [])
