"""Record Polymarket US table-tennis events -> Sportradar id, market slug, players.

    .venv/bin/python hunt/clp_map.py --loop 20      # minutes between passes

Polymarket US events carry sportradarGameId; OddsPapi fixture ids are the same
Sportradar ids ('id2503634975166720'). Events drop out of the listing once they
end, so the mapping is saved as it is seen.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, ".")
from xvenue_recorder import get                                   # noqa: E402

OUT = os.path.join("research", "clp", "map.jsonl")


def one_pass():
    d = get("https://gateway.polymarket.us/v1/events",
            {"limit": 200, "active": "true", "closed": "false", "tag_slug": "table-tennis"}) or []
    ev = d.get("events", d) if isinstance(d, dict) else d
    n = 0
    with open(OUT, "a") as fh:
        for e in ev:
            sr = e.get("sportradarGameId")
            ml = [m for m in e.get("markets") or [] if m.get("marketType") == "moneyline"]
            if not sr or not ml:
                continue
            m = ml[0]
            fh.write(json.dumps({"t": time.time(), "sr": sr, "slug": m["slug"], "start": m.get("gameStartTime"),
                                 "sides": [{"long": s.get("long"), "name": (s.get("team") or {}).get("name")
                                            or s.get("description")} for s in m.get("marketSides") or []]}) + "\n")
            n += 1
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} mapped {n} events", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0)
    a = ap.parse_args()
    while True:
        try:
            one_pass()
        except Exception as e:
            print(f"pass failed {type(e).__name__}", flush=True)
        if not a.loop:
            return
        time.sleep(a.loop * 60)


if __name__ == "__main__":
    main()
