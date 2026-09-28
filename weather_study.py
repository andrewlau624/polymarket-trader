"""Weather phase 0: can live station data call the settling band before the market?

    python weather_study.py                     # 5 stations, last 2 years
    python weather_study.py --stations NYC --years 1

Polymarket US settles daily-high markets on the NWS Climatological Report
(CLI), e.g. "highest temperature recorded at Central Park (KNYC) ... as reported
by the National Weather Service's Climatological Report (Daily)", in 1F bands.
The CLI is published the next morning. The station's METARs are public all day.

For every station-day and every hour of the (local STANDARD time) climate day,
this rebuilds what was knowable at that hour - the running max of the hourly
and special METAR temperatures (tenths C) and any 6-hour max groups whose
window lies inside the day - and compares it, rounded to whole F (M), with the
CLI high that later settled. Two tables come out:

  lock curve   P(CLI high == M), == M+1, >= M+2, < M, by hour. A late-day
               bucket bet needs P(== M) high; the < M column is how often the
               live number OVERSTATES the settlement (a rounding trap).
  cooling      the same late-day P(== M), split by whether the current reading
               has already fallen >= 3F below the running max.

Ground truth is the CLI archive (IEM json/cli.py), never IEM's plain daily
summary, which reads ~1F low because METAR rounds to whole degrees C.

Kill criterion (set before any result): if no hour reaches P(CLI == M) >= 0.97
for the cooling subset, live data cannot call the band and weather phase 1 is off.
"""

import argparse
import csv
import io
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

IEM = "https://mesonet.agron.iastate.edu"
CACHE = os.path.join("data", "weather")
# the venue's settlement stations, and their standard-time UTC offsets: the
# CLI climate day runs midnight-to-midnight LOCAL STANDARD time all year
STATIONS = {"NYC": ("KNYC", -5), "MDW": ("KMDW", -6), "MIA": ("KMIA", -5),
            "LAX": ("KLAX", -8), "SFO": ("KSFO", -8)}
_T = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_MAX6 = re.compile(r"\s1([01])(\d{3})\b")


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def whole_f(f):
    """Round half up, the way a whole-degree report reads."""
    return int(f + 0.5) if f >= 0 else -int(-f + 0.5)


def parse_metar(metar):
    """(temp_f from the T-group in tenths C or None, 6-hour max F or None)."""
    t = m6 = None
    m = _T.search(metar or "")
    if m:
        t = c_to_f((-1 if m.group(1) == "1" else 1) * int(m.group(2)) / 10.0)
    rmk = (metar or "").split(" RMK ", 1)
    if len(rmk) == 2:
        g = _MAX6.search(" " + rmk[1])
        if g:
            m6 = c_to_f((-1 if g.group(1) == "1" else 1) * int(g.group(2)) / 10.0)
    return t, m6


def cli_highs(st4, years):
    os.makedirs(CACHE, exist_ok=True)
    out = {}
    for y in years:
        fp = os.path.join(CACHE, f"cli_{st4}_{y}.json")
        if os.path.exists(fp) and y < date.today().year:
            res = json.load(open(fp))
        else:
            r = requests.get(f"{IEM}/json/cli.py", params={"station": st4, "year": y}, timeout=60)
            r.raise_for_status()
            res = r.json().get("results", [])
            json.dump(res, open(fp, "w"))
            time.sleep(1)
        for x in res:
            try:
                out[x["valid"]] = int(x["high"])
            except (TypeError, ValueError, KeyError):
                pass
    return out


def observations(st3, start, end):
    """[(utc datetime, tmpf, max6_f)] for [start, end]."""
    fp = os.path.join(CACHE, f"obs_{st3}_{start}_{end}.csv")
    if not os.path.exists(fp):
        r = requests.get(f"{IEM}/cgi-bin/request/asos.py", timeout=300, params={
            "station": st3, "data": ["tmpf", "metar"], "tz": "UTC", "format": "onlycomma",
            "latlon": "no", "report_type": [3, 4],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year, "month2": end.month, "day2": end.day})
        r.raise_for_status()
        open(fp, "w").write(r.text)
        time.sleep(1)
    out = []
    for row in csv.DictReader(io.StringIO(open(fp).read())):
        try:
            ts = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        t, m6 = parse_metar(row.get("metar"))
        if t is None:
            try:
                t = float(row["tmpf"])
            except (TypeError, ValueError):
                t = None
        out.append((ts, t, m6))
    return out


def by_climate_day(obs, offset_h):
    """{date: [(hours since local-standard midnight, tmpf, max6)]}; a 6-hour
    max group is kept only if its whole window falls inside the day."""
    days = defaultdict(list)
    for ts, t, m6 in obs:
        local = ts + timedelta(hours=offset_h)
        d = local.date()
        h = local.hour + local.minute / 60.0
        if m6 is not None and h < 6.0:
            m6 = None                        # window reaches into the day before
        days[d].append((h, t, m6))
    return days


def running_max(pts, upto_h):
    vals = [t for h, t, _ in pts if h <= upto_h and t is not None]
    vals += [m for h, _, m in pts if h <= upto_h and m is not None]
    return max(vals) if vals else None


def has_max6(pts, upto_h, min_h=15.0):
    """Has the AFTERNOON 6-hour max group (the 23:51Z one: 15:51-18:51 local
    standard) arrived? That group is the jump in every phase-0 table. The
    17:51Z group covers the morning and pins nothing, so it does not count."""
    return any(m is not None for h, _, m in pts if min_h <= h <= upto_h)


TABLE = os.path.join("research", "weather_table.json")


def model_table(rows, path=TABLE):
    """P(CLI - M = k) by station, LST hour and whether a 6-hour max has landed.
    The report prices every band off this; built from history only."""
    cells = defaultdict(lambda: defaultdict(int))
    for x in rows:
        k = max(-2, min(5, x["cli"] - x["M"]))
        cells[(x["st"], x["h"], int(x["g6"]))][k] += 1
    out = {}
    for (st, h, g), cnt in cells.items():
        n = sum(cnt.values())
        out.setdefault(st, {}).setdefault(str(h), {})[str(g)] = {
            "n": n, "p": {str(k): v / n for k, v in cnt.items()}}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(out, open(path, "w"))
    return out


def current(pts, upto_h):
    cur = [(h, t) for h, t, _ in pts if h <= upto_h and t is not None]
    return max(cur)[1] if cur else None


def study(stations, years_back):
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=int(365 * years_back))
    rows = []
    for st3 in stations:
        st4, off = STATIONS[st3]
        cli = cli_highs(st4, range(start.year, end.year + 1))
        days = by_climate_day(observations(st3, start, end + timedelta(days=1)), off)
        for d, pts in days.items():
            truth = cli.get(d.isoformat())
            if truth is None or not start <= d <= end or len(pts) < 18:
                continue
            for hour in range(0, 24):
                rm = running_max(pts, hour + 1.0)
                if rm is None:
                    continue
                cur = current(pts, hour + 1.0)
                rows.append({"st": st3, "d": d, "h": hour, "M": whole_f(rm),
                             "cli": truth, "drop": None if cur is None else rm - cur,
                             "g6": has_max6(pts, hour + 1.0)})
    return rows


def pct(n, k):
    return f"{k / n:6.1%}" if n else "   n/a"


def lock_curve(rows, title):
    print(f"\n{title}")
    print("  hour LST     n   CLI=M  CLI=M+1  CLI>=M+2  CLI<M")
    for h in range(8, 24):
        r = [x for x in rows if x["h"] == h]
        n = len(r)
        if not n:
            continue
        diff = [x["cli"] - x["M"] for x in r]
        print(f"  {h:02d}:59  {n:>7} {pct(n, sum(v == 0 for v in diff))} "
              f"{pct(n, sum(v == 1 for v in diff))}   {pct(n, sum(v >= 2 for v in diff))}"
              f"  {pct(n, sum(v < 0 for v in diff))}")


def cooling(rows):
    print("\nCOOLING: late-day P(CLI == M) once the reading has fallen >= 3F below the max")
    print("  hour LST   cooled n  CLI=M   CLI<M  | not cooled n  CLI=M")
    best = 0.0
    for h in range(12, 24):
        r = [x for x in rows if x["h"] == h and x["drop"] is not None]
        c = [x for x in r if x["drop"] >= 3.0]
        w = [x for x in r if x["drop"] < 3.0]
        pc = sum(x["cli"] == x["M"] for x in c) / len(c) if c else 0.0
        best = max(best, pc if len(c) >= 100 else 0.0)
        print(f"  {h:02d}:59   {len(c):>8} {pct(len(c), sum(x['cli'] == x['M'] for x in c))} "
              f"{pct(len(c), sum(x['cli'] < x['M'] for x in c))}  | {len(w):>12} "
              f"{pct(len(w), sum(x['cli'] == x['M'] for x in w))}")
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stations", default=",".join(STATIONS))
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--save-table", action="store_true",
                    help=f"write {TABLE} for weather_report.py")
    a = ap.parse_args()
    st = [s.strip().upper() for s in a.stations.split(",") if s.strip()]
    rows = study(st, a.years)
    if a.save_table:
        model_table(rows)
        print(f"  wrote {TABLE}")
    days = {(x["st"], x["d"]) for x in rows}
    print(f"== weather phase 0 | {len(days)} station-days | {', '.join(st)} ==")
    lock_curve(rows, "LOCK CURVE, all stations (M = running max so far, whole F)")
    for s in st:
        lock_curve([x for x in rows if x["st"] == s and x["h"] in range(14, 24)],
                   f"  {s}, afternoon/evening")
    best = cooling(rows)
    print(f"\n  verdict: best cooled P(CLI == M) with n >= 100 is {best:.1%}; the bar was "
          f"97% -> {'PHASE 1 IS ON' if best >= 0.97 else 'live data cannot call the band'}")


if __name__ == "__main__":
    main()
