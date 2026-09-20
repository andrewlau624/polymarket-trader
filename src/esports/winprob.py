"""In-play win probability for CS2 / Valorant / LoL.

Two games, two different mathematical objects. Using the wrong one is how
people end up with a model that feels sophisticated and prices badly.

CS2 and VALORANT are a RACE TO N ROUNDS, so the state space is finite and the
answer is exact - a Markov chain on (rounds_a, rounds_b), no approximation:

    W(a,b) = p*W(a+1,b) + (1-p)*W(a,b+1)
    W(N,b) = 1,  W(a,N) = 0,  and at N-1 all square it is a win-by-2 tail:
    W = q^2 / (q^2 + (1-q)^2)   with q the per-round win probability

LEAGUE OF LEGENDS has no round structure - it ends when a nexus falls, and the
carrier of information is GOLD DIFFERENCE, which behaves like a diffusion. So
it is a digital option on that difference:

    P(win) = Phi( (goldDiff + mu*tau) / (sigma*sqrt(tau)) )

with tau the expected time remaining. That is the same pricer written for
crypto binaries, and it gets the important behaviour for free: a 5k lead at
ten minutes is worth far less than the same lead at thirty, because tau
shrinks and the denominator with it.

WHY THIS MATTERS FOR TRADING. A baron steal, an ace, a retake - these are
JUMPS in the state variable. The model reprices instantly and exactly; a
thin order book does not. That gap is the edge the swings create, and it is
only capturable because these models are cheap to evaluate: the CS2 chain is
a few hundred multiplications and the LoL one is a single erf.
"""

import math
from functools import lru_cache

SIGMA_GOLD = 1600.0      # per sqrt(minute), LoL gold-diff diffusion scale
GAME_MINUTES = 32.0      # expected LoL game length


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@lru_cache(maxsize=None)
def race_win_prob(a, b, p, target=13, win_by_2=True):
    """P(A takes the series) from score (a, b), per-round win probability p.

    Exact. Not a simulation and not a normal approximation.
    """
    if a >= target and (not win_by_2 or a - b >= 2 or a == target):
        return 1.0
    if b >= target and (not win_by_2 or b - a >= 2 or b == target):
        return 0.0
    if win_by_2 and a >= target - 1 and b >= target - 1:
        # deuce: first to lead by two, a classic gambler's-ruin tail
        if a == b:
            return (p * p) / (p * p + (1 - p) * (1 - p))
        return 1.0 if a > b else 0.0
    if a >= target:
        return 1.0
    if b >= target:
        return 0.0
    return (p * race_win_prob(a + 1, b, p, target, win_by_2)
            + (1 - p) * race_win_prob(a, b + 1, p, target, win_by_2))


def round_edge(p_base, econ_adv=0.0, man_adv=0, site_adv=0.0):
    """Per-round win probability from a base skill edge plus round state.

    Economy, player count and map-side all shift a single round. A 5v4 is worth
    far more than a rifle advantage; these are the published rough magnitudes,
    and they are parameters to be fitted, not truths.
    """
    x = math.log(max(min(p_base, 0.999), 0.001) / (1 - max(min(p_base, 0.999), 0.001)))
    x += 0.55 * econ_adv          # full-buy vs eco
    x += 0.95 * man_adv           # each extra player alive
    x += site_adv
    return 1.0 / (1.0 + math.exp(-x))


def lol_win_prob(gold_diff, minute, sigma=SIGMA_GOLD, game_len=GAME_MINUTES,
                 drift_per_min=0.0):
    """P(A wins) from gold difference, as a digital option on that difference.

    tau is time remaining; as it shrinks a given lead becomes decisive, which
    is the behaviour any LoL model must reproduce and a static logistic cannot.
    """
    tau = max(game_len - minute, 0.35)
    denom = sigma * math.sqrt(tau)
    return _phi((gold_diff + drift_per_min * tau) / denom)


def lol_objective_bump(towers=0, dragons=0, barons=0, inhibs=0):
    """Objectives expressed as gold-equivalent, so one state variable suffices.

    Rather than bolting terms onto a regression, price each objective as the
    gold it is worth. A baron is the big one - roughly a 3k swing once the buff
    is used - which is exactly the discrete jump that moves a market.
    """
    return 1000.0 * towers + 550.0 * dragons + 3000.0 * barons + 1800.0 * inhibs


def implied_round_p(a, b, market_price, target=13, win_by_2=True,
                    lo=0.02, hi=0.98, iters=40):
    """Invert the race model: what per-round edge does the market imply?

    The most useful diagnostic there is. A market pricing a 3-9 team at 0.25
    implies a per-round probability; if that number is absurd, the market is
    wrong rather than the model.
    """
    for _ in range(iters):
        mid = (lo + hi) / 2
        if race_win_prob(a, b, mid, target, win_by_2) < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
