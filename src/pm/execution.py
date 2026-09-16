"""Execution layer for the Polymarket market maker.

Two brokers behind one interface:
  - PaperBroker : simulates resting orders against live books. No keys, no
                  funds. Fills when the market trades through your price.
  - ClobBroker  : places real GTC orders via py-clob-client. Requires
                  POLYMARKET_PRIVATE_KEY (and for proxy/funder wallets,
                  POLYMARKET_FUNDER + POLYMARKET_SIGNATURE_TYPE).
"""

import os
import time
import uuid


class PaperBroker:
    def __init__(self):
        self.open = {}       # order_id -> order
        self.positions = {}  # token_id -> signed size
        self.cash = 0.0
        self.fills = []

    def place(self, token_id, side, price, size, **meta):
        oid = uuid.uuid4().hex[:12]
        self.open[oid] = {
            "token_id": token_id, "side": side, "price": float(price),
            "size": float(size), **meta,
        }
        return oid

    def cancel(self, order_id):
        return self.open.pop(order_id, None) is not None

    def cancel_all(self):
        self.open.clear()

    def on_trade(self, token_id, taker_side, price):
        """Fill a resting order when a taker trade crosses it.

        taker_side is 'BUY' (someone lifted an ask) or 'SELL' (someone hit a
        bid). One resting order is consumed per crossing trade.
        """
        for oid, o in list(self.open.items()):
            if o["token_id"] != token_id:
                continue
            if taker_side == "BUY" and o["side"] == "sell" and o["price"] <= price:
                self._fill(oid, o)
                return True
            if taker_side == "SELL" and o["side"] == "buy" and o["price"] >= price:
                self._fill(oid, o)
                return True
        return False

    def on_book(self, token_id, best_bid, best_ask):
        """Fallback: fill when the touch crosses our resting price."""
        for oid, o in list(self.open.items()):
            if o["token_id"] != token_id:
                continue
            if o["side"] == "buy" and best_ask is not None and best_ask <= o["price"]:
                self._fill(oid, o)
            elif o["side"] == "sell" and best_bid is not None and best_bid >= o["price"]:
                self._fill(oid, o)

    def _fill(self, oid, o):
        signed = o["size"] if o["side"] == "buy" else -o["size"]
        self.positions[o["token_id"]] = self.positions.get(o["token_id"], 0.0) + signed
        self.cash -= signed * o["price"]
        self.fills.append({"ts": time.time(), **o})
        self.open.pop(oid, None)

    def snapshot(self):
        return {
            "n_open": len(self.open), "n_fills": len(self.fills),
            "cash": self.cash, "positions": dict(self.positions),
        }


class ClobBroker:
    """Real orders. Imported lazily so the rest of the repo works without it."""

    def __init__(self, host="https://clob.polymarket.com", chain_id=137):
        try:
            from py_clob_client.client import ClobClient
        except ImportError as e:
            raise ImportError(
                "py-clob-client is required for live trading: "
                "pip install py-clob-client"
            ) from e

        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            raise ValueError("set POLYMARKET_PRIVATE_KEY to trade live")
        funder = os.environ.get("POLYMARKET_FUNDER")
        sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1"))

        kwargs = {"key": private_key, "chain_id": chain_id, "host": host}
        if funder:
            kwargs.update({"funder": funder, "signature_type": sig_type})
        self.client = ClobClient(**kwargs)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self.positions = {}

    def place(self, token_id, side, price, size, **meta):
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        args = OrderArgs(
            price=round(float(price), 3),
            size=round(float(size), 2),
            side=BUY if side == "buy" else SELL,
            token_id=str(token_id),
        )
        signed = self.client.create_order(args)
        resp = self.client.post_order(signed, "GTC")
        return (resp or {}).get("orderID") or (resp or {}).get("orderId")

    def cancel(self, order_id):
        try:
            self.client.cancel(order_id=order_id)
            return True
        except Exception:
            return False

    def cancel_all(self):
        try:
            self.client.cancel_all()
        except Exception:
            pass

    def open_orders(self):
        try:
            return self.client.get_orders() or []
        except Exception:
            return []

    def snapshot(self):
        return {"n_open": len(self.open_orders()), "positions": dict(self.positions)}
