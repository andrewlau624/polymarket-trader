"""State that cannot silently lie.

Three failures in ladder_bot this replaces:

  * json.dump straight into the live file: a crash mid-write left a truncated
    file, load_state() swallowed the error and returned a BLANK state, and the
    bot forgot every resting order it owned. Here writes are atomic (temp file,
    fsync, rename) and a corrupt file is quarantined and reported, never
    replaced by an empty one.
  * fills inferred from venue-aggregate positions: if positions() threw, the
    empty dict made a vanished pair book as FILLED. Here a fill is only ever
    the change in that ORDER's own cumQuantity, read from the venue by id.
  * no record between "decided to place" and "got an order id": a crash there
    orphaned a live order. Here an intent is written before the call and
    resolved after it (write-ahead), and leftovers are surfaced at startup.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

from src.pm_us.fees import maker_rebate, taker_fee
from src.pm_us.jsonlog import append

VERSION = 1


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fresh(start_equity=0.0):
    return {"version": VERSION, "created": now_iso(), "start_equity": start_equity,
            "orders": {}, "intents": {}, "groups": {}, "books": {}, "games": {},
            "kills": {}, "realized": 0.0, "daily": {}, "settled": {}}


class CorruptState(Exception):
    pass


class Store:
    def __init__(self, path, ledger_path):
        self.path, self.ledger = path, ledger_path

    def load(self, start_equity=0.0):
        if not os.path.exists(self.path):
            return fresh(start_equity)
        try:
            with open(self.path) as fh:
                s = json.load(fh)
            if not isinstance(s, dict) or "orders" not in s:
                raise ValueError("not a state document")
        except (ValueError, OSError) as e:
            bad = f"{self.path}.corrupt-{int(time.time())}"
            os.replace(self.path, bad)
            raise CorruptState(
                f"state file unreadable ({type(e).__name__}); quarantined to {bad}. "
                f"Resting orders it tracked are still live on the venue - inspect "
                f"`make money` before running again.") from e
        base = fresh(start_equity)
        base.update(s)
        return base

    def save(self, s):
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        tmp = f"{self.path}.tmp-{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(s, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def log(self, kind, **rec):
        rec = {"kind": kind, "ts": now_iso(), **rec}
        append(self.ledger, rec)
        return rec


# ---- books -----------------------------------------------------------------

def book(s, game):
    return s["books"].setdefault(game, {"pos": {}, "cash": 0.0})


def book_pos(b):
    return {float(k): v for k, v in b["pos"].items() if v}


def worst_others(s, game):
    from src.income.risk import worst_case
    return sum(worst_case(book_pos(b), b["cash"])
               for g, b in s["books"].items() if g != game and g not in s["settled"])


# ---- intents and orders ----------------------------------------------------

def new_intent(s, **fields):
    iid = uuid.uuid4().hex[:12]
    s["intents"][iid] = {"iid": iid, "ts": now_iso(), **fields}
    return iid


def resolve_intent(s, iid, oid):
    it = s["intents"].pop(iid)
    if oid is None:
        return None
    rec = {**it, "oid": str(oid), "filled": 0, "status": "open"}
    s["orders"][str(oid)] = rec
    return rec


def apply_fill(s, store, order, cum_qty, avg_px=None):
    """Book the increase in an order's cumQuantity. Idempotent: calling it
    twice with the same cum_qty books nothing the second time.

    avgPx is the order's running average over ALL its fills, so the price of
    the NEW shares is recovered from cumulative notional, not read off avgPx.
    """
    prev = int(order.get("filled", 0))
    new = int(cum_qty) - prev
    if new <= 0:
        return 0
    prev_notional = order.get("notional", float(order["px"]) * prev)
    if avg_px:
        notional = float(avg_px) * int(cum_qty)
        px = (notional - prev_notional) / new
    else:
        px = float(order["px"])
        notional = prev_notional + px * new
    fee = -maker_rebate(px, new) if order.get("maker") else taker_fee(px, new)
    b = book(s, order["game"])
    key = str(float(order["line"]))
    sgn = 1 if order["side"] == "buy" else -1
    delta_cash = -sgn * px * new - fee
    b["pos"][key] = b["pos"].get(key, 0) + sgn * new
    b["cash"] = b["cash"] + delta_cash
    order["filled"] = int(cum_qty)
    order["notional"] = notional
    order["cash"] = order.get("cash", 0.0) + delta_cash
    store.log("fill", oid=order["oid"], game=order["game"], line=order["line"],
              side=order["side"], px=round(px, 5), qty=new, fee=round(fee, 5),
              maker=bool(order.get("maker")), strat=order.get("strat"),
              group=order.get("group"), fair=order.get("fair"))
    return new


def record_realized(s, amount, when=None):
    day = (when or now_iso())[:10]
    s["realized"] = s.get("realized", 0.0) + amount
    s["daily"][day] = s["daily"].get(day, 0.0) + amount


def realized_today(s):
    return s["daily"].get(now_iso()[:10], 0.0)
