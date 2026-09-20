"""Live game state for the win-probability layer.

What is actually reachable, tested rather than assumed:

  LoL   WORKS. The esports API lists live matches and their gameIds, and
        feed.lolesports.com serves a livestats "window" carrying totalGold,
        towers, barons, dragons and inhibitors per side, updating in game.
        That is the whole state vector the diffusion model needs.
  Dota  WORKS. api.opendota.com/api/live, unauthenticated.
  CS2   NO free live feed found. HLTV blocks scrapers and the official
        endpoints are partner-gated. The race model still works from a score,
        but the score has to come from somewhere.
  Val   Same. Riot has no public live esports feed.

So the model layer runs on LoL today and Dota if wanted; CS2 and Valorant need
a data deal or a score source before layer 3 applies to them. Their race model
is exact and ready the moment one exists.

The public esports-api key below is the one the lolesports web client ships
with - it is a client-side constant, not a credential.
"""

import time

import requests

LOL_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOL_API = "https://esports-api.lolesports.com/persisted/gw"
LOL_FEED = "https://feed.lolesports.com/livestats/v1"
TIMEOUT = 15


def lol_live():
    """Matches in progress, each with team codes and a gameId when one exists."""
    r = requests.get(f"{LOL_API}/getLive", params={"hl": "en-US"},
                     headers={"x-api-key": LOL_KEY}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for e in (r.json().get("data", {}).get("schedule", {}).get("events") or []):
        m = e.get("match") or {}
        games = [g for g in (m.get("games") or []) if g.get("state") == "inProgress"]
        if not games:
            games = m.get("games") or []
        out.append({
            "state": e.get("state"),
            "league": (e.get("league") or {}).get("name"),
            "teams": [t.get("code") for t in (m.get("teams") or [])],
            "names": [t.get("name") for t in (m.get("teams") or [])],
            "game_id": games[0].get("id") if games else None,
            "best_of": (m.get("strategy") or {}).get("count"),
        })
    return out


def lol_window(game_id):
    """Latest frame of live state, or None. Gold diff is blue minus red."""
    r = requests.get(f"{LOL_FEED}/window/{game_id}", timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    frames = r.json().get("frames") or []
    if not frames:
        return None
    fr = frames[-1]
    blue, red = fr.get("blueTeam") or {}, fr.get("redTeam") or {}

    def side(t):
        return {"gold": t.get("totalGold") or 0, "towers": t.get("towers") or 0,
                "barons": t.get("barons") or 0, "inhibs": t.get("inhibitors") or 0,
                "dragons": len(t.get("dragons") or []),
                "kills": t.get("totalKills") or 0}

    b, rd = side(blue), side(red)
    return {
        "ts": fr.get("rfc460Timestamp"), "state": fr.get("gameState"),
        "blue": b, "red": rd,
        "gold_diff": b["gold"] - rd["gold"],
        "tower_diff": b["towers"] - rd["towers"],
        "dragon_diff": b["dragons"] - rd["dragons"],
        "baron_diff": b["barons"] - rd["barons"],
        "inhib_diff": b["inhibs"] - rd["inhibs"],
    }


def lol_effective_gold(win):
    """One state variable: gold difference with objectives priced into it.

    Keeping objectives as separate regression terms invites overfitting on a
    handful of games. Pricing each as gold-equivalent keeps the model to a
    single diffusion, which is what makes a baron a clean discrete jump rather
    than a coefficient.
    """
    from src.esports.winprob import lol_objective_bump
    return win["gold_diff"] + lol_objective_bump(
        towers=win["tower_diff"], dragons=win["dragon_diff"],
        barons=win["baron_diff"], inhibs=win["inhib_diff"])


def dota_live():
    r = requests.get("https://api.opendota.com/api/live", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def poll(game_id, every=10.0, limit=None):
    """Yield window frames as they update. The generator IS the event stream."""
    seen, n = None, 0
    while limit is None or n < limit:
        w = lol_window(game_id)
        if w and w["ts"] != seen:
            seen = w["ts"]
            n += 1
            yield w
        time.sleep(every)
