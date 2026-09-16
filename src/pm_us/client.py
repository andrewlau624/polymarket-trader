"""Polymarket US adapter (CFTC-regulated platform, api.polymarket.us).

Separate system from the global exchange: Ed25519 API keys, KYC-gated,
human-readable market slugs, one order book per market, and a first-class
liquidity-incentive API (programs, discount factor, target size, earnings).

Credentials come from the environment:
    POLYMARKET_US_KEY_ID      (from polymarket.us/developer)
    POLYMARKET_US_SECRET_KEY
"""

import os

from polymarket_us import PolymarketUS

INTENT_BUY = "ORDER_INTENT_BUY_LONG"
INTENT_SELL = "ORDER_INTENT_SELL_LONG"
TYPE_LIMIT = "ORDER_TYPE_LIMIT"
TIF_GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"


def amount(p):
    return {"value": f"{float(p):.4f}", "currency": "USD"}


def unwrap(resp):
    """Newer US endpoints nest payloads under 'marketData'."""
    if isinstance(resp, dict):
        md = resp.get("marketData")
        if isinstance(md, dict):
            merged = {**resp, **md}
            return merged
    return resp


def px(a, default=None):
    """Unwrap an Amount (or raw number/string) to float."""
    if a is None:
        return default
    if isinstance(a, dict):
        a = a.get("value")
    try:
        return float(a)
    except (TypeError, ValueError):
        return default


class UsClient:
    def __init__(self, key_id=None, secret_key=None):
        self.key_id = key_id or os.environ.get("POLYMARKET_US_KEY_ID")
        self.secret_key = secret_key or os.environ.get("POLYMARKET_US_SECRET_KEY")
        if not self.key_id or not self.secret_key:
            raise ValueError("set POLYMARKET_US_KEY_ID and POLYMARKET_US_SECRET_KEY")
        self.c = PolymarketUS(key_id=self.key_id, secret_key=self.secret_key)

    # --- market data -----------------------------------------------------
    def markets(self, **params):
        return self.c.markets.list(params or None).get("markets", [])

    def book(self, slug):
        return unwrap(self.c.markets.book(slug))

    def bbo(self, slug):
        return unwrap(self.c.markets.bbo(slug))

    def reference(self, slug):
        """Best available (bid, ask) — from the book, else from the BBO feed."""
        try:
            bids, asks, state = self.book_levels(slug)
        except Exception:
            bids, asks, state = [], [], None
        if bids and asks:
            return bids[0][0], asks[0][0], bids, asks
        try:
            b = self.bbo(slug)
        except Exception:
            b = {}
        bb = px(b.get("bestBid")) or px(b.get("currentPx"))
        ba = px(b.get("bestAsk"))
        return bb, ba, bids, asks

    def book_levels(self, slug):
        """Normalized (bids, asks) as best-first (price, qty) lists."""
        b = self.book(slug)
        bids = [(px(l.get("px")), float(l.get("qty") or 0)) for l in (b.get("bids") or [])]
        asks = [(px(l.get("px")), float(l.get("qty") or 0)) for l in (b.get("offers") or [])]
        bids = [x for x in bids if x[0] is not None and x[1] > 0]
        asks = [x for x in asks if x[0] is not None and x[1] > 0]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return bids, asks, b.get("state")

    # --- incentives (not wrapped by the SDK) -----------------------------
    def incentives(self, **params):
        return self.c.get("/v1/incentives", query=params or None, authenticated=True)

    def earnings(self, **params):
        return self.c.get("/v1/incentives/earnings", query=params or None, authenticated=True)

    # --- account ---------------------------------------------------------
    def balances(self):
        return self.c.account.balances()

    def positions(self):
        return self.c.portfolio.positions().get("positions", {})

    def open_orders(self):
        return self.c.orders.list().get("orders", [])

    # --- orders ----------------------------------------------------------
    def place(self, slug, side, price, quantity, maker=True):
        """side: 'buy' | 'sell'. maker=True sets participateDontInitiate (post-only)."""
        params = {
            "marketSlug": slug,
            "intent": INTENT_BUY if side == "buy" else INTENT_SELL,
            "type": TYPE_LIMIT,
            "price": amount(price),
            "quantity": int(quantity),
            "tif": TIF_GTC,
        }
        if maker:
            params["participateDontInitiate"] = True
        return self.c.orders.create(params)

    def cancel(self, order_id, slug):
        return self.c.orders.cancel(order_id, {"marketSlug": slug})

    def cancel_all(self):
        try:
            self.c.orders.cancel_all()
        except Exception:
            pass

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass
