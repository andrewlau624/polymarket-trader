"""One structure, two risk profiles: the vertical.

Long the higher line L2, short the lower line L1, pays $1 iff the margin lands
between them and $0 otherwise. That is the SAME position the monotonicity
"arbitrage" takes - the only difference is what it costs to put on:

    net entry < 0   the arbitrage. Paid to hold a {0,+1} payoff, so the worst
                    case is a profit. Size it against capital and depth.
    net entry > 0   a bet. Risk the entry, win $1 at P(margin lands between).
                    Positive EV when P > net entry. Size it by Kelly.

Treating them as one scan finds far more than looking only for free money, and
the fee schedule is what makes the second kind worth having. Resting both legs
earns the maker rebate (0.0125*p*(1-p) each) instead of paying the taker fee
(0.0695), which on real clmsn-cah quotes turns a 0.010 entry into a 0.0042
CREDIT - a position that pays you to hold a 5.7% shot at $1.

    EV taking  +0.0123/share      EV resting +0.0612/share

The risk that distinguishes it from the arb: if only one leg fills you hold a
naked directional bet. That is what sizing and the stale-unwind exist for.
"""

from src.pm_us.fees import maker_rebate, taker_fee


def spans_margin(l1, l2, ties_possible=False):
    """Integer margins that make the vertical pay, for lines l1 < l2.

    A contract at line L pays iff margin > -L, so long-L2 / short-L1 pays iff
    -l2 < margin <= -l1.
    """
    lo, hi = -l2, -l1
    out = [m for m in range(int(lo) - 1, int(hi) + 2) if lo < m <= hi]
    if not ties_possible:
        out = [m for m in out if m != 0]
    return out


def rest_prices(q1, q2, tick=0.001):
    """(sell price on L1, buy price on L2) resting inside both books.

    Never crosses: a post-only order that crosses is rejected, or silently
    becomes a taker and pays 0.0695 instead of earning 0.0125.
    """
    if q1.get("ask") is None or q2.get("bid") is None:
        return None, None
    sell = max(round(q1["ask"] - tick, 3), (q1.get("bid") or 0.0) + tick)
    buy = min(round(q2["bid"] + tick, 3), (q2.get("ask") or 1.0) - tick)
    return sell, buy


def net_entry(sell_px, buy_px, maker=True):
    """Cost per share to put the vertical on. Negative means you are paid."""
    raw = buy_px - sell_px
    if maker:
        return raw - (maker_rebate(sell_px) + maker_rebate(buy_px))
    return raw + (taker_fee(sell_px) + taker_fee(buy_px))


def capital_per_share(sell_px, buy_px):
    """Long leg funded, short leg collateralised to $1. The venue does not net."""
    return max(buy_px + (1.0 - sell_px), 0.01)


def evaluate(l1, l2, q1, q2, fair_prob, tick=0.001, maker=True):
    """Everything needed to decide on one vertical, or None if unquotable."""
    sell_px, buy_px = rest_prices(q1, q2, tick) if maker else (
        q1.get("bid"), q2.get("ask"))
    if sell_px is None or buy_px is None:
        return None
    entry = net_entry(sell_px, buy_px, maker)
    margins = spans_margin(l1, l2)
    ev = (fair_prob or 0.0) - entry
    size_cap = min(q1.get("bid_sz", 0) or 0, q2.get("ask_sz", 0) or 0) if not maker \
        else min(q1.get("ask_sz", 0) or 0, q2.get("bid_sz", 0) or 0)
    return {
        "l1": l1, "l2": l2, "sell_px": sell_px, "buy_px": buy_px,
        "entry": entry, "margins": margins, "fair": fair_prob, "ev": ev,
        "capital": capital_per_share(sell_px, buy_px),
        "risk_free": entry < 0, "depth": size_cap,
        # Kelly on a {0,1} payoff bought for `entry`: f = (p - c) / (1 - c)
        "kelly": (max(ev, 0.0) / max(1.0 - entry, 1e-9)) if entry > 0 else 1.0,
    }


def fair_from_ladder(quotes, league="cfb"):
    """P(margin == k) for each strike gap, from the ladder's own fitted shape.

    Circular by nature - a wrong ladder gives a wrong fit - so a bookmaker line
    is preferred when one exists (run_bookline). This is the fallback, and it
    carries the key-number correction because a smooth curve is most wrong
    exactly where the cheap verticals sit.
    """
    from src.edge.bookline import KEY_R, _ndf_inv, _phi
    pts = [(k, (q["bid"] + q["ask"]) / 2.0) for k, q in quotes.items()
           if q.get("bid") is not None and q.get("ask") is not None]
    pts = [(k, p) for k, p in pts if 0.02 < p < 0.98]
    if len(pts) < 4:
        return {}
    best = None
    for mu in [x * 0.5 for x in range(-80, 81)]:
        for sd in [x * 0.5 for x in range(8, 61)]:
            err = sum((1.0 - _phi((-k - mu) / sd) - p) ** 2 for k, p in pts)
            if best is None or err < best[0]:
                best = (err, mu, sd)
    _e, mu, sd = best
    R = KEY_R.get(league, {})
    out = {}
    for m in range(-60, 61):
        base = _phi((m + 0.5 - mu) / sd) - _phi((m - 0.5 - mu) / sd)
        out[m] = max(min(base * R.get(abs(m), 1.0), 1.0), 0.0)
    return out


def scan(quotes, fair_pmf, tick=0.001, maker=True, min_ev=0.005, league="cfb"):
    """Every adjacent-strike vertical, ranked by EV per share.

    One scan finds both kinds: entry < 0 is the risk-free arbitrage, entry > 0
    with EV > 0 is a positive-expectation bet. Looking only for free money
    misses most of what is there.
    """
    ks = sorted(quotes)
    out = []
    for l1, l2 in zip(ks[:-1], ks[1:]):
        margins = spans_margin(l1, l2)
        p = sum(fair_pmf.get(m, 0.0) for m in margins) if fair_pmf else 0.0
        r = evaluate(l1, l2, quotes[l1], quotes[l2], p, tick, maker)
        if r is None or r["depth"] < 1:
            continue
        if r["risk_free"] or r["ev"] >= min_ev:
            out.append(r)
    return sorted(out, key=lambda r: (-r["risk_free"], -r["ev"]))


def walk_depth(bids1, asks2, fee_fn=None, min_credit=0.0):
    """Every profitable level pair, walking BOTH books down, not just the touch.

    The measured capacity curve - $50 to $100 returning half as much per
    dollar - was an artefact of reading bids[0] and asks[0] only. A violation
    that holds one level deeper is real size the bot could not see. On a
    representative book this is the difference between 20 shares and 190.

    bids1: [(price, size), ...] descending, the leg being SOLD.
    asks2: [(price, size), ...] ascending, the leg being BOUGHT.
    Returns [(credit, sell_px, buy_px, size), ...] best first, each already
    net of fees when fee_fn is supplied.
    """
    i = j = 0
    b_left = list(bids1)
    a_left = list(asks2)
    out = []
    while i < len(b_left) and j < len(a_left):
        bp, bs = b_left[i]
        ap, asz = a_left[j]
        if bp is None or ap is None:
            break
        credit = bp - ap
        if fee_fn:
            credit -= fee_fn(bp, ap)
        if credit <= min_credit:
            break                      # books have crossed; deeper is worse
        take = min(bs, asz)
        if take <= 0:
            break
        out.append((credit, bp, ap, take))
        bs -= take
        asz -= take
        b_left[i] = (bp, bs)
        a_left[j] = (ap, asz)
        if bs <= 0:
            i += 1
        if asz <= 0:
            j += 1
    return out


def depth_capacity(bids1, asks2, fee_fn=None):
    """(total shares, total credit dollars) available across all levels."""
    levels = walk_depth(bids1, asks2, fee_fn)
    shares = sum(l[3] for l in levels)
    dollars = sum(l[0] * l[3] for l in levels)
    return shares, dollars
