"""The margin distribution every strike on a ladder is priced from.

A contract at signed line L pays iff margin > -L (venue rules text, see
HANDOFF.md). So one distribution over the integer margin prices the whole
ladder, and every probability on it must come from the SAME distribution -
that is what makes pairs consistent and risk computable.

The old fair_from_ladder() fitted a normal to the ladder's own mids and then
bet against the residuals, which just finds where a normal curve is wrong.
Here (mu, sigma) come from an external sportsbook line (fairvalue.py); the
ladder is what gets judged, never what does the judging.

Key numbers: football margins pile up on 3, 7, 10, 14. The multipliers are
the measured local spike factors (RESEARCH.md S15). Unlike the old code, the
PMF is RENORMALISED after they are applied, so probabilities sum to 1.
"""

import math

from src.edge.bookline import KEY_R, implied_margin

LO, HI = -90, 90

# Empirical margin SD around the closing spread. The moneyline-implied sigma
# (mu / Phi^-1(p)) is 0/0 near a pick'em: Clemson @ Cal at -1.5 came out at
# the 30.0 clamp, which flattens the whole ladder and manufactures "edge" on
# every strike. So the prior dominates when |mu| is small and the implied
# value only informs it when there is signal to read.
LEAGUE_SIGMA = {"cfb": 15.5, "nfl": 13.5}


def _cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class MarginModel:
    """Discrete distribution of the reference team's margin."""

    def __init__(self, mu, sigma, league="cfb", ties=False, key_numbers=True):
        self.mu, self.sigma, self.league = float(mu), max(float(sigma), 1.0), league
        R = KEY_R.get(league, {}) if key_numbers else {}
        raw = {}
        for m in range(LO, HI + 1):
            if m == 0 and not ties:
                continue                  # CFB/NFL: overtime, no ties
            base = (_cdf((m + 0.5 - self.mu) / self.sigma)
                    - _cdf((m - 0.5 - self.mu) / self.sigma))
            raw[m] = base * R.get(abs(m), 1.0)
        tot = sum(raw.values()) or 1.0
        self.pmf = {m: p / tot for m, p in raw.items()}

    @classmethod
    def from_line(cls, espn_spread, p_ref, ref_is_home, league="cfb"):
        prior = LEAGUE_SIGMA.get(league, 15.0)
        mu, implied = implied_margin(espn_spread, p_ref, ref_is_home,
                                     fallback_sigma=prior)
        return cls(mu, shrink_sigma(mu, implied, prior), league)

    def p_cover(self, line):
        """P(contract at `line` pays) = P(margin > -line)."""
        cut = -float(line)
        return sum(p for m, p in self.pmf.items() if m > cut)

    def p_between(self, l1, l2):
        """P(long l2 / short l1 vertical pays) = P(-l2 < margin <= -l1)."""
        lo, hi = -float(l2), -float(l1)
        return sum(p for m, p in self.pmf.items() if lo < m <= hi)

    def ladder(self, lines):
        return {L: self.p_cover(L) for L in lines}

    def as_dict(self):
        return {"mu": round(self.mu, 3), "sigma": round(self.sigma, 3),
                "league": self.league}


def shrink_sigma(mu, implied, prior):
    """Weight on the implied sigma grows with |mu|: 0 at a pick'em, 1/2 by 14."""
    w = min(abs(mu) / 28.0, 0.5)
    return min(max((1 - w) * prior + w * implied, 10.0), 22.0)


def covers(line, margin):
    """Settlement: does the contract at `line` pay for a realised margin?"""
    return margin > -float(line)
