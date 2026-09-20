"""No-arbitrage test over a series' joint outcome space, by linear program.

A best-of-N series has a finite outcome space: every sequence of map winners
that ends the series. Bo3 gives six - AA, ABA, ABB, BAA, BAB, BB. Every market
on that series is an INDICATOR over those outcomes:

    map 2 winner = A   ->  {AA, BAA, BAB}
    match winner = A   ->  {AA, ABA, BAA}
    total > 2.5 maps   ->  {ABA, ABB, BAA, BAB}

So the whole event is one linear system, and asking "are these prices
coherent" is asking whether any probability vector reproduces them. When none
does, an arbitrage exists - and the LP hands back the portfolio rather than
leaving it to be guessed.

Formulated to be tradeable rather than theoretical:

    maximise  t
    s.t.  for every outcome w:
            sum_i u_i (payoff_iw - ask_i) + sum_i v_i (bid_i - payoff_iw)  >=  t
          0 <= u_i <= depth_i        (units bought, at the ask)
          0 <= v_i <= depth_i        (units sold, at the bid)

t is the profit guaranteed in the WORST outcome. t > 0 is an arbitrage, priced
at executable quotes and capped at real depth, so the number it reports is
money rather than a mid-price illusion. This is the discipline the football
ladder work arrived at the hard way: mids invent arbitrage, bid/ask does not.

Pairwise identities like "over 2.5 + map-1 winner takes map 2 = 1" fall out of
this as special cases; the LP finds them and anything else, including
three- and four-leg combinations no one thought to look for.
"""

import itertools

import numpy as np


def outcomes(best_of=3, known=""):
    """Every map-winner sequence that legally ends a Bo-N series.

    `known` collapses the space to what is still possible: once map 1 has been
    won by A, only sequences starting 'A' remain. That collapse is what turns
    the pre-series BOUND into an exact identity, and it is the state the market
    is in for most of a live series.
    """
    need = best_of // 2 + 1
    out = []
    for length in range(need, best_of + 1):
        for seq in itertools.product("AB", repeat=length):
            a, b = seq.count("A"), seq.count("B")
            if max(a, b) != need:
                continue
            if seq[-1] != ("A" if a > b else "B"):
                continue          # the series must end on the deciding map
            if any(max(seq[:i].count("A"), seq[:i].count("B")) >= need
                   for i in range(1, length)):
                continue          # ...and not have ended earlier
            s = "".join(seq)
            if known and not s.startswith(known):
                continue
            out.append(s)
    return out


def payoff(kind, arg, seq, best_of=3):
    """What one unit of a market pays in outcome `seq`. 1.0 or 0.0."""
    a = seq.count("A")
    b = seq.count("B")
    if kind == "map":                       # arg = map number, pays if A wins it
        i = arg - 1
        return 1.0 if i < len(seq) and seq[i] == "A" else 0.0
    if kind == "match":
        return 1.0 if a > b else 0.0
    if kind == "over":                      # arg = maps threshold, e.g. 2.5
        return 1.0 if len(seq) > arg else 0.0
    if kind == "under":
        return 1.0 if len(seq) < arg else 0.0
    raise ValueError(kind)


def build(markets, best_of=3, known=""):
    """(payoff matrix, outcome list). markets: [(kind, arg), ...]."""
    seqs = outcomes(best_of, known)
    M = np.array([[payoff(k, arg, s, best_of) for s in seqs] for k, arg in markets])
    return M, seqs


def find_arbitrage(markets, quotes, best_of=3, tol=1e-4, unit_cap=None,
                   known=""):
    """Maximise the worst-case profit. Returns None when the book is coherent.

    quotes: [(bid, bid_size, ask, ask_size), ...] aligned with `markets`.
    A missing side is passed as None and simply cannot be traded.
    """
    from scipy.optimize import linprog

    M, seqs = build(markets, best_of, known)
    n = len(markets)
    caps_buy, caps_sell = [], []
    for bid, bsz, ask, asz in quotes:
        caps_buy.append(0.0 if ask is None else float(asz or 0))
        caps_sell.append(0.0 if bid is None else float(bsz or 0))
    if unit_cap:
        caps_buy = [min(c, unit_cap) for c in caps_buy]
        caps_sell = [min(c, unit_cap) for c in caps_sell]
    if not any(caps_buy) and not any(caps_sell):
        return None

    # one row per outcome: worst-case profit must be at least t
    A, bvec = [], []
    for j, _s in enumerate(seqs):
        row = np.zeros(2 * n + 1)
        for i, (bid, _bsz, ask, _asz) in enumerate(quotes):
            pay = M[i, j]
            row[i] = -(pay - (ask if ask is not None else 0.0))
            row[n + i] = -((bid if bid is not None else 0.0) - pay)
        row[-1] = 1.0
        A.append(row)
        bvec.append(0.0)

    bounds = ([(0, c) for c in caps_buy] + [(0, c) for c in caps_sell]
              + [(None, None)])
    c = np.zeros(2 * n + 1)
    c[-1] = -1.0
    res = linprog(c, A_ub=np.array(A), b_ub=np.array(bvec), bounds=bounds,
                  method="highs")
    if not res.success:
        return None
    t = float(-res.fun)
    if t <= tol:
        return None
    u, v = res.x[:n], res.x[n:2 * n]
    legs = []
    for i, (kind, arg) in enumerate(markets):
        if u[i] > 1e-6:
            legs.append(("buy", kind, arg, round(float(u[i]), 2), quotes[i][2]))
        if v[i] > 1e-6:
            legs.append(("sell", kind, arg, round(float(v[i]), 2), quotes[i][0]))
    return {"profit": t, "legs": legs, "outcomes": seqs}
