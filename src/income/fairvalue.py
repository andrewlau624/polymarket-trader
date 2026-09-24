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
    def __init__(self, pause=0.25, log=print):
        self.boards, self.lines, self.pause, self.say = {}, {}, pause, log

    def _board(self, path, date):
        key = (path, date)
        if key not in self.boards:
            try:
                self.boards[key] = scoreboard(path, date=date)
            except Exception:
                self.boards[key] = []
            time.sleep(self.pause)
        return self.boards[key]

    def game(self, base):
        """dict(model, state, margin, provider, ...) or None. Cached per run."""
        if base in self.lines:
            return self.lines[base]
        self.lines[base] = out = self._lookup(base)
        return out

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
            if g.get("state") == "post" and g.get("completed"):
                out["margin"] = int(float(ref["score"])) - int(float(oth["score"]))
        except (TypeError, ValueError, KeyError):
            pass
        if g.get("state") == "pre":
            from run_bookline import espn_line
            spread, p_home, p_away, prov = espn_line(g["event_id"], path)
            time.sleep(self.pause)
            if spread is not None:
                p_ref = p_home if ref_is_home else p_away
                out["model"] = MarginModel.from_line(spread, p_ref, ref_is_home,
                                                     league=sport if sport in ("cfb", "nfl") else "cfb")
                out["provider"] = prov
                out["spread"] = spread
        return out
