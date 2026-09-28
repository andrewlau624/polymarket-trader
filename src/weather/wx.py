"""Daily-high temperature markets: bands, live station state, band probabilities.

Venue slugs (settled on the NWS CLI high, whole F):
  tc-temp-nychigh-2026-09-25-lt67f        high <= 66
  tc-temp-nychigh-2026-09-25-gte67lt68f   high == 67
  tc-temp-nychigh-2026-09-25-gte72f       high >= 72

The live state is rebuilt exactly as weather_study.py rebuilds history, so the
probability table it produced applies: M is the running max of the climate
day's METAR temperatures (tenths C) and in-day 6-hour max groups, whole F;
g6 says whether a 6-hour max group has landed yet.
"""

import re
from datetime import datetime, timedelta, timezone

import requests

import weather_study as ws

AWC = "https://aviationweather.gov/api/data/metar"
CITY = {"nychigh": "NYC", "mdwhigh": "MDW", "miahigh": "MIA", "laxhigh": "LAX",
        "sfohigh": "SFO"}
_SLUG = re.compile(r"^tc-temp-(?P<city>[a-z]+high)-(?P<date>\d{4}-\d{2}-\d{2})-(?P<band>.+)$")
_BAND = re.compile(r"^(?:lt(?P<lt>\d+)f|gte(?P<a>\d+)lt(?P<b>\d+)f|gte(?P<gte>\d+)f)$")


def parse(slug):
    """(station, date, band, lo, hi) - inclusive whole-F bounds, None = open."""
    m = _SLUG.match(slug or "")
    if not m or m.group("city") not in CITY:
        return None
    b = _BAND.match(m.group("band"))
    if not b:
        return None
    if b.group("lt"):
        lo, hi = None, int(b.group("lt")) - 1
    elif b.group("a"):
        lo, hi = int(b.group("a")), int(b.group("b")) - 1
    else:
        lo, hi = int(b.group("gte")), None
    return CITY[m.group("city")], m.group("date"), m.group("band"), lo, hi


def in_band(v, lo, hi):
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def band_prob(dist, M, lo, hi):
    """P(CLI high lands in [lo, hi]) given P(CLI - M = k)."""
    return sum(p for k, p in dist.items() if in_band(M + int(k), lo, hi))


def cell(table, st, hour, g6, min_n=50):
    """The history's distribution for this station/hour/state, or None if thin."""
    c = ((table.get(st) or {}).get(str(hour)) or {}).get(str(int(g6)))
    return c["p"] if c and c["n"] >= min_n else None


def climate_day(now, st):
    """The CLI day (local STANDARD time) running at `now`, and its hour."""
    off = ws.STATIONS[st][1]
    local = now + timedelta(hours=off)
    return local.date().isoformat(), local.hour


def live_obs(stations, hours=30):
    """{station: [(utc, tmpf, max6_f)]} from aviationweather.gov, raw METARs."""
    ids = ",".join(ws.STATIONS[s][0] for s in stations)
    r = requests.get(AWC, params={"ids": ids, "format": "json", "hours": hours}, timeout=30)
    r.raise_for_status()
    by4 = {ws.STATIONS[s][0]: s for s in stations}
    out = {s: [] for s in stations}
    for x in r.json() or []:
        st = by4.get(x.get("icaoId"))
        if not st or x.get("obsTime") is None:
            continue
        t, m6 = ws.parse_metar(x.get("rawOb"))
        if t is None and x.get("temp") is not None:
            t = ws.c_to_f(float(x["temp"]))
        out[st].append((datetime.fromtimestamp(int(x["obsTime"]), timezone.utc), t, m6))
    return out


def state(obs, st, day):
    """Live {M, cur, drop, g6, n_obs, last_obs} for one station's climate day."""
    days = ws.by_climate_day(obs, ws.STATIONS[st][1])
    pts = days.get(datetime.fromisoformat(day).date()) or []
    rm = ws.running_max(pts, 24.0)
    if rm is None:
        return None
    cur = ws.current(pts, 24.0)
    return {"M": ws.whole_f(rm), "max_f": round(rm, 2), "cur": cur,
            "drop": None if cur is None else round(rm - cur, 2),
            "g6": ws.has_max6(pts, 24.0), "n_obs": len(pts),
            "last_obs": max(o[0] for o in obs).isoformat() if obs else None}
