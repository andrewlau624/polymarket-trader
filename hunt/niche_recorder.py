"""Record Polymarket US books on niche, US-only match markets. Places NO orders.

    .venv/bin/python hunt/niche_recorder.py --minutes 600

Table tennis (Setka Cup UA/CZ/MD, Czech Liga Pro, TT Elite, TT Cup, WTT) and
minor tennis (ITF, UTR) trade only on Polymarket US, so the international
dump cannot price them. Every --every seconds, for each moneyline starting
within --ahead hours (or started < 3 h ago), one line to
research/niche/rec-<UTC date>.jsonl:

    slug, start, long/short names, [bid, bid size, ask, ask size, state]

Results come later from the closed listing (research/us_closed.jsonl), where a
settled side carries price "1".
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from src.xvenue import core                                        # noqa: E402
from xvenue_recorder import PM, get                                # noqa: E402

NICHE = {"setkameua", "setkamecz", "setkamemd", "czechligapro", "ttelite", "ttcup", "wtt",
         "itfme", "itfwo", "itfm", "itfw", "utr", "tsc"}
OUT = os.path.join("research", "niche")


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def listing():
    out, i = [], 0
    while True:
        d = get(f"{PM}/markets", {"limit": 500, "offset": i * 500, "active": "true", "closed": "false"}) or {}
        page = d.get("markets", [])
        if not page:
            break
        out += [m for m in page if m.get("marketType") == "moneyline"
                and m.get("slug", "").split("-")[1:2] and m["slug"].split("-")[1] in NICHE]
        i += 1
        time.sleep(0.25)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=600)
    ap.add_argument("--every", type=float, default=300)
    ap.add_argument("--ahead", type=float, default=3.0)
    ap.add_argument("--relist", type=float, default=30.0, help="minutes between listing refreshes")
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    t_end, mk, t_list = time.time() + a.minutes * 60, [], 0.0
    while time.time() < t_end:
        t0 = time.time()
        now = datetime.now(timezone.utc)
        if t0 - t_list > a.relist * 60:
            mk = listing()
            t_list = time.time()
            print(f"{now.isoformat()[:19]} {len(mk)} niche moneylines listed", flush=True)
        todo = [m for m in mk if (st := ts(m.get("gameStartTime") or ""))
                and now - timedelta(hours=3) <= st <= now + timedelta(hours=a.ahead)]
        fp = os.path.join(OUT, f"rec-{now.date().isoformat()}.jsonl")
        with open(fp, "a") as fh:
            for m in todo:
                b = get(f"{PM}/markets/{m['slug']}/book")
                if not b:
                    continue
                sides = m.get("marketSides") or []
                fh.write(json.dumps({
                    "t": round(time.time(), 1), "slug": m["slug"], "start": m.get("gameStartTime"),
                    "long": next((s.get("description") for s in sides if s.get("long")), None),
                    "short": next((s.get("description") for s in sides if not s.get("long")), None),
                    "q": core.pm_quote(b)}) + "\n")
                time.sleep(0.25)
        print(f"  {now.isoformat()[11:19]} {len(todo)} books", flush=True)
        time.sleep(max(0.0, a.every - (time.time() - t0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
