"""Chart-only trading on LoL match prices: do trader signals beat costs?

    python lol_ta.py                            # on research/lol_obs.jsonl

No game data. Each game's price path (the venue mid, every ~3 s, from
lol_recorder.py) is resampled to 5 s bars, and five textbook signals trade it
with their settings FIXED HERE, before anyone looked at a result - tuning
them until one works is how a backtest invents money:

  momentum     price up >= 5c over 30 s -> buy it;          exit after 60 s
  reversion    price down >= 5c over 30 s -> buy it (fade);  exit after 60 s
  ma_cross     30 s average crosses above 2 min average -> buy; exit on cross back
  breakout     new 2 min high -> buy;                        exit after 60 s
  rsi          14-bar RSI < 30 -> buy;                       exit when RSI > 50

Every signal runs both ways (a signal to buy team A's price falling is a buy
of team B), enters at the real ASK, exits at the real BID, and pays the taker
fee both times. A round trip at a 50c price costs ~4-5c, so a signal has to
predict moves bigger than that.

Per signal: trades, games, mean P&L per share, a 95% CI resampling GAMES, and
the same mean on each half of the games. A signal counts only if the CI is
above zero AND both halves are positive.
"""

import argparse
import random
from collections import defaultdict
from datetime import datetime

from src.pm_us.fees import taker_fee
from src.pm_us.jsonlog import iter_records

BAR = 5.0
MIN_GAMES = 20


def _t(s):
    return datetime.fromisoformat(s).timestamp()


def paths(recs):
    """{game: [(t, bid, ask)]} on the match market (Bo1) or the live game market."""
    out = defaultdict(list)
    for r in recs:
        k = f"map:{r['game']}" if f"map:{r['game']}" in r["q"] else "match"
        x = r["q"].get(k)
        if not x or x[4] not in (None, "MARKET_STATE_OPEN") or x[0] is None or x[2] is None:
            continue
        out[f"{r['event']}#{r['game']}"].append((_t(r["ts"]), x[0], x[2]))
    return out


def bars(pts):
    """5 s bars: (bid, ask, mid) of the last quote in each bar; gaps carried."""
    if not pts:
        return []
    t0 = pts[0][0]
    last, out, i = None, [], 0
    n = int((pts[-1][0] - t0) // BAR) + 1
    for b in range(n):
        while i < len(pts) and pts[i][0] < t0 + (b + 1) * BAR:
            last = pts[i]
            i += 1
        if last:
            out.append((last[1], last[2], (last[1] + last[2]) / 2))
    return out


def rsi(mids, n=14):
    if len(mids) <= n:
        return None
    up = sum(max(mids[i] - mids[i - 1], 0) for i in range(-n, 0))
    dn = sum(max(mids[i - 1] - mids[i], 0) for i in range(-n, 0))
    if up + dn == 0:
        return 50.0
    return 100.0 * up / (up + dn)


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def signals(mids, i):
    """{name: +1 buy A / -1 buy B / 0} at bar i, from bars <= i only."""
    s = {}
    k30, k120 = int(30 / BAR), int(120 / BAR)
    if i >= k30:
        d = mids[i] - mids[i - k30]
        s["momentum"] = 1 if d >= 0.05 else -1 if d <= -0.05 else 0
        s["reversion"] = -s["momentum"]
    if i >= k120 + 1:
        f0, f1 = mean(mids[i - k30:i]), mean(mids[i - k30 + 1:i + 1])
        s0, s1 = mean(mids[i - k120:i]), mean(mids[i - k120 + 1:i + 1])
        s["ma_cross"] = 1 if f0 <= s0 and f1 > s1 else -1 if f0 >= s0 and f1 < s1 else 0
        hi, lo = max(mids[i - k120:i]), min(mids[i - k120:i])
        s["breakout"] = 1 if mids[i] > hi else -1 if mids[i] < lo else 0
    r = rsi(mids[:i + 1])
    if r is not None:
        s["rsi"] = 1 if r < 30 else -1 if r > 70 else 0
    return s


def exit_bar(name, mids, i, side):
    """Bar index at which this signal exits a position opened at bar i."""
    k60 = int(60 / BAR)
    if name in ("momentum", "reversion", "breakout"):
        return i + k60
    k30, k120 = int(30 / BAR), int(120 / BAR)
    for j in range(i + 1, len(mids)):
        if name == "ma_cross":
            f, s = mean(mids[j - k30 + 1:j + 1]), mean(mids[j - k120 + 1:j + 1])
            if (f < s) if side > 0 else (f > s):
                return j
        if name == "rsi":
            r = rsi(mids[:j + 1])
            if r is not None and ((r > 50) if side > 0 else (r < 50)):
                return j
    return None


def trade(b, i, j, side):
    """Buy at the ask, sell at the bid, fees both ways. side -1 = buy team B."""
    if side > 0:
        cost, out = b[i][1], b[j][0]
    else:
        cost, out = 1 - b[i][0], 1 - b[j][1]
    return out - cost - taker_fee(cost) - taker_fee(out)


def run(game_paths):
    res = defaultdict(list)              # name -> [(game, pnl)]
    for g, pts in game_paths.items():
        b = bars(pts)
        mids = [x[2] for x in b]
        busy = {}
        for i in range(len(b)):
            for name, side in signals(mids, i).items():
                if not side or busy.get(name, -1) >= i:
                    continue
                j = exit_bar(name, mids, i, side)
                if j is None or j >= len(b):
                    continue
                res[name].append((g, trade(b, i, j, side)))
                busy[name] = j           # one position per signal at a time
    return res


def ci(rows, reps=2000, seed=3):
    by = defaultdict(list)
    for g, v in rows:
        by[g].append(v)
    gs = list(by.values())
    if len(gs) < 2:
        return None, None
    rnd = random.Random(seed)
    ms = sorted(mean([v for _ in gs for v in rnd.choice(gs)]) for _ in range(reps))
    return ms[int(0.025 * reps)], ms[int(0.975 * reps)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--obs", default="research/lol_obs.jsonl")
    a = ap.parse_args()
    gp = paths(iter_records(a.obs))
    order = sorted(gp, key=lambda g: gp[g][0][0])
    half = set(order[: len(order) // 2])
    print(f"== chart-only signals | {len(gp)} games | 5 s bars | ask in, bid out, fees in ==")
    res = run(gp)
    for name in ("momentum", "reversion", "ma_cross", "breakout", "rsi"):
        rows = res.get(name, [])
        if not rows:
            print(f"  {name:<10} no trades")
            continue
        lo, hi = ci(rows)
        h1 = [v for g, v in rows if g in half]
        h2 = [v for g, v in rows if g not in half]
        ok = lo is not None and lo > 0 and h1 and h2 and mean(h1) > 0 and mean(h2) > 0
        print(f"  {name:<10} trades {len(rows):>4} games {len({g for g, _ in rows}):>3} "
              f"mean {mean([v for _, v in rows]):+.4f}/share"
              + (f"  CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None else "")
              + f"  halves {mean(h1):+.4f} / {mean(h2):+.4f}"
              + ("  <- PASSES" if ok else ""))
    cost = [taker_fee(ask) + taker_fee(bid) + (ask - bid)       # pts are (t, bid, ask)
            for pts in gp.values() for _t, bid, ask in pts]
    if cost:
        print(f"\n  a round trip here costs {mean(cost):.4f}/share on average "
              f"(both fees + the spread); that is the bar every signal must clear")
    print(f"  verdict needs >= {MIN_GAMES} games; have {len(gp)}")


if __name__ == "__main__":
    main()
