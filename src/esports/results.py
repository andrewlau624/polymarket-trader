"""Who won each recorded LoL game, from Riot rather than the market's last price.

The recorder often stops before a game market settles, so reading the winner
off the final price left 32 of 55 games unknown. Two sources fix that:

  * between games of a series, the recorder's own 'wins' field moves: whoever's
    count went up won the game that just ended
  * the series' last game (and every best-of-1) is won by the team Riot's
    schedule marks outcome 'win' for the match

Results are cached in research/lol_results.json; completed matches never change.
"""

import json
import os
from datetime import datetime, timedelta

from src.esports import lolrec as L

CACHE = os.path.join("research", "lol_results.json")


def completed(pages=6, path=CACHE):
    """[{start, teams: [{code, wins, won}]}] for finished matches, newest pages
    first, walking `pages` pages back. Cached by start+codes."""
    try:
        have = json.load(open(path))
    except (OSError, ValueError):
        have = {}
    token = None
    for _ in range(pages):
        d = L._get("getSchedule", **({"pageToken": token} if token else {}))
        sch = d.get("schedule") or {}
        for e in sch.get("events") or []:
            m = e.get("match") or {}
            ts = m.get("teams") or []
            if e.get("state") != "completed" or len(ts) != 2:
                continue
            key = f"{e.get('startTime')}|{'-'.join((t.get('code') or '').lower() for t in ts)}"
            have[key] = {"start": e.get("startTime"),
                         "teams": [{"code": (t.get("code") or "").lower(),
                                    "wins": (t.get("result") or {}).get("gameWins") or 0,
                                    "won": (t.get("result") or {}).get("outcome") == "win"}
                                   for t in ts]}
        token = (sch.get("pages") or {}).get("older")
        if not token:
            break
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(have, open(path, "w"))
    return list(have.values())


def _as_details(matches):
    return [{"start": m["start"],
             "teams": [{"id": t["code"], "code": t["code"], "name": t["code"],
                        "wins": t["wins"], "won": t["won"]} for t in m["teams"]]}
            for m in matches]


def winners(recs, matches):
    """{'<event>#<game>': +1 if team A won that game, -1 if team B}."""
    by_event = {}
    for r in recs:
        by_event.setdefault(r["event"], []).append(r)
    details = _as_details(matches)
    out = {}
    for ev, rs in by_event.items():
        rs.sort(key=lambda r: r["ts"])
        # 1. between games: the wins counter moved
        last = None
        for r in rs:
            w = r.get("wins")
            if not w:
                continue
            if last and sum(w) == sum(last) + 1:
                g = sum(last) + 1
                out[f"{ev}#{g}"] = 1 if w[0] > last[0] else -1
            last = w
        # 2. the last game: the match winner won it
        p = L.parse("aec-" + ev)
        if not p:
            continue
        _e, a, b, _role = p
        day = datetime.fromisoformat(ev[-10:])
        near = [d for d in details
                if abs((datetime.fromisoformat(d["start"][:10]) - day).days) <= 1]
        pair = L.pair_event(a, b, near)
        if not pair:
            continue
        _d, ta, tb = pair
        played = ta["wins"] + tb["wins"]
        if played and (ta["won"] or tb["won"]):
            out.setdefault(f"{ev}#{played}", 1 if ta["won"] else -1)
    return out
