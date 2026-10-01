"""Record Polymarket US and the international Polymarket book on the same games.

    .venv/bin/python hunt/lag_recorder.py --minutes 120          # live + next 2h
    .venv/bin/python hunt/lag_recorder.py --pair-only            # show the pairing

S4 in TEST_PLAN.md. Public endpoints, no orders, no keys. One line per game
per cycle to research/lag/rec-<UTC date>.jsonl:

    us    [bid, bid size, ask, ask size, state] for the US LONG team
    intl  [bid, bid size, ask, ask size] for the SAME team on the international book
    tu, ti  when each answered (fetched back to back)

Pairing: US 'aec-nfl-atl-no-2026-10-05' (ET date) is international
'nfl-atl-no-2026-10-06' (UTC date), so both dates are tried. The match is accepted
only if the international moneyline starts within 90 min of the US game and the
US long team's name is one of its outcomes.
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

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OUT = os.path.join("research", "lag")
LEAGUES = set(core.LEAGUES) | {"lol", "cs2", "dota2", "val", "atp", "wta", "epl", "ucl", "mls"}


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except (AttributeError, ValueError):
        return None


def us_games(now, ahead_h):
    out, i = [], 0
    while True:
        d = get(f"{PM}/markets", {"limit": 500, "offset": i * 500, "active": "true", "closed": "false"}) or {}
        page = d.get("markets", [])
        if not page:
            break
        for m in page:
            p = m.get("slug", "").split("-")
            if m.get("marketType") != "moneyline" or len(p) < 3 or p[0] != "aec" or p[1] not in LEAGUES:
                continue
            st = ts(m.get("gameStartTime") or "")
            if st and now - timedelta(hours=4) <= st <= now + timedelta(hours=ahead_h):
                out.append(m)
        i += 1
        time.sleep(0.25)
    return out


def norm(s):
    return " ".join(core._tokens(s))


def pair(us):
    """International token id for the US LONG team, or None."""
    slug = us["slug"][4:]
    base, date = slug[:-10], slug[-10:]
    st = ts(us["gameStartTime"])
    longs = [s for s in us.get("marketSides") or [] if s.get("long")]
    if not longs or not st:
        return None
    names = {norm(n) for n in core.side_names(longs[0]) if n}
    for d in (date, (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat(),
              (datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()):
        ev = get(f"{GAMMA}/events", {"slug": base + d}) or []
        time.sleep(0.2)
        for e in ev:
            for m in e.get("markets") or []:
                if m.get("sportsMarketType") != "moneyline" or m.get("closed"):
                    continue
                gst = ts(m.get("gameStartTime") or "")
                if not gst or abs((gst - st).total_seconds()) > 90 * 60:
                    continue
                outs = json.loads(m.get("outcomes") or "[]")
                toks = json.loads(m.get("clobTokenIds") or "[]")
                hit = [i for i, o in enumerate(outs) if norm(o) in names]
                if len(outs) == 2 and len(toks) == 2 and len(hit) == 1:
                    return {"intl_slug": m["slug"], "token": toks[hit[0]], "outcome": outs[hit[0]]}
    return None


def intl_quote(book):
    bids = sorted(((float(x["price"]), float(x["size"])) for x in book.get("bids") or []), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in book.get("asks") or [])
    return [bids[0][0] if bids else None, bids[0][1] if bids else 0.0,
            asks[0][0] if asks else None, asks[0][1] if asks else 0.0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--ahead", type=float, default=2.0)
    ap.add_argument("--pair-only", action="store_true")
    ap.add_argument("--repair-every", type=float, default=30.0, help="minutes between re-pairing")
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    t_end = time.time() + a.minutes * 60
    games, t_pair = [], 0.0
    while time.time() < t_end:
        now = datetime.now(timezone.utc)
        if time.time() - t_pair > a.repair_every * 60:
            games = []
            for m in us_games(now, a.ahead):
                p = pair(m)
                if p:
                    games.append({"us_slug": m["slug"], "start": m["gameStartTime"],
                                  "long": [s["description"] for s in m["marketSides"] if s["long"]][0], **p})
            t_pair = time.time()
            print(f"{now.isoformat()[:19]} paired {len(games)} games", flush=True)
            if a.pair_only:
                for g in games:
                    print(f"  {g['us_slug']:<40} {g['long']:<22} -> {g['intl_slug']} [{g['outcome']}]")
                return 0
        live = [g for g in games if ts(g["start"]) - timedelta(hours=a.ahead) <= now]
        if not live:
            time.sleep(30)
            continue
        fp = os.path.join(OUT, f"rec-{now.date().isoformat()}.jsonl")
        with open(fp, "a") as fh:
            for g in live:
                try:
                    ub = get(f"{PM}/markets/{g['us_slug']}/book")
                    tu = time.time()
                    ib = get(f"{CLOB}/book", {"token_id": g["token"]})
                    ti = time.time()
                except Exception as e:
                    print(f"  {g['us_slug']}: {type(e).__name__}", flush=True)
                    continue
                if not ub or not ib:
                    continue
                fh.write(json.dumps({"t": round(tu, 2), "tu": round(tu, 2), "ti": round(ti, 2),
                                     "us_slug": g["us_slug"], "intl_slug": g["intl_slug"],
                                     "start": g["start"], "us": core.pm_quote(ub),
                                     "intl": intl_quote(ib)}) + "\n")
                time.sleep(0.15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
