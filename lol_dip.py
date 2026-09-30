"""Buy the dip when the game is still winnable: a LoL mean-reversion test.

    python lol_dip.py                        # on research/lol_obs.jsonl

The idea (the user's): mid-game, a team's price can crash after a fight, but
if the game STATE says it is still close, one fight can swing it back. So buy
the crash only when the state says the game is live. Rules fixed here, before
any result:

  drop     the game-market mid for one team falls >= 10c within 60 s
  state    (feed, as known at that moment - it trails real time ~26 s)
             minute 14-30          the "one fight can reverse it" window
             |gold diff| <= 3000   still close
             no inhibitor lead for the other side
  entry    buy the dropped team at the ask; at most one entry per team per 3 min
  exits    all reported, each on the same entries:
             sell at the bid after 60 / 120 / 300 s
             bracket: take +10c or stop -10c on the bid, else sell at 300 s
             hold to the game's end (settled from the final price >= 0.95 / <= 0.05)
  control  the same drops with NO state filter. If the filter does not beat
           it, the stats are not adding anything.

Fees both ways (entry and any sale). CI resamples GAMES. A rule counts only
with the CI above zero AND both halves of the games positive.
"""

import argparse
import random
from collections import defaultdict
from datetime import datetime

from src.pm_us.fees import taker_fee
from src.pm_us.jsonlog import iter_records

DROP, DROP_S = 0.10, 60.0
MIN_MIN, MAX_MIN = 14.0, 30.0
MAX_GOLD = 3000
COOLDOWN = 180.0
HOLDS = (60, 120, 300)
TAKE, STOP = 0.10, 0.10


def _t(s):
    return datetime.fromisoformat(s).timestamp()


def games(recs):
    """{game: [(t, bid_A, ask_A, feed)]} for open, two-sided game markets."""
    out = defaultdict(list)
    for r in recs:
        k = f"map:{r['game']}" if f"map:{r['game']}" in r["q"] else "match"
        x = r["q"].get(k)
        if not x or x[4] not in (None, "MARKET_STATE_OPEN") or x[0] is None or x[2] is None:
            continue
        out[f"{r['event']}#{r['game']}"].append((_t(r["ts"]), x[0], x[2], r.get("feed")))
    return out


def winner(pts):
    """+1 team A won, -1 team B, None if the recording ends undecided."""
    m = (pts[-1][1] + pts[-1][2]) / 2
    return 1 if m >= 0.95 else -1 if m <= 0.05 else None


def live_enough(feed, side):
    """Is the game still winnable for `side` (+1 A, -1 B), by the feed?"""
    if not feed or feed.get("minute") is None:
        return False
    if not MIN_MIN <= feed["minute"] <= MAX_MIN:
        return False
    gold = feed.get("gold") or 0
    if abs(gold) > MAX_GOLD:
        return False
    return (feed.get("inhibs") or 0) * side >= 0     # no inhib lead against us


def price(pt, side, which):
    """Cost to buy (ask) or proceeds to sell (bid) of `side` at point pt."""
    _t_, bid, ask, _f = pt
    if side > 0:
        return ask if which == "buy" else bid
    return 1 - bid if which == "buy" else 1 - ask


def entries(pts, use_filter):
    out, last = [], {1: -1e9, -1: -1e9}
    for i, pt in enumerate(pts):
        t = pt[0]
        prev = [p for p in pts[:i] if t - p[0] <= DROP_S]
        if not prev:
            continue
        for side in (1, -1):
            fall = price(prev[0], side, "sell") - price(pt, side, "sell")
            if fall < DROP or t - last[side] < COOLDOWN:
                continue
            if use_filter and not live_enough(pt[3], side):
                continue
            last[side] = t
            out.append((i, side))
    return out


def outcomes(pts, i, side, win):
    """{exit: pnl per share after fees} for one entry."""
    cost = price(pts[i], side, "buy")
    fee_in = taker_fee(cost)
    res = {}
    t0 = pts[i][0]
    for h in HOLDS:
        later = next((p for p in pts[i:] if p[0] - t0 >= h), None)
        if later is not None:
            out = price(later, side, "sell")
            res[f"sell{h}s"] = out - cost - fee_in - taker_fee(out)
    for p in pts[i + 1:]:
        out = price(p, side, "sell")
        if out >= cost + TAKE or out <= cost - STOP or p[0] - t0 >= HOLDS[-1]:
            res["bracket"] = out - cost - fee_in - taker_fee(out)
            break
    if win is not None:
        res["hold_to_end"] = (1.0 if win == side else 0.0) - cost - fee_in
    return res


def ci(rows, reps=2000, seed=4):
    by = defaultdict(list)
    for g, v in rows:
        by[g].append(v)
    gs = list(by.values())
    if len(gs) < 2:
        return None, None
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        pick = [v for _ in gs for v in rnd.choice(gs)]
        ms.append(sum(pick) / len(pick))
    ms.sort()
    return ms[int(0.025 * reps)], ms[int(0.975 * reps)]


def run(gp, use_filter, wins=None, trades=None):
    """exit -> [(game, pnl)]; `trades` (if given) collects sim.py trade dicts."""
    rows = defaultdict(list)
    for g, pts in gp.items():
        win = (wins or {}).get(g) or winner(pts)
        for i, side in entries(pts, use_filter):
            cost = price(pts[i], side, "buy")
            for k, v in outcomes(pts, i, side, win).items():
                rows[k].append((g, v))
                if trades is not None:
                    trades[k].append({"t": pts[i][0], "game": g, "cost": cost, "pnl": v})
    return rows


def report(title, rows, half):
    print(f"\n  {title}")
    for k in [f"sell{h}s" for h in HOLDS] + ["bracket", "hold_to_end"]:
        r = rows.get(k) or []
        if not r:
            print(f"    {k:<12} no trades")
            continue
        m = sum(v for _, v in r) / len(r)
        lo, hi = ci(r)
        h1 = [v for g, v in r if g in half]
        h2 = [v for g, v in r if g not in half]
        mh = lambda xs: sum(xs) / len(xs) if xs else 0.0
        ok = lo is not None and lo > 0 and h1 and h2 and mh(h1) > 0 and mh(h2) > 0
        print(f"    {k:<12} n={len(r):>4} games {len({g for g, _ in r}):>3} "
              f"mean {m:+.4f}" + (f"  CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None else "")
              + f"  halves {mh(h1):+.4f} / {mh(h2):+.4f}" + ("  <- PASSES" if ok else ""))


PRIMARY = "hold_to_end"          # the one rule judged (pre-registered 2026-09-30)
MIN_DECIDED = 40


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--obs", default="research/lol_obs.jsonl")
    ap.add_argument("--bankroll", type=float, default=200.0)
    ap.add_argument("--no-riot", action="store_true", help="winners from final price only")
    a = ap.parse_args()
    recs = list(iter_records(a.obs))
    gp = games(recs)
    wins = {}
    if not a.no_riot:
        try:
            from src.esports.results import completed, winners
            wins = winners(recs, completed())
        except Exception as e:
            print(f"  (Riot results unavailable: {type(e).__name__}; using final prices)")
    order = sorted(gp, key=lambda g: gp[g][0][0])
    half = set(order[: len(order) // 2])
    decided = sum(1 for g, p in gp.items() if (wins.get(g) or winner(p)) is not None)
    print(f"== buy the recoverable dip | {len(gp)} games ({decided} with a known winner, "
          f"{sum(1 for g in gp if g in wins)} from Riot) ==")
    tf, tc = defaultdict(list), defaultdict(list)
    report("WITH the state filter (minute 14-30, gold within 3k, no inhib lead against)",
           run(gp, True, wins, tf), half)
    report("CONTROL: every 10c drop, no filter", run(gp, False, wins, tc), half)

    from src import sim
    print(f"\n== in money: shape of the trades and a ${a.bankroll:.0f} bankroll ==")
    for label, tr in (("CONTROL", tc), ("FILTERED", tf)):
        for k in (PRIMARY, "bracket"):
            sim.describe(f"{label} {k}", tr.get(k) or [], a.bankroll)
    prim = [(t["game"], t["pnl"]) for t in tc.get(PRIMARY) or []]
    ng = len({g for g, _ in prim})
    lo, _hi = ci(prim) if prim else (None, None)
    h1 = [v for g, v in prim if g in half]
    h2 = [v for g, v in prim if g not in half]
    ok = ng >= MIN_DECIDED and lo is not None and lo > 0 and h1 and h2 \
        and sum(h1) > 0 and sum(h2) > 0
    print(f"\n  VERDICT on the pre-registered rule (10c crash, no filter, hold to end): "
          f"{'GO' if ok else 'not yet'} - needs >= {MIN_DECIDED} decided games, CI above 0, "
          f"both halves positive; have {ng} games"
          + (f", CI low {lo:+.4f}" if lo is not None else ""))
    print("\n  The filter earns its keep only if it beats the control AND passes on its own."
          "\n  Needs 20+ games before any of this means much.")


if __name__ == "__main__":
    main()
