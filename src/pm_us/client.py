"""Polymarket US adapter (CFTC-regulated platform, api.polymarket.us).

Separate system from the global exchange: Ed25519 API keys, KYC-gated,
human-readable market slugs, one order book per market, and a first-class
liquidity-incentive API (programs, discount factor, target size, earnings).

Credentials come from the environment:
    POLYMARKET_US_KEY_ID      (from polymarket.us/developer)
    POLYMARKET_US_SECRET_KEY
"""

import os
import time

from polymarket_us import PolymarketUS

try:
    from polymarket_us import RateLimitError
except Exception:  # pragma: no cover
    RateLimitError = None

INTENT_BUY = "ORDER_INTENT_BUY_LONG"
INTENT_SELL = "ORDER_INTENT_SELL_LONG"
TYPE_LIMIT = "ORDER_TYPE_LIMIT"
TIF_GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
TIF_IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"


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
        self.c = PolymarketUS(key_id=self.key_id, secret_key=self.secret_key, timeout=15.0)

    def _retry(self, fn, attempts=4):
        """Back off on rate limits (API allows 20 req/s per key)."""
        delay = 0.4
        for i in range(attempts):
            try:
                return fn()
            except Exception as e:
                limited = RateLimitError is not None and isinstance(e, RateLimitError)
                if not limited and "429" not in str(e):
                    raise
                if i == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2

    # --- market data -----------------------------------------------------
    def markets(self, **params):
        return self.c.markets.list(params or None).get("markets", [])

    def book(self, slug):
        return unwrap(self._retry(lambda: self.c.markets.book(slug)))

    def bbo(self, slug):
        return unwrap(self._retry(lambda: self.c.markets.bbo(slug)))

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

    def settlement(self, slug):
        """{'slug', 'settlement'} once resolved; raises NotFound while open."""
        return self._retry(lambda: self.c.markets.settlement(slug))

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
        return self._retry(
            lambda: self.c.get("/v1/incentives", query=params or None, authenticated=True))

    def earnings(self, **params):
        return self._retry(
            lambda: self.c.get("/v1/incentives/earnings", query=params or None, authenticated=True))

    def program_sample(self, program_type="liquidityProgram"):
        """One raw program + timePeriod, for discovering fields we do not read.

        The bot reads rewardPool/discountFactor/targetSize and nothing else. If
        the venue also publishes a minimum size or a max spread to qualify, we
        are blind to it - and every reward so far came back SKIPPED.
        """
        resp = self.incentives(statuses=["active"], program_type=program_type)
        progs = resp.get("programs", []) if isinstance(resp, dict) else []
        if not progs:
            return None, None
        p = progs[0]
        tps = p.get("timePeriods") or []
        return p, (tps[0] if tps else None)

    def all_programs(self, program_type="liquidityProgram", max_pages=12):
        """Every active liquidity program period, paginated."""
        rows, token = [], None
        for _ in range(max_pages):
            q = {"statuses": ["active"], "program_type": program_type}
            if token:
                q["page_token"] = token
            resp = self.incentives(**q)
            if not isinstance(resp, dict):
                break
            for m in resp.get("programs", []):
                ev = m.get("eventStartTime")
                for t in (m.get("timePeriods") or []):
                    if t.get("status") != "active":
                        continue
                    rows.append({
                        "slug": m.get("marketSlug"),
                        "category": m.get("category"),
                        "subcategory": m.get("subcategory"),
                        "event_start": ev,
                        "period": t.get("period"),
                        "pool": float(t.get("rewardPool") or 0),
                        "discount": float(t.get("discountFactor") or 0.4) or 0.4,
                        "target": float(t.get("targetSize") or 0),
                        "start": t.get("start"),
                        "end": t.get("end") or ev,
                    })
            token = resp.get("nextPageToken")
            if not token:
                break
        return rows

    def top_programs(self, n=10, program_type="liquidityProgram"):
        """Active liquidity programs, biggest pool first."""
        rows = []
        resp = self.incentives(statuses=["active"], program_type=program_type)
        for m in (resp.get("programs", []) if isinstance(resp, dict) else []):
            for t in (m.get("timePeriods") or []):
                if t.get("status") != "active":
                    continue
                rows.append({
                    "slug": m.get("marketSlug"),
                    "category": m.get("category"),
                    "pool": float(t.get("rewardPool") or 0),
                    "discount": float(t.get("discountFactor") or 0.4) or 0.4,
                    "target": float(t.get("targetSize") or 0),
                    "period": t.get("period"),
                    "end": t.get("end"),
                })
        rows.sort(key=lambda r: r["pool"], reverse=True)
        return rows[:n]

    # --- account ---------------------------------------------------------
    def balances(self):
        return self.c.account.balances()

    def positions(self):
        return self.c.portfolio.positions().get("positions", {})

    def open_orders(self):
        return self.c.orders.list().get("orders", [])

    # --- orders ----------------------------------------------------------
    def place(self, slug, side, price, quantity, maker=True, tif=None):
        """side: 'buy' | 'sell'. maker=True sets participateDontInitiate (post-only).

        tif='ioc' makes a taker order that fills what it can at `price` or
        better and cancels the rest - a hard slippage cap, unlike a GTC limit
        that would rest if the book moved away between read and send.
        """
        params = {
            "marketSlug": slug,
            "intent": INTENT_BUY if side == "buy" else INTENT_SELL,
            "type": TYPE_LIMIT,
            "price": amount(price),
            "quantity": int(quantity),
            "tif": TIF_IOC if tif == "ioc" else TIF_GTC,
        }
        if maker:
            params["participateDontInitiate"] = True
        return self.c.orders.create(params)

    def order(self, order_id):
        """One order by id: state, cumQuantity, leavesQuantity, avgPx."""
        return self._retry(lambda: self.c.orders.retrieve(order_id))

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
