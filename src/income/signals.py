"""What to trade on one ladder, given fresh books and (optionally) a model.

Four signal types, each honest about what makes it pay:

  taker_arbs      bid(L1) > ask(L2) + both taker fees. Both legs crossed at
                  once. The only thing here that is genuinely risk-free.

  rest_hedge      rest ONE leg post-only; the moment it fills, cross the
                  other. Pays when the resting price beats the hedge's ask by
                  more than one taker fee. Exposure is naked only between the
                  fill and the hedge (minutes on a manage cycle), not 12 hours.
                  This replaces "rest both legs", which fired on perfectly
                  consistent books (L1 0.49/0.52, L2 0.52/0.55 was "risk-free")
                  because it only paid if BOTH limit orders filled - and the
                  fill you get is the informed one.

  value_takes     one strike vs the sportsbook-implied fair. A single taker leg
                  costs 0.0695*p*(1-p) - 1.7c at the money - so a deviation of
                  fee + buffer is a bet with a reason behind it.

  mm_quotes       two-sided maker quotes around fair, skewed by inventory.
                  Earns the rebate and is the only thing that can qualify for
                  liquidity rewards. Experimental: off unless asked for.

quotes: {line: {"bid","bid_sz","ask","ask_sz","bids","asks"}} with bids/asks
the full best-first [(px, sz)] levels when available.
"""

from src.pm_us.fees import maker_rebate, taker_fee


def _ok(q, *keys):
    return all(q.get(k) is not None for k in keys)


def taker_arbs(quotes, min_credit=0.003):
    """Monotonicity violations that survive taking both legs, depth-walked."""
    ks = sorted(quotes)
    out = []
    for i, l1 in enumerate(ks):
        q1 = quotes[l1]
        if not _ok(q1, "bid"):
            continue
        for l2 in ks[i + 1:]:
            q2 = quotes[l2]
            if not _ok(q2, "ask"):
                continue
            bids = q1.get("bids") or [(q1["bid"], q1.get("bid_sz", 0))]
            asks = q2.get("asks") or [(q2["ask"], q2.get("ask_sz", 0))]
            levels = walk(bids, asks, min_credit)
            if not levels:
                continue
            size = sum(l[3] for l in levels)
            worst = levels[-1]
            out.append({
                "kind": "taker_arb", "sell": l1, "buy": l2,
                "sell_px": worst[1], "buy_px": worst[2],   # IOC limit prices
                "credit": sum(l[0] * l[3] for l in levels) / size,
                "size": size,
            })
    out.sort(key=lambda r: -r["credit"] * r["size"])
    return _disjoint(out)


def walk(bids, asks, min_credit):
    """[(credit_after_fees, sell_px, buy_px, size)] walking both books."""
    b = [list(x) for x in bids]
    a = [list(x) for x in asks]
    i = j = 0
    out = []
    while i < len(b) and j < len(a):
        bp, bs = b[i]
        ap, asz = a[j]
        credit = bp - ap - taker_fee(bp) - taker_fee(ap)
        if credit <= min_credit:
            break
        take = min(bs, asz)
        if take <= 0:
            break
        out.append((credit, bp, ap, take))
        b[i][1] -= take
        a[j][1] -= take
        if b[i][1] <= 0:
            i += 1
        if a[j][1] <= 0:
            j += 1
    return out


def rest_price(q, side, tick):
    """Best post-only price that rests: one tick inside, never crossing."""
    bid, ask = q.get("bid"), q.get("ask")
    if side == "sell":
        if ask is None:
            return None
        px = round(ask - tick, 4)
        if bid is not None and px <= bid:
            px = ask                       # spread is one tick: join the ask
        return px
    if bid is None:
        return None
    px = round(bid + tick, 4)
    if ask is not None and px >= ask:
        px = bid
    return px


def rest_hedge(quotes, tick=0.001, min_credit=0.005):
    """Rest one leg, hedge the other on fill. Credit is after the rebate on the
    resting leg and the TAKER fee on the hedge - the fee actually paid."""
    ks = sorted(quotes)
    out = []
    for i, l1 in enumerate(ks):
        for l2 in ks[i + 1:]:
            q1, q2 = quotes[l1], quotes[l2]
            # A: rest the sell on L1, buy L2 at its ask when filled
            s = rest_price(q1, "sell", tick)
            if s is not None and _ok(q2, "ask") and q2.get("ask_sz", 0) > 0:
                c = s - q2["ask"] + maker_rebate(s) - taker_fee(q2["ask"])
                if c >= min_credit:
                    out.append({"kind": "rest_hedge", "sell": l1, "buy": l2,
                                "rest_line": l1, "rest_side": "sell",
                                "rest_px": s, "hedge_line": l2,
                                "hedge_side": "buy", "hedge_px": q2["ask"],
                                "credit": c, "size": q2["ask_sz"]})
            # B: rest the buy on L2, sell L1 at its bid when filled
            b = rest_price(q2, "buy", tick)
            if b is not None and _ok(q1, "bid") and q1.get("bid_sz", 0) > 0:
                c = q1["bid"] - b + maker_rebate(b) - taker_fee(q1["bid"])
                if c >= min_credit:
                    out.append({"kind": "rest_hedge", "sell": l1, "buy": l2,
                                "rest_line": l2, "rest_side": "buy",
                                "rest_px": b, "hedge_line": l1,
                                "hedge_side": "sell", "hedge_px": q1["bid"],
                                "credit": c, "size": q1["bid_sz"]})
    out.sort(key=lambda r: -r["credit"])
    return _disjoint(out)


def hedge_credit(rest_side, rest_px, hedge_q):
    """Re-price a resting leg's pair against the hedge book as it is NOW."""
    if rest_side == "sell":
        a = hedge_q.get("ask")
        return None if a is None else rest_px - a + maker_rebate(rest_px) - taker_fee(a)
    b = hedge_q.get("bid")
    return None if b is None else b - rest_px + maker_rebate(rest_px) - taker_fee(b)


def value_takes(quotes, fair, edge_min=0.03, px_band=(0.04, 0.96)):
    """Single-strike takes where the book is wrong by more than fee + buffer.

    edge = fair - ask - fee (buy) or bid - fair - fee (sell). The buffer is
    there because fair is an estimate: ESPN's line can be stale and the
    normal-plus-key-numbers shape is approximate.
    """
    lo, hi = px_band
    out = []
    for L, q in quotes.items():
        f = fair.get(L)
        if f is None:
            continue
        a, b = q.get("ask"), q.get("bid")
        if a is not None and lo <= a <= hi and q.get("ask_sz", 0) > 0:
            e = f - a - taker_fee(a)
            if e >= edge_min:
                out.append({"kind": "value", "line": L, "side": "buy",
                            "px": a, "fair": f, "edge": e,
                            "depth": q.get("ask_sz", 0)})
        if b is not None and lo <= b <= hi and q.get("bid_sz", 0) > 0:
            e = b - f - taker_fee(b)
            if e >= edge_min:
                out.append({"kind": "value", "line": L, "side": "sell",
                            "px": b, "fair": f, "edge": e,
                            "depth": q.get("bid_sz", 0)})
    out.sort(key=lambda r: -r["edge"])
    return out


def mm_quotes(quotes, fair, half_spread=0.02, skew_per_delta=0.0, net_delta=0.0,
              tick=0.001, px_band=(0.05, 0.95)):
    """Two-sided maker quotes around fair. Never crosses; never quotes through
    fair (a bid above fair is a donation). Skew leans against inventory."""
    lo, hi = px_band
    shift = skew_per_delta * net_delta
    out = []
    for L, q in quotes.items():
        f = fair.get(L)
        if f is None or not lo <= f <= hi:
            continue
        bid = round(f - half_spread - shift, 3)
        ask = round(f + half_spread - shift, 3)
        if q.get("ask") is not None:
            bid = min(bid, round(q["ask"] - tick, 3))
        if q.get("bid") is not None:
            ask = max(ask, round(q["bid"] + tick, 3))
        bid, ask = min(bid, f - tick), max(ask, f + tick)
        if lo <= bid < ask <= hi:
            out.append({"kind": "mm", "line": L, "bid": bid, "ask": ask, "fair": f})
    return out


def model_agrees(quotes, fair, max_median_dev=0.12, max_spread=0.10):
    """(ok, median |fair - mid|) over strikes with a usable two-sided book.

    A wrong ESPN match or a flipped ladder sign does not produce a few big
    edges, it produces edges EVERYWHERE. When the model disagrees with the
    whole ladder, the model is what is broken: trade nothing off it.
    """
    devs = sorted(abs(fair[L] - (q["bid"] + q["ask"]) / 2.0)
                  for L, q in quotes.items()
                  if L in fair and _ok(q, "bid", "ask") and q["ask"] - q["bid"] <= max_spread)
    if len(devs) < 3:
        return False, None
    med = devs[len(devs) // 2]
    return med <= max_median_dev, med


def _disjoint(rows):
    """One trade per strike, best first, so capital is not committed twice to
    what is effectively one view."""
    seen, keep = set(), []
    for r in rows:
        if r["sell"] in seen or r["buy"] in seen:
            continue
        seen.update((r["sell"], r["buy"]))
        keep.append(r)
    return keep
