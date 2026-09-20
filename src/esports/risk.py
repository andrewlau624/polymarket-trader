"""Position sizing and risk limits for the esports book.

Kelly gives the growth-optimal stake for a KNOWN edge. Ours is estimated from
70 observations, so full Kelly is the fastest way to lose the account: it sizes
on a point estimate as though it were a fact. The measured +0.107 at a price of
0.82 implies f* = 0.59 of bankroll on a single match. That is not a plan.

So: fractional Kelly on the CONSERVATIVE END of the confidence interval, then
three caps that bind before it does.

  1. Per-position   - no single leg dominates
  2. PER EVENT      - the real one. map1, map2, match and totals on one series
                      are the same bet wearing four hats. Correlated legs sized
                      independently is how a "diversified" book turns out to be
                      one position.
  3. Total exposure - and a drawdown breaker that stops trading rather than
                      sizing down, because a broken model does not self-correct.

Arbitrage legs are exempt from the edge-based sizing: an LP-verified pair has
no downside state, so it is limited by capital and depth, not by Kelly.
"""

from dataclasses import dataclass, field


@dataclass
class Limits:
    bankroll: float
    kelly_fraction: float = 0.25      # quarter Kelly on the CI's low end
    max_per_position: float = 0.05    # of bankroll
    max_per_event: float = 0.10       # all legs on one series combined
    max_total: float = 0.60           # deployed at once
    max_drawdown: float = 0.20        # halt, do not shrink
    min_edge: float = 0.03            # below this the spread eats it


@dataclass
class Book:
    limits: Limits
    peak: float = 0.0
    deployed: float = 0.0
    by_event: dict = field(default_factory=dict)
    realized: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def equity(self):
        return self.limits.bankroll + self.realized

    def check_drawdown(self):
        eq = self.equity()
        self.peak = max(self.peak, eq)
        if self.peak > 0 and (self.peak - eq) / self.peak > self.limits.max_drawdown:
            self.halted = True
            self.halt_reason = (f"drawdown {(self.peak - eq) / self.peak:.1%} "
                                f"exceeds {self.limits.max_drawdown:.0%}")
        return not self.halted


def kelly_binary(p_true, price, side="buy"):
    """Growth-optimal fraction for a binary paying 1.

    buy at `price`:  risk `price`, gain (1-price).  f* = (p - price)/(1 - price)
    sell at `price`: risk (1-price), gain `price`.  f* = (price - p)/price
    """
    p = min(max(p_true, 1e-6), 1 - 1e-6)
    q = min(max(price, 1e-6), 1 - 1e-6)
    f = (p - q) / (1 - q) if side == "buy" else (q - p) / q
    return max(f, 0.0)


def size_edge_trade(book, event, price, edge_low, side="buy"):
    """Units to trade on a statistical edge, after Kelly shrinkage and caps.

    edge_low is the LOW end of the edge's confidence interval, not the point
    estimate. Sizing on the point estimate treats 70 observations as certainty.
    """
    L = book.limits
    if not book.check_drawdown():
        return 0.0, book.halt_reason
    if edge_low < L.min_edge:
        return 0.0, f"edge {edge_low:.3f} below floor {L.min_edge:.3f}"

    p_true = price + edge_low if side == "buy" else price - edge_low
    f = kelly_binary(p_true, price, side) * L.kelly_fraction
    eq = book.equity()
    dollars = f * eq

    cap_pos = L.max_per_position * eq
    used_ev = book.by_event.get(event, 0.0)
    cap_ev = max(L.max_per_event * eq - used_ev, 0.0)
    cap_tot = max(L.max_total * eq - book.deployed, 0.0)
    allowed = min(dollars, cap_pos, cap_ev, cap_tot)
    if allowed <= 0:
        return 0.0, "a cap is binding"

    at_risk = price if side == "buy" else (1.0 - price)
    units = allowed / max(at_risk, 0.01)
    why = (f"kelly {f:.3f} -> ${dollars:.2f}; caps pos ${cap_pos:.2f} "
           f"event ${cap_ev:.2f} total ${cap_tot:.2f}")
    return units, why


def register(book, event, dollars):
    book.deployed += dollars
    book.by_event[event] = book.by_event.get(event, 0.0) + dollars


def size_arbitrage(book, needed_capital):
    """Arbitrage has no losing state, so Kelly does not apply.

    Bounded by free capital and by the LP's depth caps - not by edge, because
    the worst case is already non-negative by construction.
    """
    L = book.limits
    if not book.check_drawdown():
        return 0.0, book.halt_reason
    free = max(L.max_total * book.equity() - book.deployed, 0.0)
    take = min(needed_capital, free)
    return take, f"free ${free:.2f} of ${L.max_total * book.equity():.2f} allowance"
