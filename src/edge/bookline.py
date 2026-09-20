"""Price a whole spread ladder from ONE external bookmaker line.

Built from what the public attempts actually show:

  polymm (real numbers, public wallet): arb leg +$8,293, directional residual
  -$3,184, dead of "got too slow to defend its edge". It RESTED limit orders
  priced off bookmaker odds; when the true price moved, informed flow hit the
  stale quote before it could be pulled. That residual IS adverse selection.

  nebulousx: prices correct in 50-100ms and taker orders are delayed 250ms, so
  no speed-based strategy survives. And "the only ones on the leaderboards are
  market making pair arbitrage bots".

  royceee: anchor on the REASON for a mispricing, not on price or time.

THREE CHANGES that address those directly.

1. TAKE, NEVER REST. polymm's whole loss column is resting quotes getting
   picked off. Crossing the spread costs a known amount; being picked off
   costs an unknown one. Polymarket US quotes 0.005 wide on these ladders, so
   crossing is cheap enough to simply buy the immunity.

2. PRICE THE WHOLE LADDER, NOT ONE MARKET. A bookmaker publishes a spread AND
   a moneyline. The spread gives the MEAN of the margin distribution; the
   de-vigged moneyline gives P(margin > 0), which pins the SD:

        mu = spread,   sigma = mu / Phi^-1(p_favourite)

   Two observables, two parameters, exactly determined - and that distribution
   prices all thirty strikes at once instead of one market. (For LSU @ MISS
   this returns sigma = 16.5 against an empirical CFB SD of 14.6.)

   It also breaks a circularity: run_keyvertical fits a normal to the ladder's
   OWN prices, so a wrong ladder yields a wrong fit. An external line cannot
   be contaminated by the book it is judging.

3. TRADE PAIRS, NOT LEVELS. Buy the most underpriced strike and sell the most
   overpriced one ON THE SAME GAME. If the bookmaker's level is wrong, both
   legs move together and it cancels - only the SHAPE has to be right. That is
   a far weaker requirement than being right about the game, and it removes
   the directional exposure that cost polymm 38% of its gross.
"""

import math

# local key-number lumpiness, measured against NEIGHBOURING margins
# (RESEARCH.md S15). A smooth curve underprices 3 and 7 badly.
KEY_R = {"cfb": {1: 0.54, 2: 1.00, 3: 3.24, 4: 1.00, 5: 0.90, 6: 0.77, 7: 3.47,
                 8: 1.00, 9: 0.80, 10: 2.39, 11: 0.85, 13: 0.90, 14: 2.82,
                 17: 1.60, 21: 1.40},
         "nfl": {1: 0.48, 3: 3.20, 6: 1.24, 7: 2.07, 10: 2.07, 14: 2.48,
                 17: 1.50, 4: 1.00, 8: 1.00}}


def ml_prob(american):
    """Implied probability from an American moneyline, vig included."""
    if american is None:
        return None
    a = float(american)
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig(ml_a, ml_b):
    """Two moneylines -> two probabilities summing to 1. Proportional method."""
    pa, pb = ml_prob(ml_a), ml_prob(ml_b)
    if pa is None or pb is None or pa + pb <= 0:
        return None, None
    s = pa + pb
    return pa / s, pb / s


def _ndf_inv(p):
    """Inverse standard normal CDF (Acklam), accurate to ~1e-9."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p = min(max(p, 1e-9), 1 - 1e-9)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return ((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        return -((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                 / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
    q = p - 0.5
    r = q * q
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def implied_margin(spread, p_favourite, fallback_sigma=14.6):
    """(mu, sigma) of the favourite's margin, from spread and de-vigged ML.

    mu is the spread. sigma follows from P(margin > 0) = Phi(mu/sigma). When
    the moneyline is missing or the spread is ~0 the SD is unidentifiable, so
    it falls back to the empirical value rather than inventing one.
    """
    mu = abs(float(spread))
    if p_favourite is None or abs(mu) < 0.5:
        return mu, fallback_sigma
    z = _ndf_inv(min(max(p_favourite, 0.51), 0.999))
    if z <= 1e-6:
        return mu, fallback_sigma
    sigma = mu / z
    return mu, min(max(sigma, 6.0), 30.0)


def fair_strike(line, mu, sigma, league="cfb", lumpy=True):
    """Fair price of the contract paying iff margin > -line.

    The smooth tail is Phi; the key-number correction moves probability mass
    onto 3 and 7 and off their neighbours, which is where a smooth curve is
    most wrong and where the ladder's cheapest verticals sit.
    """
    base = 1.0 - _phi((-line - mu) / sigma)
    if not lumpy:
        return min(max(base, 0.001), 0.999)
    # redistribute using the local spike factors around the half-point line
    R = KEY_R.get(league, {})
    lo, hi = -line - 0.5, -line + 0.5
    adj = 0.0
    for k, r in R.items():
        for m in (k, -k):
            if m <= -line:
                continue
            pm = _phi((m + 0.5 - mu) / sigma) - _phi((m - 0.5 - mu) / sigma)
            adj += pm * (r - 1.0)
    return min(max(base + adj * 0.15, 0.001), 0.999)


def fair_ladder(lines, spread, p_fav, league="cfb"):
    """{line: fair price} for every strike, from one external line."""
    mu, sigma = implied_margin(spread, p_fav)
    return {L: fair_strike(L, mu, sigma, league) for L in lines}, mu, sigma
