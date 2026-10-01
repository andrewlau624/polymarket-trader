"""T5: snapshot bet365 odds for Czech Liga Pro via OddsPapi (8 requests/day budget).

    .venv/bin/python hunt/clp_odds.py            # one snapshot -> research/clp/odds-<ts>.json
    .venv/bin/python hunt/clp_odds.py --loop 4   # every 4 h, forever

OddsPapi's free tier allows ~250 requests a month: 6 snapshots (every 4 h) plus
one names call a day is ~210. One bulk call returns every
upcoming fixture of the tournament for one bookmaker. Fixture ids are Sportradar
ids, which Polymarket US events carry as sportradarGameId.
"""

import argparse
import json
import os
import time
import urllib.request

OUT = os.path.join("research", "clp")
URL = "https://api.oddspapi.io/v4/odds-by-tournaments?apiKey={k}&tournamentIds=36349&bookmaker=bet365"
NAMES = "https://api.oddspapi.io/v4/fixtures?apiKey={k}&tournamentId=36349&from={a}&to={b}"


def key():
    if os.environ.get("ODDSPAPI_KEY"):
        return os.environ["ODDSPAPI_KEY"]
    try:
        for line in open(".env"):
            if line.startswith("ODDSPAPI_KEY="):
                return line.strip().split("=", 1)[1]
    except OSError:
        pass
    raise SystemExit("ODDSPAPI_KEY missing: put it in /etc/pm-us.env or .env")


def snapshot():
    os.makedirs(OUT, exist_ok=True)
    req = urllib.request.Request(URL.format(k=key()), headers={"User-Agent": "trading-lab-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    fp = os.path.join(OUT, f"odds-{int(time.time())}.json")
    open(fp, "wb").write(body)
    d = json.loads(body)
    n = len(d) if isinstance(d, list) else 0
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} saved {n} fixtures -> {fp}", flush=True)


def names_today():
    """Once a day: player names for fixtures today .. +2 days (the bulk odds call has
    none). ~300 KB, one request."""
    os.makedirs(OUT, exist_ok=True)
    a = time.strftime("%Y-%m-%d", time.gmtime())
    fp = os.path.join(OUT, f"names-{a}.json")
    if os.path.exists(fp):
        return
    b = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 2 * 86400))
    req = urllib.request.Request(NAMES.format(k=key(), a=a, b=b), headers={"User-Agent": "trading-lab-research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.loads(r.read())
    json.dump({f["fixtureId"]: [f.get("participant1Name"), f.get("participant2Name")] for f in rows}, open(fp, "w"))
    print(f"names for {len(rows)} fixtures -> {fp}", flush=True)


def fair(fixture):
    """(p1, p2) de-vigged bet365 match-winner probabilities, or None. Market 251:
    outcome 251 = participant 1, 252 = participant 2 (decimal odds)."""
    try:
        o = fixture["bookmakerOdds"]["bet365"]["markets"]["251"]["outcomes"]
        a = o["251"]["players"]["0"]["price"]
        b = o["252"]["players"]["0"]["price"]
    except (KeyError, TypeError):
        return None
    if not a or not b or a <= 1 or b <= 1:
        return None
    ia, ib = 1 / a, 1 / b
    return ia / (ia + ib), ib / (ia + ib)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0, help="hours between snapshots")
    a = ap.parse_args()
    while True:
        try:
            names_today()
            snapshot()
        except Exception as e:
            print(f"snapshot failed: {type(e).__name__} {str(e)[:120]}", flush=True)
        if not a.loop:
            return
        time.sleep(a.loop * 3600)


if __name__ == "__main__":
    main()
