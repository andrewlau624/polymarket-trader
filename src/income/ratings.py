"""Season-stats team ratings: points for and against, adjusted for opponents.

margin(home - away) = r_home - r_away + hfa + noise, fitted by ridge least
squares over every completed game so far. That is the opponent-adjusted
version of "points scored and allowed" (an SRS rating): beating a bad team by
20 counts for less than beating a good one by 7. The ridge pulls each team
toward a PRIOR - last season's rating, regressed halfway - so a team that has
played three games is not rated off three games alone.

Win probability = Phi((r_home - r_away + hfa) / sigma). sigma is the spread
of real margins around the rating's prediction, measured on past seasons in
dog_backtest.py rather than assumed.

Everything a rating uses must be dated BEFORE the game it prices; fit()
takes whatever games it is given, so that discipline lives in the caller.
"""

import math

import numpy as np

SIGMA = {"cfb": 16.0, "nfl": 13.5}     # defaults; dog_backtest.py measures them
HFA_PRIOR = {"cfb": 2.5, "nfl": 1.5}
RIDGE = 4.0                            # pseudo-games of weight on the prior
CARRY = 0.5                            # share of last season's rating kept
# A college team with no rating last season almost always played in the FBS
# listing only as some FBS team's FCS opponent: start it well below average.
NEW_TEAM = {"cfb": -12.0, "nfl": 0.0}


def fit(games, prior=None, ridge=RIDGE, league="cfb"):
    """games: [(home, away, home_margin, neutral)]. -> (ratings, hfa)."""
    prior = dict(prior or {})
    teams = sorted({g[0] for g in games} | {g[1] for g in games} | set(prior))
    for t in teams:
        prior.setdefault(t, NEW_TEAM[league])
    if not games:
        return {t: prior.get(t, 0.0) for t in teams}, HFA_PRIOR[league]
    idx = {t: i for i, t in enumerate(teams)}
    n, k = len(games), len(teams) + 1              # last column: home field
    rows = n + len(teams) + 1
    A = np.zeros((rows, k))
    b = np.zeros(rows)
    for r, (h, a, m, neutral) in enumerate(games):
        A[r, idx[h]], A[r, idx[a]] = 1.0, -1.0
        A[r, -1] = 0.0 if neutral else 1.0
        b[r] = m
    w = math.sqrt(ridge)
    for j, t in enumerate(teams):                  # each team pulled to its prior
        A[n + j, j], b[n + j] = w, w * prior.get(t, 0.0)
    A[-1, -1], b[-1] = w, w * HFA_PRIOR[league]    # and home field to its prior
    x = np.linalg.lstsq(A, b, rcond=None)[0]
    return {t: float(x[idx[t]]) for t in teams}, float(x[-1])


def carry(ratings, share=CARRY):
    """Last season's final ratings as this season's prior."""
    return {t: share * r for t, r in ratings.items()}


def win_prob(ratings, hfa, home, away, neutral=False, league="cfb", sigma=None):
    """P(home wins). None if either team has no rating at all."""
    if home not in ratings or away not in ratings:
        return None
    mu = ratings[home] - ratings[away] + (0.0 if neutral else hfa)
    s = sigma or SIGMA[league]
    return 0.5 * (1.0 + math.erf(mu / (s * math.sqrt(2.0))))
