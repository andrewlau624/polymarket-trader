"""Risk on a spread ladder is ONE number, not N positions.

Every strike on a ladder is a function of the same underlying quantity - the
margin of victory. So a book of twenty positions is not twenty risks; it is one
exposure to that margin, and it can be measured and hedged as such.

For a strike at signed line L, the contract pays iff margin > -L, so under a
Normal(mu, sigma) margin its price and sensitivities are

    P      = 1 - Phi((-L - mu) / sigma)
    delta  = dP/dmu    = phi(z) / sigma          z = (-L - mu)/sigma
    gamma  = d2P/dmu2  = z * phi(z) / sigma^2
    vega   = dP/dsigma = z * phi(z) / sigma

WHY THIS UNLOCKS SCALE. Picking off individual violations caps at whatever
depth sits at the touch. Quoting the WHOLE ladder does not - but only if the
resulting inventory is controllable. Net delta says exactly how exposed the
book is to the game's outcome, in units of "shares of margin", and a long in
one strike genuinely hedges a short in another. That is what lets a $17 account
run a hundred quotes instead of two pairs.

Delta is also the natural hedge ratio: to neutralise d shares of exposure at
one strike, trade d * (delta_here / delta_there) at another.
"""

import math

SQRT2PI = math.sqrt(2.0 * math.pi)


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x):
    return math.exp(-0.5 * x * x) / SQRT2PI


def price(line, mu, sigma):
    return 1.0 - _phi((-line - mu) / max(sigma, 1e-6))


def greeks(line, mu, sigma):
    """(price, delta, gamma, vega) for one strike."""
    s = max(sigma, 1e-6)
    z = (-line - mu) / s
    pdf = _pdf(z)
    return {
        "price": 1.0 - _phi(z),
        "delta": pdf / s,                 # per point of expected margin
        "gamma": z * pdf / (s * s),
        "vega": z * pdf / s,              # per point of margin SD
    }


def book_risk(positions, mu, sigma):
    """Aggregate a book. positions: {line: signed shares}.

    Net delta is the number that matters: it is the book's P&L per point the
    expected margin moves. A pair that is long one strike and short another
    nets close to zero by construction, which is why the arbitrage is safe -
    and the same arithmetic tells you when a half-filled pair is not.
    """
    tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "value": 0.0,
           "gross": 0.0, "net_shares": 0.0}
    per = {}
    for line, shares in positions.items():
        g = greeks(line, mu, sigma)
        per[line] = {k: v * shares for k, v in g.items()}
        per[line]["shares"] = shares
        tot["delta"] += g["delta"] * shares
        tot["gamma"] += g["gamma"] * shares
        tot["vega"] += g["vega"] * shares
        tot["value"] += g["price"] * shares
        tot["gross"] += abs(shares)
        tot["net_shares"] += shares
    return tot, per


def hedge_ratio(line_from, line_to, mu, sigma):
    """Shares at `line_to` that neutralise one share at `line_from`."""
    a = greeks(line_from, mu, sigma)["delta"]
    b = greeks(line_to, mu, sigma)["delta"]
    if abs(b) < 1e-9:
        return 0.0
    return -a / b


def best_hedge(line, shares, quotes, mu, sigma, exclude=()):
    """Cheapest strike to offset `shares` at `line`, by spread cost per delta.

    A naked leg does not have to be unwound on its own strike. Any strike on
    the ladder hedges it, and the one with the tightest spread per unit of
    delta is usually not the one you are stuck in.
    """
    target = greeks(line, mu, sigma)["delta"] * shares
    best = None
    for L, q in quotes.items():
        if L == line or L in exclude:
            continue
        bid, ask = q.get("bid"), q.get("ask")
        if bid is None or ask is None:
            continue
        d = greeks(L, mu, sigma)["delta"]
        if abs(d) < 1e-6:
            continue
        need = -target / d
        cost = abs(need) * (ask - bid) / 2.0
        if best is None or cost < best["cost"]:
            best = {"line": L, "shares": need, "cost": cost,
                    "spread": ask - bid, "delta": d}
    return best
