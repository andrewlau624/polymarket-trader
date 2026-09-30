"""Does the venue misprice daily-high bands against live station data?

    python weather_report.py                 # research/weather/rec-*.jsonl

Every recorded book is priced off the phase-0 history (weather_study.py
--save-table: P(CLI - M = k) by station, local-standard hour, and whether the
6-hour max group has landed) and graded against the CLI high once IEM has it.

  1. coverage      polls, station-days, how often books are two-sided, spreads
  2. calibration   Brier of the venue's mid vs the history's probability, by
                   phase of the day (before the 6-h max, after it, day over)
  3. paper trades  first time a band's YES or NO is >= 2c under the history's
                   probability after the taker fee: bought at the ask (YES) or
                   1 - bid (NO), held to the CLI. Size at that price recorded.
  4. known winner  after the 6-h max at MIA/LAX/SFO (~98% pinned in phase 0):
                   how often band M was still offered at <= 95c, and how much

Verdict, fixed before any data: worth tiny real money only if section 3 has
>= 100 trades over >= 20 station-days with the whole 95% CI above zero.
"""

import argparse
import glob
import json
import os
import random
from collections import defaultdict

import weather_study as ws
from src.pm_us.fees import taker_fee
from src.weather import wx

EDGE = 0.02
MIN_TRADES, MIN_DAYS = 100, 20
PINNED = ("MIA", "LAX", "SFO")


def load(pattern):
    for fp in sorted(glob.glob(pattern)):
        with open(fp) as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def phase(r):
    if r["hour"] >= 24:
        return "day over"
    return "after 6h max" if r["state"]["g6"] else "before 6h max"


def final(st, day, now=None):
    """Is this climate day's CLI the FINAL one? NWS issues partial-day CLIs in
    the afternoon ("high so far"), and the archive files them under the same
    date - grading against one scored two fake +49c trades on day one. The
    final report comes out the next morning local time; wait until 10:00."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    local = now + timedelta(hours=ws.STATIONS[st][1])
    d = datetime.fromisoformat(day).date()
    return local.date() > d + timedelta(days=1) or \
        (local.date() == d + timedelta(days=1) and local.hour >= 10)


def truths(recs, now=None):
    need = defaultdict(set)
    for r in recs:
        need[r["st"]].add(int(r["day"][:4]))
    out = {}
    for st, years in need.items():
        cli = ws.cli_highs(ws.STATIONS[st][0], sorted(years))
        out.update({(st, d): v for d, v in cli.items() if final(st, d, now)})
    return out


def ci(rows, reps=2000, seed=9):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recs", default=os.path.join("research", "weather", "rec-*.jsonl"))
    ap.add_argument("--table", default=ws.TABLE)
    a = ap.parse_args()
    if not os.path.exists(a.table):
        raise SystemExit(f"no {a.table}: run  python weather_study.py --save-table  first")
    table = json.load(open(a.table))
    recs = [r for r in load(a.recs) if r.get("state")]
    print(f"== weather recorder | {len(recs)} polls | "
          f"{len({(r['st'], r['day']) for r in recs})} station-days ==")
    if not recs:
        print("  nothing recorded yet")
        return
    truth = truths(recs)

    # 1. coverage
    q = [b for r in recs for b in r["books"].values() if b]
    two = [b for b in q if b[0] is not None and b[2] is not None
           and b[4] in (None, "MARKET_STATE_OPEN")]
    sp = sorted(b[2] - b[0] for b in two)
    print(f"\n1. COVERAGE: {len(q)} band quotes, two-sided and open {len(two) / max(len(q), 1):.0%}"
          + (f", median spread {sp[len(sp) // 2]:.3f}" if sp else ""))
    graded = [r for r in recs if (r["st"], r["day"]) in truth]
    print(f"   graded against the CLI so far: {len(graded)} polls, "
          f"{len({(r['st'], r['day']) for r in graded})} station-days")

    # 2 + 3 + 4
    brier = defaultdict(lambda: [0.0, 0.0, 0])
    tight = defaultdict(lambda: [0.0, 0.0, 0])     # only books a trader could use
    spreads = defaultdict(list)
    trades, seen = [], set()
    pinned = []
    for r in graded:
        st, s, hi_true = r["st"], r["state"], truth[(r["st"], r["day"])]
        dist = wx.cell(table, st, min(r["hour"], 23), s["g6"])
        if dist is None:
            continue
        ph = phase(r)
        for band, (lo, hi) in r["bands"].items():
            b = r["books"].get(band)
            if not b or b[4] not in (None, "MARKET_STATE_OPEN"):
                continue
            p = wx.band_prob(dist, s["M"], lo, hi)
            won = 1.0 if wx.in_band(hi_true, lo, hi) else 0.0
            if b[0] is not None and b[2] is not None:
                mid = (b[0] + b[2]) / 2
                acc = brier[ph]
                acc[0] += (mid - won) ** 2
                acc[1] += (p - won) ** 2
                acc[2] += 1
                spreads[ph].append(b[2] - b[0])
                if b[2] - b[0] <= 0.10:
                    t_ = tight[ph]
                    t_[0] += (mid - won) ** 2
                    t_[1] += (p - won) ** 2
                    t_[2] += 1
            day = (st, r["day"])
            if b[2] is not None and (day, band, "yes") not in seen \
                    and p - b[2] - taker_fee(b[2]) >= EDGE:
                seen.add((day, band, "yes"))
                trades.append({"day": day, "ph": ph, "side": "YES", "px": b[2],
                               "size": b[3], "p": p,
                               "pnl": won - b[2] - taker_fee(b[2])})
            no_px = None if b[0] is None else round(1 - b[0], 4)
            if no_px is not None and (day, band, "no") not in seen \
                    and (1 - p) - no_px - taker_fee(no_px) >= EDGE:
                seen.add((day, band, "no"))
                trades.append({"day": day, "ph": ph, "side": "NO", "px": no_px,
                               "size": b[1], "p": 1 - p,
                               "pnl": (1 - won) - no_px - taker_fee(no_px)})
            if st in PINNED and ph == "after 6h max" and wx.in_band(s["M"], lo, hi) \
                    and b[2] is not None and b[2] <= 0.95 \
                    and (day, band, "pin") not in seen:
                seen.add((day, band, "pin"))
                pinned.append({"day": day, "px": b[2], "size": b[3],
                               "pnl": won - b[2] - taker_fee(b[2])})

    print("\n2. CALIBRATION (Brier, lower is better): venue mid vs station history")
    for ph in ("before 6h max", "after 6h max", "day over"):
        v, m, n = brier[ph]
        if n:
            sp = sorted(spreads[ph])
            print(f"   {ph:<14} n={n:>6}  venue {v / n:.4f}  history {m / n:.4f}  "
                  f"-> {'HISTORY better' if m < v else 'venue better'}  "
                  f"(median spread {sp[len(sp) // 2]:.2f})")
            tv, tm, tn = tight[ph]
            if tn:
                print(f"   {'  spread<=10c':<14} n={tn:>6}  venue {tv / tn:.4f}  "
                      f"history {tm / tn:.4f}")
            else:
                print(f"   {'  spread<=10c':<14} none: every two-sided book was wider")

    print(f"\n3. PAPER TRADES: first time a side is >= {EDGE:.0%} under the history, after fees")
    days = {t["day"] for t in trades}
    lo, hi = ci([(t["day"], t["pnl"]) for t in trades])
    if trades:
        print(f"   {len(trades)} trades over {len(days)} station-days, mean "
              f"{sum(t['pnl'] for t in trades) / len(trades):+.4f}/share"
              + (f"  CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None else "")
              + f", median size at price {sorted(t['size'] for t in trades)[len(trades) // 2]:.0f}")
        by_day = defaultdict(list)
        for t in trades:
            by_day[t["day"]].append(t["pnl"])
        top = sorted(by_day.items(), key=lambda kv: -sum(kv[1]))
        tot = sum(t["pnl"] for t in trades)
        print(f"     best station-day {top[0][0][0]} {top[0][0][1]}: "
              f"{sum(top[0][1]):+.2f} of the {tot:+.2f} total; without it mean "
              f"{(tot - sum(top[0][1])) / max(len(trades) - len(top[0][1]), 1):+.4f}")
        for key, title in (("ph", "phase"), ("side", "side"), ("st", "station")):
            grp = defaultdict(list)
            for t in trades:
                grp[t["day"][0] if key == "st" else t[key]].append(t["pnl"])
            for k, v in sorted(grp.items()):
                print(f"     {title} {k:<14} n={len(v):>4} mean {sum(v) / len(v):+.4f}")
    else:
        print("   none")

    print("\n4. KNOWN WINNER after the 6h max (MIA/LAX/SFO): band M offered at <= 95c")
    if pinned:
        print(f"   {len(pinned)} times over {len({p['day'] for p in pinned})} station-days, "
              f"won {sum(p['pnl'] > 0 for p in pinned)}, mean "
              f"{sum(p['pnl'] for p in pinned) / len(pinned):+.4f}/share, median size "
              f"{sorted(p['size'] for p in pinned)[len(pinned) // 2]:.0f}")
    else:
        print("   never (so far)")

    ok = len(trades) >= MIN_TRADES and len(days) >= MIN_DAYS and lo is not None and lo > 0
    print(f"\n   verdict: {'GO for tiny real money' if ok else 'not yet'} "
          f"(needs >= {MIN_TRADES} trades, >= {MIN_DAYS} station-days, CI above 0; "
          f"have {len(trades)}, {len(days)})")


if __name__ == "__main__":
    main()
