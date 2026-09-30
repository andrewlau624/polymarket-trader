"""Record League of Legends markets beside Riot's live feed. Places NO orders.

    python lol_recorder.py --minutes 4.5         # what lol_cycle.sh runs from cron
    python lol_recorder.py --minutes 120 --every 2

Each run: find LoL events the venue lists (from the income bot's listing
cache), pair each with a live Riot match by team code, then every --every
seconds write one line per live event to research/lol_obs.jsonl:

    venue book for every market on the event (bid, size, ask, size, state)
    feed state from team A's side (gold / objective diffs, kills, minute,
      how far the feed trails real time)
    the feed model's P(A wins this game)
    which per-game props the feed has already DECIDED (first blood, kills)
    any series arbitrage net of fees (src/esports/noarb.py)

Exits at once when nothing is live, so a cron line every 5 minutes is cheap.
lol_report.py turns the file into the three answers (see src/esports/lolrec.py).
"""

import argparse
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from src.esports import lolrec as L

OBS = os.path.join("research", "lol_obs.jsonl")
META = os.path.join("research", "lol_meta.json")
LOCK = os.path.join("research", "lol.lock")
INV_CACHE = os.path.join("research", "inventory_cache.json")


def venue_events(slugs, today):
    """{event: {"a", "b", "markets": {role: slug}}} for LoL dated around today."""
    out = {}
    for sl in slugs:
        p = L.parse(sl)
        if not p:
            continue
        event, a, b, role = p
        if event[-10:] not in today:
            continue
        ev = out.setdefault(event, {"a": a, "b": b, "markets": {}})
        ev["markets"][role] = sl
    return out


def listing(c, say):
    """(slugs, quotes) from the income bot's listing cache; quotes carry each
    market's gameStartTime, which pairing uses."""
    try:
        with open(INV_CACHE) as fh:
            d = json.load(fh)
        return set(d["slugs"]), d.get("quotes") or {}
    except (OSError, ValueError, KeyError):
        pass
    from income_bot import all_slugs                 # no cache yet: build one
    return all_slugs(c, say), getattr(c, "_quotes", {}) or {}


def names_b_side(c, slug, a_name, b_name, meta):
    """Does this game market pay on team B? The venue writes 'Will <team> win
    Game N'; everything so far has named team A, but a flipped market would
    corrupt every number downstream, so read the question once and remember."""
    if slug in meta:
        return meta[slug]
    try:
        q = (c.c.markets.retrieve_by_slug(slug).get("market") or {}).get("question") or ""
    except Exception:
        return None
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    head = norm(q.split(" win ")[0])
    flip = None
    if norm(a_name) and norm(a_name) in head:
        flip = False
    elif norm(b_name) and norm(b_name) in head:
        flip = True
    meta[slug] = flip
    return flip


def quote(c, slug):
    try:
        bids, asks, state = c.book_levels(slug)
    except Exception:
        return None
    return [bids[0][0] if bids else None, bids[0][1] if bids else 0,
            asks[0][0] if asks else None, asks[0][1] if asks else 0, state]


def flipped(q):
    """A market on B, restated on A: bid_A = 1 - ask_B, ask_A = 1 - bid_B."""
    bid, bsz, ask, asz, state = q
    return [None if ask is None else round(1 - ask, 4), asz,
            None if bid is None else round(1 - bid, 4), bsz, state]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=4.5)
    ap.add_argument("--every", type=float, default=3.0)
    ap.add_argument("--out", default=OBS)
    a = ap.parse_args(argv)
    t_end = time.time() + a.minutes * 60
    os.makedirs("research", exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    say = lambda *x: print(*x, flush=True)

    from src.pm_us.client import UsClient
    from src.pm_us.jsonlog import append
    c = UsClient()
    now = datetime.now(timezone.utc)
    days = {now.date().isoformat(),
            datetime.fromtimestamp(time.time() - 86400, timezone.utc).date().isoformat()}
    slugs, quotes = listing(c, say)
    events = venue_events(slugs, days)
    stamp = f"lol_recorder | {now.isoformat()[:19]} |"
    if not events:
        say(f"{stamp} venue lists no LoL match today")     # one line per run: cron is alive
        return 0
    sched = L.scheduled_matches(now)
    if not sched:
        say(f"{stamp} venue LoL events {len(events)}, nothing on Riot's schedule now")
        return 0
    details = []
    for m in sched:
        try:
            details.append({**L.match_detail(m["match_id"]), "start": m["start"]})
        except Exception:
            continue
    try:
        meta = json.load(open(META))
    except (OSError, ValueError):
        meta = {}

    tracked, unpaired, idle = {}, [], []
    for ev, v in events.items():
        start = (quotes.get(v["markets"].get(("match",), "")) or [None, None])[1]
        pair = L.pair_event(v["a"], v["b"], details, start=start)
        if not pair:
            unpaired.append(ev)
            continue
        g, s0 = L.live_game(pair[0])
        if not g:
            idle.append(ev)
            continue
        tracked[ev] = {**v, "detail": pair[0], "A": pair[1], "B": pair[2],
                       "game": g, "start": {g["id"]: s0}, "fb": {}}
    say(f"{stamp} venue LoL events {len(events)}, on Riot's schedule {len(details)}, "
        f"paired+live {len(tracked)}, paired not live {len(idle)}, unpaired {len(unpaired)}"
        + (f" | recording: {', '.join(tracked)}" if tracked else "")
        + (f" | unpaired: {', '.join(unpaired[:6])}" if unpaired else ""))
    if not tracked:
        return 0

    polls = 0
    while time.time() < t_end:
        t0 = time.time()
        for ev, T in tracked.items():
            if polls and polls % 10 == 0:        # series score moves once a game
                try:
                    d = {**L.match_detail(T["detail"]["match_id"]),
                         "start": T["detail"].get("start")}
                    T["detail"] = d
                    T["A"] = next(t for t in d["teams"] if t["id"] == T["A"]["id"])
                    T["B"] = next(t for t in d["teams"] if t["id"] == T["B"]["id"])
                    g2, s2 = L.live_game(d)       # the feed says which game is on
                    if g2:
                        T["game"] = g2
                        T["start"].setdefault(g2["id"], s2)
                except Exception:
                    pass
            wa, wb = T["A"]["wins"], T["B"]["wins"]
            g = T["game"]
            game_n = g["n"] or wa + wb + 1
            # Riot's series score lags or never updates for some leagues; the
            # arb needs it right, so it only runs when it agrees with the game number
            wins_ok = wa + wb == game_n - 1
            st, mp = None, None
            if g:
                if g["id"] not in T["start"]:
                    T["start"][g["id"]] = L.first_ts(g["id"])
                fr, gm = L.latest_frame(g["id"])
                if fr:
                    st = L.state_for(fr, gm, T["A"]["id"], T["start"][g["id"]])
                    mp = L.model_p(st)
                    if g["n"] not in T["fb"] and sum(st["kills"]) > 0:
                        # the first frame we see with kills: exact only if we saw 0-0
                        T["fb"][g["n"]] = (T["A"]["code"] if st["kills"][0] > st["kills"][1]
                                           else T["B"]["code"]) \
                            if 0 in st["kills"] and T.get("saw_zero") == g["n"] else "?"
                    if sum(st["kills"]) == 0:
                        T["saw_zero"] = g["n"]
            q, dec = {}, {}
            for role, slug in T["markets"].items():
                if role[0] in ("map", "fb", "kills", "odd") and role[1] < game_n:
                    continue                      # that game is over
                x = quote(c, slug)
                if x is None:
                    continue
                if role[0] == "map" and names_b_side(c, slug, T["A"]["name"],
                                                     T["B"]["name"], meta):
                    x = flipped(x)
                q[L.role_key(role)] = x
                if st and role[0] in ("fb", "kills") and role[1] == game_n:
                    fb = T["fb"].get(game_n)
                    v = L.decided(role, st, T["A"]["code"], None if fb == "?" else fb)
                    if v is not None:
                        dec[L.role_key(role)] = v
            roles = [r for r in T["markets"] if r[0] in ("match", "map", "over", "hcap")
                     and L.role_key(r) in q and not (r[0] == "map" and r[1] < game_n)]
            res = None
            if roles and wins_ok and T["detail"].get("best_of") in (3, 5):
                rq = [tuple(q[L.role_key(r)][:4]) for r in roles
                      if q[L.role_key(r)][4] in (None, "MARKET_STATE_OPEN")]
                rr = [r for r in roles if q[L.role_key(r)][4] in (None, "MARKET_STATE_OPEN")]
                if rr:
                    try:
                        res = L.arb(L.series_roles(rr), rq, T["detail"]["best_of"], wa, wb)
                    except Exception:
                        res = None
            append(a.out, {"ts": datetime.now(timezone.utc).isoformat(), "event": ev,
                           "league": T["detail"].get("league"),
                           "match_id": T["detail"].get("match_id"),
                           "best_of": T["detail"].get("best_of"), "wins": [wa, wb],
                           "game": game_n, "A": T["A"]["code"], "feed": st,
                           "model_p": mp, "fb": T["fb"].get(game_n), "q": q,
                           "decided": dec,
                           "arb": None if not res else {"net": res["profit_net"],
                                                        "legs": res["legs"]}})
        polls += 1
        json.dump(meta, open(META, "w"))
        time.sleep(max(0.0, a.every - (time.time() - t0)))
    say(f"  {polls} polls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
