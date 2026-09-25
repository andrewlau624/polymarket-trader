"""The external anchor: ESPN's published sportsbook line for a ladder's game.

A sharp-ish sportsbook line is the best free predictor of the margin, and it
cannot be contaminated by the thin ladder it is judging. Spread gives the
mean; the de-vigged moneyline pins sigma (src/edge/bookline.implied_margin).

The same lookup also answers two things the bot needs and never had:
  * game state (pre / in / post) - value bets and MM quotes stop at kickoff,
    and the last pre-game fair is frozen as the CLOSING line for CLV
  * the final margin, so the bot settles its own books instead of guessing

Sign: the ladder resolves on the FIRST slug token's team (venue rules text).
If that side cannot be identified the game is refused, not guessed - a
flipped sign is what produced nine fake arbitrages early in this project.
"""

import time

from src.income.model import MarginModel
from src.pm_us.feed import SPORT_PATHS, _team_score, match_game, parse_slug, scoreboard


class LineSource:
    """max_age: seconds a scoreboard stays fresh. A one-shot cron run can
    cache for its whole life; the in-play loop needs the score and clock
    re-read every poll, so it passes a few seconds."""

    def __init__(self, pause=0.25, log=print, max_age=1e9):
        self.boards, self.lines, self.pause, self.say = {}, {}, pause, log
        self.max_age = max_age
        self.pre_lines = {}          # event_id -> (spread, p_home, p_away, prov)

    def _board(self, path, date):
        key = (path, date)
        hit = self.boards.get(key)
        if hit is None or time.time() - hit[0] > self.max_age:
            try:
                hit = (time.time(), scoreboard(path, date=date))
            except Exception:
                hit = (time.time(), hit[1] if hit else [])
            self.boards[key] = hit
            time.sleep(self.pause)
        return hit[1]

    def game(self, base, need_line=True):
        """dict(model, state, margin, provider, ...) or None.

        need_line=False answers from the scoreboard alone (state, score,
        clock). Finding the live games among ~1,000 ladders must not fetch a
        pre-game line for every one of them - that alone would outlast the
        5-minute in-play window.
        """
        hit = self.lines.get(base)
        if hit is not None and time.time() - hit[0] <= self.max_age:
            out = hit[1]
        else:
            out = self._lookup(base)
            self.lines[base] = (time.time(), out)
        if need_line and out and out["state"] in ("pre", "in") and "_line" not in out:
            self._attach_line(out)
        return out

    def _attach_line(self, out):
        """The PRE-game line, fetched once per event: in-play it is the prior
        the live model starts from, not a live price."""
        out["_line"] = True
        eid = out["event_id"]
        if eid not in self.pre_lines:
            from run_bookline import espn_line
            self.pre_lines[eid] = espn_line(eid, out["_path"])
            time.sleep(self.pause)
        spread, p_home, p_away, prov = self.pre_lines[eid]
        if spread is None:
            return
        sport = out["league"]
        p_ref = p_home if out["ref_is_home"] else p_away
        out["model"] = MarginModel.from_line(spread, p_ref, out["ref_is_home"],
                                             league=sport if sport in ("cfb", "nfl") else "cfb")
        out["provider"] = prov
        out["spread"] = spread

    def _lookup(self, base):
        slug = base.replace("asc-", "aec-", 1)
        parsed = parse_slug(slug)
        if not parsed:
            return None                         # sub-period and odd slugs
        sport, tokens, date = parsed
        path = SPORT_PATHS.get(sport)
        if not path or not tokens:
            return None
        # ESPN dates games in UTC; a prime-time kickoff is the next UTC day
        from datetime import date as _d, timedelta
        y, m, d = (int(x) for x in date.split("-"))
        games = []
        for dd in (_d(y, m, d), _d(y, m, d) + timedelta(days=1)):
            games += self._board(path, dd.strftime("%Y%m%d"))
        g = match_game(slug, games)
        if not g:
            return None
        home = next((t for t in g["teams"] if t["home_away"] == "home"), {})
        away = next((t for t in g["teams"] if t["home_away"] == "away"), {})
        sh, sa = _team_score(tokens[0], home), _team_score(tokens[0], away)
        if sh == sa:
            return None                         # side unknown: refuse
        ref_is_home = sh > sa
        out = {"event_id": g["event_id"], "state": g.get("state"),
               "start": g.get("date"), "ref_is_home": ref_is_home,
               "league": sport, "model": None, "margin": None, "provider": None}
        try:
            ref = home if ref_is_home else away
            oth = away if ref_is_home else home
            now_margin = int(float(ref["score"])) - int(float(oth["score"]))
            if g.get("state") == "post" and g.get("completed"):
                out["margin"] = now_margin
            if g.get("state") == "in":
                out["margin_now"] = now_margin
                out["score"] = (ref["score"], oth["score"])
        except (TypeError, ValueError, KeyError):
            pass
        out["period"], out["clock"] = g.get("period"), g.get("clock")
        out["_path"] = path
        return out
