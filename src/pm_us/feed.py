"""ESPN as a free, keyless event feed for the markets the US venue lists.

Two things come out of the same public endpoints:

  * plays, each with an ISO `wallclock` - the ground truth for "when did the
    world learn this", which is what makes repricing lag measurable
  * ESPN's own live win probability, keyed by play id - an independent model
    to diff the market against

Nothing here is authenticated. Be polite with the poll interval.
"""

import re
from datetime import datetime, timezone

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports"
TIMEOUT = 15

# Polymarket US slugs look like aec-cfb-ill-ohiost-2026-09-26
SPORT_PATHS = {
    "cfb": "football/college-football",
    "nfl": "football/nfl",
    "ufc": "mma/ufc",
    "nba": "basketball/nba",
    "cbb": "basketball/mens-college-basketball",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
}
_SLUG = re.compile(r"^aec-(?P<sport>[a-z0-9]+)-(?P<teams>.+)-(?P<date>\d{4}-\d{2}-\d{2})$")


def parse_slug(slug):
    """('cfb', ['ill','ohiost'], '2026-09-26') from a US market slug, else None."""
    m = _SLUG.match(slug or "")
    if not m:
        return None
    teams = [t for t in m.group("teams").split("-") if t]
    return m.group("sport"), teams, m.group("date")


def scoreboard(sport_path, date=None):
    """Games for a date (YYYYMMDD); today's slate when date is None."""
    params = {"dates": date} if date else None
    r = requests.get(f"{SITE}/{sport_path}/scoreboard", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for ev in r.json().get("events", []):
        comps = ev.get("competitions") or [{}]
        teams = []
        for c in comps[0].get("competitors", []):
            t = c.get("team") or {}
            teams.append({
                "home_away": c.get("homeAway"),
                "abbrev": t.get("abbreviation") or "",
                "location": t.get("location") or "",
                "name": t.get("name") or "",
                "display": t.get("displayName") or "",
                "score": c.get("score"),
            })
        out.append({
            "event_id": ev.get("id"),
            "short": ev.get("shortName"),
            "date": ev.get("date"),
            "state": (ev.get("status") or {}).get("type", {}).get("state"),
            "teams": teams,
        })
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _subseq(short, long):
    """Is `short` a subsequence of `long`? 'clmsn' <= 'clemson' -> True."""
    it = iter(long)
    return all(ch in it for ch in short)


def _team_score(token, team):
    """How well a slug token identifies one competitor. Higher is better, 0 = no."""
    tok = _norm(token)
    if not tok:
        return 0
    cands = [_norm(team.get(k)) for k in ("abbrev", "location", "name", "display")]
    cands = [c for c in cands if c]
    if tok in cands:
        return 4
    if any(c.startswith(tok) for c in cands):
        return 3
    # squeezed spellings: 'ohiost' -> 'ohiostate', 'clmsn' -> 'clemson'
    if any(_subseq(tok, c) for c in cands):
        return 2 if len(tok) >= 4 else 1
    return 0


def match_game(slug, games):
    """Best ESPN game for a US market slug, or None when it is ambiguous.

    Requires the two slug tokens to identify two DIFFERENT competitors, which
    is what stops 'ill' matching both sides of an Illinois game.
    """
    parsed = parse_slug(slug)
    if not parsed:
        return None
    _sport, tokens, date = parsed
    best, best_score = None, 0
    for g in games:
        if g.get("date") and not str(g["date"]).startswith(date):
            continue
        teams = g.get("teams") or []
        if len(teams) < 2:
            continue
        # try both orderings, require distinct competitors
        pairings = []
        for i, a in enumerate(teams):
            for j, b in enumerate(teams):
                if i == j:
                    continue
                sa = max(_team_score(t, a) for t in tokens[:1] or [""])
                sb = max(_team_score(t, b) for t in tokens[1:2] or [""])
                if sa and sb:
                    pairings.append(sa + sb)
        if not pairings:
            continue
        sc = max(pairings)
        if sc > best_score:
            best, best_score = g, sc
    # 4 = both sides matched at least on a squeezed spelling
    return best if best_score >= 4 else None


def plays(event_id, sport_path):
    """Every play with a wallclock, newest last, with ESPN win prob joined on."""
    r = requests.get(f"{SITE}/{sport_path}/summary",
                     params={"event": event_id}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    wp = {p.get("playId"): p.get("homeWinPercentage")
          for p in (d.get("winprobability") or []) if p.get("playId")}
    out = []
    drives = d.get("drives") or {}
    buckets = list(drives.get("previous") or [])
    if drives.get("current"):
        buckets.append(drives["current"])
    for dr in buckets:
        for p in (dr.get("plays") or []):
            if not p.get("wallclock"):
                continue
            pid = str(p.get("id") or "")
            out.append({
                "play_id": pid,
                "wallclock": p["wallclock"],
                "scoring": bool(p.get("scoringPlay")),
                "away_score": p.get("awayScore"),
                "home_score": p.get("homeScore"),
                "clock": ((p.get("clock") or {}).get("displayValue")),
                "text": (p.get("text") or "")[:220],
                "home_wp": wp.get(pid),
            })
    out.sort(key=lambda r: r["wallclock"])
    return out


def iso_to_epoch(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()
