"""T5: snapshot bet365 odds for Czech Liga Pro via OddsPapi (8 requests/day budget).

    .venv/bin/python hunt/clp_odds.py            # one snapshot -> research/clp/odds-<ts>.json
    .venv/bin/python hunt/clp_odds.py --loop 3   # every 3 h, forever

OddsPapi's free tier allows ~250 requests a month. One bulk call returns every
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


def key():
    for line in open(".env"):
        if line.startswith("ODDSPAPI_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("ODDSPAPI_KEY missing from .env")


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
            snapshot()
        except Exception as e:
            print(f"snapshot failed: {type(e).__name__} {str(e)[:120]}", flush=True)
        if not a.loop:
            return
        time.sleep(a.loop * 3600)


if __name__ == "__main__":
    main()
