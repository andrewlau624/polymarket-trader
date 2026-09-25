"""A deterministic in-memory venue with the UsClient surface the bot uses."""

import itertools


class FakeVenue:
    def __init__(self):
        self.books = {}              # slug -> (bids, asks) best-first
        self.orders = {}
        self.ids = itertools.count(1)
        self.fail_order_reads = False
        self.placed = []

    def set_book(self, slug, bid=None, ask=None, bid_sz=10, ask_sz=10):
        self.books[slug] = ([(bid, bid_sz)] if bid is not None else [],
                            [(ask, ask_sz)] if ask is not None else [])

    # --- UsClient surface ----------------------------------------------
    def book_levels(self, slug):
        b, a = self.books.get(slug, ([], []))
        return list(b), list(a), "open"

    def place(self, slug, side, price, qty, maker=True, tif=None):
        bids, asks = self.books.get(slug, ([], []))
        if maker:
            if (side == "buy" and asks and price >= asks[0][0]) or \
                    (side == "sell" and bids and price <= bids[0][0]):
                raise RuntimeError("post-only would cross")
        oid = f"o{next(self.ids)}"
        o = {"id": oid, "marketSlug": slug, "side": side, "price": {"value": str(price)},
             "quantity": qty, "cumQuantity": 0, "state": "ORDER_STATE_NEW",
             "maker": maker}
        self.orders[oid] = o
        self.placed.append(o)
        if not maker:                        # IOC against visible depth
            lvl = asks if side == "buy" else bids
            ok = lvl and ((side == "buy" and lvl[0][0] <= price) or
                          (side == "sell" and lvl[0][0] >= price))
            got = min(qty, int(lvl[0][1])) if ok else 0
            o["cumQuantity"] = got
            o["state"] = "ORDER_STATE_FILLED" if got == qty else "ORDER_STATE_CANCELED"
        return {"id": oid}

    def order(self, oid):
        if self.fail_order_reads:
            raise RuntimeError("503")
        return {"order": dict(self.orders[oid])}

    def cancel(self, oid, slug):
        o = self.orders[oid]
        if o["state"] not in ("ORDER_STATE_FILLED",):
            o["state"] = "ORDER_STATE_CANCELED"

    def open_orders(self):
        return [o for o in self.orders.values()
                if o["state"] in ("ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED")]

    def positions(self):
        return {}

    def balances(self):
        return {"balances": [{"buyingPower": "100"}]}

    def all_programs(self):
        return [{"slug": s} for s in self.books]

    def markets(self, **kw):
        return []

    def close(self):
        pass

    # --- test helpers --------------------------------------------------
    def fill(self, oid, n):
        o = self.orders[oid]
        o["cumQuantity"] = min(o["cumQuantity"] + n, o["quantity"])
        o["state"] = ("ORDER_STATE_FILLED" if o["cumQuantity"] == o["quantity"]
                      else "ORDER_STATE_PARTIALLY_FILLED")


class FakeLines:
    def __init__(self, games=None):
        self.games = games or {}

    def game(self, base, need_line=True):
        return self.games.get(base)
