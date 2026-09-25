"""In-play: the day-trading half of the engine.

Two strategies run while a game is live, and they are held to different
standards on purpose:

  inplay_arb   LIVE. Cross-strike violations on a ladder while it reprices
               chaotically in-play, both legs crossed at once. It needs no
               faster feed and no better prediction, only two strikes
               contradicting each other by more than two taker fees. It is
               levelled and settled by the same machinery as pre-game arbs,
               and settles tonight.

  divergence   PAPER until it passes GO_RULE. Live market vs a live margin
               model (score, clock, pre-game line). Enter when the gap beats
               the full round-trip cost AND has persisted, exit on
               convergence, a stop, a time stop, or before the final two
               minutes. Flat by the whistle: never held to settlement.

Why divergence is paper-only: RESEARCH.md S8 found no in-play momentum or
reversion edge in 128,517 observations, and S4 measured ESPN plays arriving
34-67s late while this venue reprices in 50-100ms. A gap between our model
and the market is therefore, by default, US being stale. The persistence
window, the post-score cooldown and the stable-mid requirement exist to
filter that out; the paper record decides whether they did.

Live model (Stern 1994): final margin = current margin D + the remaining
game, which is ~N(mu_pre * tau, sigma_pre^2 * tau) with tau the fraction of
regulation left and (mu_pre, sigma_pre) from the last pre-game line.
"""

import math
from dataclasses import dataclass, field

from src.income.model import MarginModel
from src.pm_us.fees import taker_fee

# (periods, seconds per period) of regulation
REGULATION = {"cfb": (4, 900), "nfl": (4, 900), "nba": (4, 720),
              "cbb": (2, 1200), "nhl": (3, 1200)}

# Pre-registered, before any in-play result exists. Stricter than the
# pre-game kill rules because this thesis starts with negative evidence:
# t > 2, spread over many games, and it must hold in BOTH halves of the
# sample, so one hot Saturday cannot pass it.
GO_RULE = {"min_trips": 50, "min_games": 6, "t_min": 2.0}


def tau_remaining(league, period, clock):
    """Fraction of regulation left, or None (pre-game, OT, unknown sport)."""
    reg = REGULATION.get(league)
    if not reg or not period:
        return None
    n, secs = reg
    p = int(period)
    if p > n:
        return None                               # overtime: model does not apply
    left = (n - p) * secs + max(float(clock or 0.0), 0.0)
    return max(min(left / (n * secs), 1.0), 0.0)


def live_model(pre_mu, pre_sigma, margin_now, tau, league="cfb"):
    """Distribution of the FINAL margin given the game so far."""
    mu = margin_now + pre_mu * tau
    sigma = max(pre_sigma * math.sqrt(max(tau, 0.0)), 1.0)
    return MarginModel(mu, sigma, league, key_numbers=False)


def round_trip_cost(entry_px, fair, bid, ask, slip=0.005):
    """What a taker round trip costs: fee in, fee out (at ~fair), the spread
    paid on the way out, and slippage for arriving a poll plus 250ms late."""
    half = (ask - bid) / 2.0 if bid is not None and ask is not None else 0.02
    return taker_fee(entry_px) + taker_fee(fair) + half + 2 * slip


@dataclass
class Divergence:
    """Persistence filter for one (game, line)."""
    sign: int = 0
    since: float = 0.0
    mid0: float = 0.0


@dataclass
class Tracker:
    persist_s: float = 90.0         # gap must stand this long
    cooldown_s: float = 150.0       # ignore everything after a score change
    max_mid_move: float = 0.02      # the market must not be moving meanwhile
    buffer: float = 0.01            # edge required beyond the round trip
    state: dict = field(default_factory=dict)
    last_score: dict = field(default_factory=dict)   # game -> (score, t)

    def observe_score(self, game, score, now):
        prev = self.last_score.get(game)
        if prev is None or prev[0] != score:
            self.last_score[game] = (score, now)
            # a score change invalidates every open divergence on the game
            for k in [k for k in self.state if k[0] == game]:
                self.state.pop(k)

    def signal(self, game, line, fair, bid, ask, now):
        """'buy' | 'sell' | None once a gap has stood long enough to trade."""
        if bid is None or ask is None:
            return None
        sc = self.last_score.get(game)
        if sc is None or now - sc[1] < self.cooldown_s:
            return None
        mid = (bid + ask) / 2.0
        buy_gap = fair - ask - round_trip_cost(ask, fair, bid, ask)
        sell_gap = bid - fair - round_trip_cost(bid, fair, bid, ask)
        sign = 1 if buy_gap > self.buffer else (-1 if sell_gap > self.buffer else 0)
        key = (game, float(line))
        d = self.state.get(key)
        if sign == 0:
            self.state.pop(key, None)
            return None
        if d is None or d.sign != sign or abs(mid - d.mid0) > self.max_mid_move:
            self.state[key] = Divergence(sign, now, mid)
            return None
        if now - d.since >= self.persist_s:
            return "buy" if sign > 0 else "sell"
        return None


# ---- paper book ---------------------------------------------------------------

def paper_open(paper, game, line, side, bid, ask, fair, now, qty=10, slip=0.005):
    key = f"{game}|{line}"
    if key in paper:
        return None
    px = (ask + slip) if side == "buy" else (bid - slip)
    paper[key] = {"game": game, "line": float(line), "side": side, "px": px,
                  "qty": qty, "t": now, "fair": fair}
    return paper[key]


def paper_exit_reason(pos, bid, ask, fair, tau, now, state,
                      time_stop_s=900.0, stop=0.08, end_tau=2.0 / 60.0):
    """Why to close now, or None. Flat before the final two minutes."""
    if state != "in" or tau is None or tau <= end_tau:
        return "end_of_game"
    if bid is None or ask is None:
        return None
    if pos["side"] == "buy":
        if bid >= fair - 0.005:
            return "converged"
        if bid <= pos["px"] - stop:
            return "stop"
    else:
        if ask <= fair + 0.005:
            return "converged"
        if ask >= pos["px"] + stop:
            return "stop"
    if now - pos["t"] >= time_stop_s:
        return "time_stop"
    return None


def paper_close(pos, bid, ask, slip=0.005):
    """Net P&L per share of a paper round trip, both taker fees included.
    None when there is no price to exit at (the caller retries)."""
    if pos["side"] == "buy":
        if bid is None:
            return None
        out = bid - slip
        return out - pos["px"] - taker_fee(pos["px"]) - taker_fee(out)
    if ask is None:
        return None
    out = ask + slip
    return pos["px"] - out - taker_fee(pos["px"]) - taker_fee(out)


def go_status(trips):
    """trips: [(game, pnl_per_share, ts)] in time order -> (passed, detail)."""
    from src.income.risk import tstat
    n = len(trips)
    games = len({g for g, _p, _t in trips})
    xs = [p for _g, p, _t in trips]
    mu, t = tstat(xs)
    half = n // 2
    h1 = sum(xs[:half]) / half if half else 0.0
    h2 = sum(xs[half:]) / (n - half) if n - half else 0.0
    ok = (n >= GO_RULE["min_trips"] and games >= GO_RULE["min_games"]
          and mu > 0 and t > GO_RULE["t_min"] and h1 > 0 and h2 > 0)
    return ok, (f"n={n}/{GO_RULE['min_trips']} games={games}/{GO_RULE['min_games']} "
                f"mean={mu:+.4f} t={t:+.2f} halves {h1:+.4f}/{h2:+.4f}")
