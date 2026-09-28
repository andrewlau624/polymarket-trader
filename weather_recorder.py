"""Record daily-high temperature books beside live station data. Places NO orders.

    python weather_recorder.py --minutes 4.6          # what weather_cycle.sh runs
    python weather_recorder.py --minutes 60 --every 60

Every --every seconds, for each of the five settlement stations, one line per
open market day to research/weather/rec-<UTC date>.jsonl:

    the climate day and its local-standard hour
    live station state: running max M (whole F), current reading, how far it
    has fallen, whether today's 6-hour max group has landed (g6)
    every band's book: bid, size, ask, size, state

Daily files, not the rotating ledger: a week of this must survive intact for
weather_report.py, which prices each band off the phase-0 table and grades it
against the CLI high once that is published.
"""

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from src.weather import wx

OUT_DIR = os.path.join("research", "weather")
LOCK = os.path.join("research", "weather.lock")
INV_CACHE = os.path.join("research", "inventory_cache.json")


def markets(slugs, now):
    """{(station, day): {band: (lo, hi, slug)}} for climate days still trading:
    today's, and yesterday's until its CLI settles the next morning."""
    out = {}
    for sl in slugs:
        p = wx.parse(sl)
        if not p:
            continue
        st, day, band, lo, hi = p
        today, _ = wx.climate_day(now, st)
        yday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
        if day not in (today, yday):
            continue
        out.setdefault((st, day), {})[band] = (lo, hi, sl)
    return out


def slugs_from_cache():
    try:
        with open(INV_CACHE) as fh:
            return set(json.load(fh)["slugs"])
    except (OSError, ValueError, KeyError):
        return None


def quote(c, slug):
    try:
        bids, asks, state = c.book_levels(slug)
    except Exception:
        return None
    return [bids[0][0] if bids else None, bids[0][1] if bids else 0,
            asks[0][0] if asks else None, asks[0][1] if asks else 0, state]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=4.6)
    ap.add_argument("--every", type=float, default=90.0)
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args(argv)
    t_end = time.time() + a.minutes * 60
    os.makedirs("research", exist_ok=True)
    os.makedirs(a.out_dir, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    say = lambda *x: print(*x, flush=True)
    from src.pm_us.client import UsClient
    c = UsClient()
    slugs = slugs_from_cache()
    if slugs is None:
        from income_bot import all_slugs
        slugs = all_slugs(c, say)
    now = datetime.now(timezone.utc)
    mk = markets(slugs, now)
    stamp = f"weather_recorder | {now.isoformat()[:19]} |"
    if not mk:
        say(f"{stamp} no open temperature markets in the listing")
        return 0
    stations = sorted({st for st, _ in mk})
    say(f"{stamp} {len(mk)} station-days, {sum(len(v) for v in mk.values())} bands")
    polls = 0
    while time.time() < t_end:
        t0 = time.time()
        now = datetime.now(timezone.utc)
        try:
            obs = wx.live_obs(stations)
        except Exception as e:
            say(f"  metar fetch failed: {type(e).__name__}")
            obs = None
        fp = os.path.join(a.out_dir, f"rec-{now.date().isoformat()}.jsonl")
        with open(fp, "a") as fh:
            for (st, day), bands in sorted(mk.items()):
                today, hour = wx.climate_day(now, st)
                s = wx.state(obs[st], st, day) if obs else None
                books = {b: quote(c, sl) for b, (_lo, _hi, sl) in bands.items()}
                fh.write(json.dumps({
                    "ts": now.isoformat(), "st": st, "day": day,
                    "hour": hour if day == today else 24, "state": s,
                    "bands": {b: [lo, hi] for b, (lo, hi, _sl) in bands.items()},
                    "books": books}) + "\n")
        polls += 1
        time.sleep(max(0.0, a.every - (time.time() - t0)))
    say(f"  {polls} polls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
