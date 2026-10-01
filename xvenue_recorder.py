"""Record the same games' books on Kalshi and Polymarket US. Places NO orders.

    python xvenue_recorder.py --minutes 4.6        # what xvenue_cycle.sh runs
    python xvenue_recorder.py --minutes 30 --refresh

TEST_PLAN.md is the spec. Public endpoints only, so it needs no API keys.

Every sweep, for each paired game starting within --ahead hours or in play
(started < 5 h ago), one line to research/xvenue/rec-<UTC date>.jsonl:

    pm  [bid, bid size, ask, ask size, state] for Polymarket's LONG team
    kl  [yes bid, size, yes ask, size, status] for the same team on Kalshi
    ks  the same for Polymarket's SHORT team on Kalshi
    tp, tk  when each venue answered: they are fetched back to back per game,
            because a minute between snapshots manufactures fake gaps

The pairing (src/xvenue/core.pair_games) is rebuilt from the full Polymarket
listing every --refresh-hours. That listing is ~145 pages and takes ~95 s.
"""

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from src.xvenue import core

PM = "https://gateway.polymarket.us/v1"
KAL = "https://api.elections.kalshi.com/trade-api/v2"
OUT_DIR = os.path.join("research", "xvenue")
PAIRS = os.path.join(OUT_DIR, "pairs.json")
LOCK = os.path.join("research", "xvenue.lock")


def get(url, params=None, tries=5):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    delay = 1.0
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ValueError):
            if i == tries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    return None


def pm_moneylines(say):
    out, i = [], 0
    while True:
        d = get(f"{PM}/markets", {"limit": 500, "offset": i * 500,
                                  "active": "true", "closed": "false"}) or {}
        page = d.get("markets", [])
        out += [m for m in page if m.get("marketType") == "moneyline"
                and m.get("slug", "").split("-")[1:2] and m["slug"].split("-")[1] in core.LEAGUES]
        if len(page) < 500:
            break
        i += 1
        time.sleep(0.3)
    say(f"  polymarket listing: {i + 1} pages, {len(out)} moneylines in scope")
    return out


def k_markets():
    out = []
    for series in core.LEAGUES.values():
        cur = None
        while True:
            q = {"series_ticker": series, "status": "open", "limit": 1000}
            if cur:
                q["cursor"] = cur
            d = get(f"{KAL}/markets", q) or {}
            out += d.get("markets", [])
            cur = d.get("cursor")
            if not cur:
                break
            time.sleep(0.2)
        time.sleep(0.2)
    return out


def load_pairs(refresh_h, force, say):
    try:
        with open(PAIRS) as fh:
            d = json.load(fh)
        fresh = time.time() - d["t"] < refresh_h * 3600
    except (OSError, ValueError, KeyError):
        d, fresh = None, False
    if d and fresh and not force:
        return d["games"]
    games = core.pair_games(pm_moneylines(say), k_markets())
    tmp = PAIRS + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"t": time.time(), "games": games}, fh)
    os.replace(tmp, PAIRS)
    say(f"  paired {len(games)} games")
    return games


def in_window(g, now, ahead_h):
    try:
        st = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return now - timedelta(hours=5) <= st <= now + timedelta(hours=ahead_h)


def snap(g):
    """Both venues for one game, back to back."""
    book = get(f"{PM}/markets/{g['pm_slug']}/book")
    tp = time.time()
    ev = get(f"{KAL}/events/{g['game']}", {"with_nested_markets": "true"}) or {}
    tk = time.time()
    ms = {m["ticker"]: m for m in (ev.get("event") or {}).get("markets") or ev.get("markets") or []}
    kl, ks = ms.get(g["k_long"]), ms.get(g["k_short"])
    return {"pm": core.pm_quote(book) if book else None,
            "kl": core.k_quote(kl) if kl else None,
            "ks": core.k_quote(ks) if ks else None,
            "tp": round(tp, 2), "tk": round(tk, 2)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=4.6)
    ap.add_argument("--ahead", type=float, default=168.0, help="hours before start to record")
    ap.add_argument("--refresh-hours", type=float, default=6.0)
    ap.add_argument("--refresh", action="store_true", help="rebuild the pairing now")
    ap.add_argument("--pause", type=float, default=0.25, help="seconds between games")
    a = ap.parse_args(argv)
    t_end = time.time() + a.minutes * 60
    os.makedirs(OUT_DIR, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    say = lambda *x: print(*x, flush=True)
    now = datetime.now(timezone.utc)
    say(f"xvenue_recorder | {now.isoformat()[:19]} |")
    games = load_pairs(a.refresh_hours, a.refresh, say)
    sweeps = lines = 0
    while time.time() < t_end:
        now = datetime.now(timezone.utc)
        todo = [g for g in games if in_window(g, now, a.ahead)]
        if not todo:
            say("  no paired games in the window")
            break
        fp = os.path.join(OUT_DIR, f"rec-{now.date().isoformat()}.jsonl")
        with open(fp, "a") as fh:
            for g in todo:
                if time.time() >= t_end:
                    break
                try:
                    s = snap(g)
                except Exception as e:
                    say(f"  {g['game']}: {type(e).__name__} {str(e)[:60]}")
                    continue
                st = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
                fh.write(json.dumps({
                    "t": s["tp"], "sweep": sweeps, "game": g["game"], "league": g["league"],
                    "start": g["start"], "live": now >= st, "pm_slug": g["pm_slug"],
                    "long": g["long"], "short": g["short"],
                    "k_long": g["k_long"], "k_short": g["k_short"], **s}) + "\n")
                lines += 1
                time.sleep(a.pause)
        sweeps += 1
    say(f"  {sweeps} sweeps, {lines} lines, {len(todo) if games else 0} games in window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
