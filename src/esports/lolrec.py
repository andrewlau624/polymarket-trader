"""League of Legends on Polymarket US: what the recorder needs to know.

Three questions decide whether a LoL swing bot can work, and all three need
the venue's book and Riot's live feed captured side by side, every few seconds:

  1. LATENCY. First blood and "game N total kills over 24.5" are DECIDED by
     the feed the moment it shows the kill. How long does the venue keep
     selling the winning side cheap afterwards? That is a speed edge with
     no model in it, and the cleanest test of whether the venue lags.
  2. LEAD-LAG. Does the game market move AFTER the feed's gold/objective
     swings (tradeable) or with them (not)?
  3. STRUCTURE. Game-N, match, total-games and handicap markets on one series
     must be coherent (src/esports/noarb.py). How often, net of fees, are they not?

Slugs seen on the venue (A, B = team codes; A is the long side of the match):

  aec-lol-A-B-DATE                   match winner (A)
  astatc-lol-A-B-DATE-game2          A wins game 2
  tsc-lol-A-B-DATE-tot-2pt5          more than 2.5 games
  asc-lol-A-B-DATE-hcap-neg-1pt5     A covers -1.5 games
  astatc-lol-A-B-DATE-g1fb-fly       team `fly` draws first blood in game 1
  tsc-lol-A-B-DATE-g1tk-24pt5        game 1 total kills over 24.5
  astatc-lol-A-B-DATE-g1oe           game 1 total kills odd (recorded, not used)
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests

from src.esports.feeds import LOL_API, LOL_FEED, LOL_KEY, TIMEOUT

_BASE = re.compile(r"^(?P<pre>[a-z]+)-lol-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
                   r"(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<rest>.+))?$")


def _num(s):
    return float(s.replace("pt", "."))


def parse(slug):
    """(event, a, b, role) or None. role is a tuple, e.g. ('map', 2)."""
    m = _BASE.match(slug or "")
    if not m:
        return None
    pre, a, b, rest = m.group("pre"), m.group("a"), m.group("b"), m.group("rest") or ""
    event = f"lol-{a}-{b}-{m.group('date')}"
    role = None
    if pre == "aec" and not rest:
        role = ("match",)
    elif (g := re.fullmatch(r"game(\d)", rest)):
        role = ("map", int(g.group(1)))
    elif (g := re.fullmatch(r"tot-(\d+pt\d)", rest)):
        role = ("over", _num(g.group(1)))
    elif (g := re.fullmatch(r"hcap-(neg|pos)-(\d+pt\d)", rest)):
        role = ("hcap", (-1 if g.group(1) == "neg" else 1) * _num(g.group(2)))
    elif (g := re.fullmatch(r"g(\d)fb-([a-z0-9]+)", rest)):
        role = ("fb", int(g.group(1)), g.group(2))
    elif (g := re.fullmatch(r"g(\d)tk-(\d+pt\d)", rest)):
        role = ("kills", int(g.group(1)), _num(g.group(2)))
    elif (g := re.fullmatch(r"g(\d)oe", rest)):
        role = ("odd", int(g.group(1)))
    if role is None:
        return None
    return event, a, b, role


def role_key(role):
    return ":".join(str(x) for x in role)


def series_roles(markets):
    """The roles noarb.py can price: [(kind, arg)] for its LP."""
    out = []
    for role in markets:
        if role[0] == "match":
            out.append(("match", None))
        elif role[0] in ("map", "over", "hcap"):
            out.append((role[0], role[1]))
    return out


# ---- Riot's side ------------------------------------------------------------

def _get(path, **params):
    r = requests.get(f"{LOL_API}/{path}", params={"hl": "en-US", **params},
                     headers={"x-api-key": LOL_KEY}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("data") or {}


def live_matches():
    """[{match_id, league, best_of, teams: [{id, code, name, wins}], game}]"""
    out = []
    for e in (_get("getLive").get("schedule", {}).get("events") or []):
        m = e.get("match") or {}
        if not m.get("id") or len(m.get("teams") or []) != 2:
            continue
        out.append({"match_id": m["id"], "league": (e.get("league") or {}).get("name"),
                    "best_of": (m.get("strategy") or {}).get("count")})
    return out


def match_detail(match_id):
    ev = _get("getEventDetails", id=match_id).get("event") or {}
    m = ev.get("match") or {}
    teams = [{"id": t.get("id"), "code": (t.get("code") or "").lower(),
              "name": t.get("name"), "wins": (t.get("result") or {}).get("gameWins") or 0}
             for t in m.get("teams") or []]
    games = [{"n": g.get("number"), "id": g.get("id"), "state": g.get("state"),
              "sides": {t.get("id"): t.get("side") for t in g.get("teams") or []}}
             for g in m.get("games") or []]
    return {"match_id": match_id, "league": (ev.get("league") or {}).get("name"),
            "best_of": (m.get("strategy") or {}).get("count"), "teams": teams, "games": games}


def pair_event(a, b, details):
    """The live Riot match whose team codes are the slug's (a, b), with A first."""
    for d in details:
        codes = [t["code"] for t in d["teams"]]
        if a in codes and b in codes:
            ta = next(t for t in d["teams"] if t["code"] == a)
            tb = next(t for t in d["teams"] if t["code"] == b)
            return d, ta, tb
    return None


def _window(game_id, starting=None):
    params = {"startingTime": starting} if starting else None
    r = requests.get(f"{LOL_FEED}/window/{game_id}", params=params, timeout=TIMEOUT)
    if r.status_code != 200 or not r.content:
        return None
    return r.json()


def first_ts(game_id):
    """When the game started: the first frame's timestamp (no startingTime
    returns the opening window of the game)."""
    w = _window(game_id)
    fr = (w or {}).get("frames") or []
    return fr[0]["rfc460Timestamp"] if fr else None


def latest_frame(game_id, now=None):
    """The newest frame the feed will serve. It trails real time by tens of
    seconds (the broadcast delay), so walk back until a window answers."""
    now = now or datetime.now(timezone.utc)
    for back in (10, 20, 30, 45, 60, 90, 150):
        t = now - timedelta(seconds=back)
        t = t.replace(second=t.second - t.second % 10, microsecond=0)
        w = _window(game_id, t.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        fr = (w or {}).get("frames") or []
        if fr:
            return fr[-1], (w.get("gameMetadata") or {})
    return None, None


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def state_for(frame, meta, a_id, start_ts=None, now=None):
    """Feed state from team A's side: gold/objective diffs, kills, minute."""
    blue_id = (meta.get("blueTeamMetadata") or {}).get("esportsTeamId")
    a_blue = blue_id == a_id
    A = frame["blueTeam"] if a_blue else frame["redTeam"]
    B = frame["redTeam"] if a_blue else frame["blueTeam"]

    def n(t, k):
        v = t.get(k) or 0
        return len(v) if isinstance(v, list) else v
    ft = _ts(frame.get("rfc460Timestamp"))
    now = now or datetime.now(timezone.utc)
    st = _ts(start_ts)
    return {"ts": frame.get("rfc460Timestamp"), "state": frame.get("gameState"),
            "delay_s": round((now - ft).total_seconds(), 1) if ft else None,
            "minute": round((ft - st).total_seconds() / 60, 2) if ft and st else None,
            "gold": n(A, "totalGold") - n(B, "totalGold"),
            "kills": [n(A, "totalKills"), n(B, "totalKills")],
            "towers": n(A, "towers") - n(B, "towers"),
            "dragons": n(A, "dragons") - n(B, "dragons"),
            "barons": n(A, "barons") - n(B, "barons"),
            "inhibs": n(A, "inhibitors") - n(B, "inhibitors")}


def model_p(st):
    """P(A wins this game) from the feed: effective gold as a digital option."""
    from src.esports.winprob import lol_objective_bump, lol_win_prob
    if st.get("minute") is None:
        return None
    eff = st["gold"] + lol_objective_bump(st["towers"], st["dragons"], st["barons"],
                                          st["inhibs"])
    return round(lol_win_prob(eff, st["minute"]), 4)


def decided(role, st, a_code, fb_team):
    """What the feed says a per-game prop pays, if it is already decided.
    fb_team: code of the team that drew first blood this game, or None."""
    if role[0] == "fb" and fb_team:
        return 1.0 if role[2] == fb_team else 0.0
    if role[0] == "kills":
        total = sum(st["kills"])
        if total > role[2]:
            return 1.0                   # over is locked in once passed
        if st.get("state") == "finished":
            return 0.0
    return None


def arb(roles, quotes, best_of, wins_a, wins_b, fee=None):
    """noarb.py over the series markets, net of taker fees. None if coherent."""
    try:
        import scipy  # noqa: F401  (noarb raises SystemExit without it, which
    except ImportError:  # no `except Exception` catches - it would kill the recorder)
        return None
    from src.esports.noarb import find_arbitrage
    from src.pm_us.fees import taker_fee
    fee = fee or taker_fee
    known = "A" * wins_a + "B" * wins_b     # order is irrelevant once decided
    res = find_arbitrage(roles, quotes, best_of=best_of, known=known, unit_cap=50)
    if not res:
        return None
    cost = sum(units * fee(px) for _s, _k, _a, units, px in res["legs"])
    res["profit_net"] = round(res["profit"] - cost, 4)
    return res if res["profit_net"] > 0 else None


def now_s():
    return time.time()
