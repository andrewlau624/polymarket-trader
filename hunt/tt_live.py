"""T3 recorder: live table-tennis score and Polymarket US book, back to back. No orders.

    .venv/bin/python hunt/tt_live.py --minutes 600

Every cycle: list live table-tennis events (period S1..S5), then for each one
fetch /v1/events/{id} (score 'a-b, a-b, ...' game by game, period) and the
moneyline book. One line per event per cycle to research/ttlive/rec-<UTC date>.jsonl:

    t, event id, slug (market), period, score, team ids in SCORE order,
    long team id (the side the book is quoted in), te (score time), tb (book time),
    q [bid, bid size, ask, ask size, state]
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, ".")
from src.xvenue import core                                        # noqa: E402
from xvenue_recorder import PM, get                                # noqa: E402

G = "https://gateway.polymarket.us/v1"
OUT = os.path.join("research", "ttlive")


def live_events():
    d = get(f"{G}/events", {"limit": 200, "active": "true", "closed": "false", "tag_slug": "table-tennis"}) or []
    ev = d.get("events", d) if isinstance(d, dict) else d
    return [e for e in ev if str(e.get("period") or "").startswith("S")]


def moneyline(e):
    for m in e.get("markets") or []:
        if m.get("marketType") == "moneyline" or m.get("sportsMarketType", "").endswith("match_winner"):
            return m
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=600)
    ap.add_argument("--pause", type=float, default=0.15)
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    t_end, cycles = time.time() + a.minutes * 60, 0
    while time.time() < t_end:
        try:
            evs = live_events()
        except Exception as ex:
            print(f"list failed {type(ex).__name__}", flush=True)
            time.sleep(5)
            continue
        if not evs:
            time.sleep(10)
            continue
        fp = os.path.join(OUT, f"rec-{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl")
        with open(fp, "a") as fh:
            for e in evs:
                try:
                    d = get(f"{G}/events/{e['id']}") or {}
                    te = time.time()
                    d = d.get("event", d)
                    m = moneyline(d) or moneyline(e)
                    if not m:
                        continue
                    b = get(f"{PM}/markets/{m['slug']}/book")
                    tb = time.time()
                except Exception as ex:
                    print(f"  {e.get('slug')}: {type(ex).__name__}", flush=True)
                    continue
                st = d.get("eventState") or {}
                sides = m.get("marketSides") or []
                long_id = next(((s.get("team") or {}).get("id") for s in sides if s.get("long")), None)
                fh.write(json.dumps({
                    "t": round(te, 2), "te": round(te, 2), "tb": round(tb, 2), "id": e["id"],
                    "slug": m["slug"], "start": m.get("gameStartTime"),
                    "period": st.get("period") or d.get("period"), "score": st.get("score") or d.get("score"),
                    "live": st.get("live"), "teams": [t.get("id") for t in d.get("teams") or []],
                    "names": [t.get("name") for t in d.get("teams") or []], "long_id": long_id,
                    "q": core.pm_quote(b) if b else None}) + "\n")
                time.sleep(a.pause)
        cycles += 1
        if cycles % 50 == 0:
            print(f"{time.strftime('%H:%M:%S', time.gmtime())} cycle {cycles}, {len(evs)} live", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
