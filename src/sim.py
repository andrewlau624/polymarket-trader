"""What a strategy's trades are worth in money: shape, sizing, and swings.

A trade is {t, game, cost, pnl} with pnl per share after fees and cost the
price paid per share (so a $10 stake buys 10 / cost shares).

  shape(trades)     win rate, average win, average loss, payoff ratio (average
                    win / average loss), profit factor (gross wins / gross
                    losses), expectancy per share and per dollar staked.
                    A payoff ratio above 1 is NOT an edge by itself: with a
                    30% win rate you need a payoff above 2.33 just to break even.
                    Expectancy = win% x avg win - loss% x avg loss is the edge.
  replay(trades)    bankroll path, betting fractional Kelly on an edge estimated
                    ONLY from earlier trades (shrunk toward zero), nothing until
                    MIN_HISTORY trades exist, never more than MAX_FRAC of bankroll
                    per bet or MAX_GAME_FRAC per game.
  monte_carlo(...)  the same rule over resampled orderings of whole games: the
                    spread of outcomes you should expect, not the one you got.
"""

import random
from collections import defaultdict

KELLY_FRAC = 0.25        # quarter Kelly: full Kelly on an estimated edge ruins people
MAX_FRAC = 0.05          # never more than 5% of bankroll on one bet
MAX_GAME_FRAC = 0.10     # ...nor 10% on one game: entries in one game share an outcome
MIN_HISTORY = 20         # trades seen before the first real-size bet
SHRINK = 30              # edge estimate weighted n / (n + SHRINK)


def shape(trades):
    if not trades:
        return None
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [-t["pnl"] for t in trades if t["pnl"] <= 0]
    n = len(trades)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    exp = sum(t["pnl"] for t in trades) / n
    staked = sum(t["cost"] for t in trades)
    streak = worst = 0
    for t in sorted(trades, key=lambda x: x["t"]):
        streak = streak + 1 if t["pnl"] <= 0 else 0
        worst = max(worst, streak)
    return {"n": n, "win_rate": len(wins) / n, "avg_win": aw, "avg_loss": al,
            "payoff": aw / al if al else float("inf"),
            "breakeven_payoff": (1 - len(wins) / n) / (len(wins) / n) if wins else float("inf"),
            "profit_factor": sum(wins) / sum(losses) if losses else float("inf"),
            "exp_share": exp, "exp_dollar": sum(t["pnl"] for t in trades) / staked,
            "worst_streak": worst}


def replay(trades, bankroll=200.0):
    """Walk-forward sized bankroll path. Returns (final, max drawdown frac, bets)."""
    b = peak = bankroll
    mdd, hist, bets = 0.0, [], 0
    on_game = defaultdict(float)
    for t in sorted(trades, key=lambda x: x["t"]):
        if len(hist) >= MIN_HISTORY and b > 0:
            n = len(hist)
            edge = (sum(hist) / n) * n / (n + SHRINK)      # per share, shrunk
            c = t["cost"]
            p_hat = min(max(c + edge, 0.0), 0.999)
            f = (p_hat - c) / (1 - c) if c < 1 else 0.0     # Kelly for a $1 binary
            stake = min(max(f, 0.0) * KELLY_FRAC, MAX_FRAC) * b
            stake = min(stake, max(MAX_GAME_FRAC * b - on_game[t["game"]], 0.0))
            if stake > 0:
                on_game[t["game"]] += stake
                b += stake / c * t["pnl"]
                bets += 1
                peak = max(peak, b)
                mdd = max(mdd, (peak - b) / peak if peak else 0.0)
        hist.append(t["pnl"])
    return b, mdd, bets


def monte_carlo(trades, bankroll=200.0, reps=2000, seed=13):
    """Resample whole games with replacement, in random order, and replay."""
    by = defaultdict(list)
    for t in trades:
        by[t["game"]].append(t)
    games = list(by.values())
    if len(games) < 5:
        return None
    rnd = random.Random(seed)
    finals, dds = [], []
    for _ in range(reps):
        seq, clock = [], 0.0
        for _g in games:
            for t in sorted(rnd.choice(games), key=lambda x: x["t"]):
                clock += 1.0
                seq.append({**t, "t": clock})
        f, d, _ = replay(seq, bankroll)
        finals.append(f)
        dds.append(d)
    finals.sort()
    return {"median": finals[reps // 2], "p05": finals[int(0.05 * reps)],
            "p95": finals[int(0.95 * reps)],
            "p_loss": sum(f < bankroll for f in finals) / reps,
            "p_half": sum(d >= 0.5 for d in dds) / reps}


def per_week(trades):
    ts = [t["t"] for t in trades]
    if len(ts) < 2:
        return None
    days = (max(ts) - min(ts)) / 86400.0
    return len(ts) / max(days, 1.0) * 7.0


def describe(title, trades, bankroll=200.0, say=print):
    s = shape(trades)
    say(f"\n  {title}")
    if not s:
        say("    no trades")
        return None
    say(f"    {s['n']} trades | win rate {s['win_rate']:.0%} | avg win {s['avg_win']:+.3f} "
        f"avg loss -{s['avg_loss']:.3f} per share")
    say(f"    payoff ratio {s['payoff']:.2f} (breakeven at this win rate: "
        f"{s['breakeven_payoff']:.2f}) | profit factor {s['profit_factor']:.2f} | "
        f"worst losing streak {s['worst_streak']}")
    say(f"    expectancy {s['exp_share']:+.4f}/share = {s['exp_dollar']:+.1%} per dollar staked")
    f, dd, bets = replay(trades, bankroll)
    say(f"    as recorded, ${bankroll:.0f} quarter-Kelly: -> ${f:.2f} "
        f"({bets} sized bets, max drawdown {dd:.0%})")
    mc = monte_carlo(trades, bankroll)
    if mc:
        wk = per_week(trades)
        say(f"    resampled x2000: median ${mc['median']:.2f} [5%: ${mc['p05']:.2f}, "
            f"95%: ${mc['p95']:.2f}] | P(end down) {mc['p_loss']:.0%} | "
            f"P(50% drawdown) {mc['p_half']:.0%}"
            + (f" | ~{wk:.0f} trades/week" if wk else ""))
    return s
