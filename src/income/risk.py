"""Risk on a ladder is exactly computable, so compute it exactly.

Every contract on a game settles on one integer: the margin. A game's book -
signed shares per line plus the cash paid or received - therefore has a P&L
for every margin, and the worst of those is the most the book can lose. No
delta approximation, no normal assumption: a half-filled pair, three value
bets and a hedge are all just rows in the same scenario table.

Kill rules are PRE-REGISTERED here, before any live results exist, in the
manner of swing-trader's signals.py. A strategy that trips one stays off until
a human clears it with --reset-kill; the bot never re-enables itself.
"""

import math
from dataclasses import dataclass

from src.income.model import HI, LO, covers


def scenario_pnl(pos, cash):
    """{margin: settlement P&L} for pos {line: signed shares}."""
    return {m: cash + sum(sh for L, sh in pos.items() if covers(L, m))
            for m in range(LO, HI + 1)}


def worst_case(pos, cash):
    return min(scenario_pnl(pos, cash).values()) if pos or cash else 0.0


def expected_pnl(pos, cash, pmf):
    return cash + sum(sh * sum(p for m, p in pmf.items() if covers(L, m))
                      for L, sh in pos.items())


def with_trade(pos, cash, line, side, px, shares, fee):
    """(pos, cash) after a hypothetical fill. fee: +ve paid, -ve rebate."""
    pos = dict(pos)
    sgn = 1 if side == "buy" else -1
    pos[line] = pos.get(line, 0) + sgn * shares
    cash = cash - sgn * px * shares - fee
    return pos, cash


@dataclass
class Limits:
    max_game_loss: float = 3.0        # worst-case $ on any one game
    max_total_loss: float = 8.0       # sum of per-game worst cases
    daily_loss: float = 3.0           # realised $ lost today halts new risk
    max_drawdown_frac: float = 0.25   # of starting equity, realised: halt all
    max_naked_min: float = 20.0       # unhedged rest leg, then force-hedge


def allowed(pos, cash, others_worst, limits):
    """Can a game end in (pos, cash)? others_worst: sum of other games' worst."""
    w = worst_case(pos, cash)
    if w < -limits.max_game_loss:
        return False, f"game worst case {w:.2f} < -{limits.max_game_loss}"
    if w + others_worst < -limits.max_total_loss:
        return False, f"book worst case {w + others_worst:.2f} < -{limits.max_total_loss}"
    return True, ""


# ---- pre-registered kill rules ---------------------------------------------
# strategy: (min observations, metric description)
KILL_RULES = {
    # value takes live or die by closing line value. Beating the close is the
    # standard proof of edge in sports betting and arrives in days.
    "value": (40, "mean CLV after fees < 0 with t < -1"),
    # a rest-hedge pair's realised credit (after the hedge's actual price)
    "rest_hedge": (20, "mean realised pair credit < 0 with t < -1"),
    # MM fills: markout vs fair one cycle later, net of rebate
    "mm": (50, "mean markout + rebate < 0 with t < -1"),
    # crossed pairs lock their credit at entry unless a leg slips: a losing
    # average means the fills are not the prices the scan saw
    "taker_arb": (15, "mean realised pair credit < 0 with t < -1"),
    "inplay_arb": (15, "mean realised pair credit < 0 with t < -1"),
}


def tstat(xs):
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return mu, (mu / math.sqrt(var / n) if var > 0 else (math.inf if mu > 0 else -math.inf))


def evaluate_kills(metrics, kills):
    """metrics: {strategy: [per-trade metric]}. Returns updated kills dict."""
    out = dict(kills)
    for strat, (min_n, desc) in KILL_RULES.items():
        xs = metrics.get(strat) or []
        if strat in out or len(xs) < min_n:
            continue
        mu, t = tstat(xs)
        if mu < 0 and t < -1:
            out[strat] = f"{desc}: n={len(xs)} mean={mu:+.4f} t={t:+.2f}"
    return out


def halt_reason(realized_total, realized_today, start_equity, limits):
    """A book-wide halt on new risk (management of open risk continues)."""
    if realized_today <= -limits.daily_loss:
        return f"daily loss {realized_today:.2f} hit -{limits.daily_loss}"
    if start_equity > 0 and realized_total <= -limits.max_drawdown_frac * start_equity:
        return (f"drawdown {realized_total:.2f} beyond "
                f"{limits.max_drawdown_frac:.0%} of ${start_equity:.2f}")
    return None
