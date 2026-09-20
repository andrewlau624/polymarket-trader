"""The venue's published fee schedule. Effective 2026-09-17, exchange-wide.

    Fee = theta * C * p * (1 - p)

    taker  theta = +0.0695   (charged, debits at trade time)
    maker  theta = -0.0125   (REBATE, credits at fill time)

Source: docs.polymarket.us/fees, worked examples reproduced in the self-test
below. Symmetric around p=0.50, smallest at the extremes.

WHY THIS DOMINATES EVERYTHING ELSE HERE.

A monotonicity pair is two legs. Taking both, at a typical leg price of 0.58:

    2 * 0.0695 * 0.58 * 0.42  =  0.0339 per share

against observed credits of 0.025 to 0.040. Most violations we have found are
NEGATIVE as takes. But resting both legs earns:

    2 * 0.0125 * 0.58 * 0.42  =  +0.0061 per share

The swing between the two execution modes is ~0.040 a share - larger than the
largest violation ever observed on this venue. Execution mode matters more than
the size of the mispricing, and ladder_bot has been taking both legs.

Resting is viable here specifically BECAUSE the edge is slow: violations stood
in 12 of 15 observations across ten sweeps over hours. There is time to wait
for a fill. That is the opposite of polymm's situation, which rested quotes
priced off a model that went stale in seconds and died of adverse selection.
And a resting PAIR is hedged against level moves by construction - both legs
are on the same game - so only relative moves can hurt it.
"""

TAKER_THETA = 0.0695
MAKER_THETA = 0.0125          # magnitude; it is a rebate, so signed +ve to you


def taker_fee(price, contracts=1):
    """Dollars charged. Positive number = money out."""
    p = min(max(float(price), 0.01), 0.99)
    return TAKER_THETA * contracts * p * (1.0 - p)


def maker_rebate(price, contracts=1):
    """Dollars received. Positive number = money in."""
    p = min(max(float(price), 0.01), 0.99)
    return MAKER_THETA * contracts * p * (1.0 - p)


def pair_cost(px_a, px_b, contracts=1, a_maker=False, b_maker=False):
    """Net fee cost of a two-leg pair. Negative means the fees PAY you."""
    a = -maker_rebate(px_a, contracts) if a_maker else taker_fee(px_a, contracts)
    b = -maker_rebate(px_b, contracts) if b_maker else taker_fee(px_b, contracts)
    return a + b


def min_credit(px_a, px_b, a_maker=False, b_maker=False, margin=0.002):
    """Smallest per-share credit that still nets positive after fees."""
    return pair_cost(px_a, px_b, 1, a_maker, b_maker) + margin


def self_test():
    """Reproduce the docs' worked examples exactly."""
    cases = [(0.10, -6.26, 1.12), (0.65, -15.81, 2.84), (0.30, -14.60, 2.62),
             (0.90, -6.26, 1.12), (0.50, -17.38, 3.12)]
    for p, want_t, want_m in cases:
        t, m = -taker_fee(p, 1000), maker_rebate(p, 1000)
        assert abs(t - want_t) < 0.02, (p, t, want_t)
        assert abs(m - want_m) < 0.02, (p, m, want_m)
    return True
