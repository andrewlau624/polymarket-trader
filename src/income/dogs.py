"""Underdogs, on paper, going forward: the live half of dog_backtest.py.

Every college football / NFL moneyline side priced 5-20% within 6 hours of
kickoff becomes a paper position, with two numbers recorded at entry: the
season-stats rating's win probability (src/income/ratings.py) and the de-vigged
sportsbook price. Step 1 (all dogs) and step 2 (only dogs the rating likes)
are then read off the same positions by fav_report.py --dogs.

In-game, the bot's in-play loop already reads every live moneyline's book each
poll; mark_live() uses that read, so this costs no requests. A 40/60/80% exit
counts only when the BID held the target for CONFIRM consecutive polls, in a
market the venue says is matching: the Liberty-Coastal book showed a 0.53 ask
for eight minutes that six real orders could not fill. The paper sale is
booked at the target, not the (higher) bid that confirmed it.

The in-play loop does not run during full-scan cycles (13/17/21/01 UTC), so a
spike during one is missed. That only ever biases the exits DOWN.

Grading is the venue's own settlement, via favs.resolve().
"""

import json
import os
import time
from datetime import datetime, timezone

from src.income import favs

PATH = os.path.join("research", "dogs.json")
LEDGER = os.path.join("research", "dogs.jsonl")
RATINGS = os.path.join("research", "ratings.json")
BAND = {"dog": (0.05, 0.20)}
MAX_HOURS = 6.0
TARGETS = (0.40, 0.60, 0.80)
CONFIRM = 3
LEAGUES = ("cfb", "nfl")


def targets():
    return {str(t): t for t in TARGETS}


def league_of(slug):
    for lg in LEAGUES:
        if slug.startswith(f"aec-{lg}-"):
            return lg
    return None


# ---- ratings ---------------------------------------------------------------

def season_of(now):
    return now.year if now.month >= 3 else now.year - 1   # January bowls/playoffs


def refresh_ratings(path=RATINGS, now=None, max_age_h=20.0, say=print):
    """Refit both leagues from ESPN at most once a day. Returns the ratings doc."""
    now = now or datetime.now(timezone.utc)
    doc = load_ratings(path)
    if doc and time.time() - doc.get("ts", 0) < max_age_h * 3600:
        return doc
    import dog_backtest as bt
    from src.income import ratings as R
    season = season_of(now)
    new = {"ts": time.time(), "season": season, "leagues": {}}
    for lg in LEAGUES:
        try:
            sigma = (doc or {}).get("leagues", {}).get(lg, {}).get("sigma")
            if not sigma:
                sigma, _ = bt.measure_sigma(lg, (season - 4, season - 3, season - 2))
            prior = R.carry(bt.season_final(lg, season - 1))
            ev = bt.season_events(lg, season, final=False)
            r, hfa = R.fit(bt.games_of(ev), prior, league=lg)
            new["leagues"][lg] = {"r": r, "hfa": hfa, "sigma": sigma, "games": len(ev)}
            say(f"  ratings {lg}: {len(ev)} games this season, sigma {sigma:.1f}")
        except Exception as e:
            say(f"  ratings {lg} FAILED {type(e).__name__}: {str(e)[:100]}")
            if doc and lg in doc.get("leagues", {}):
                new["leagues"][lg] = doc["leagues"][lg]
    tmp = f"{path}.tmp-{os.getpid()}"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(new, fh)
    os.replace(tmp, path)
    return new


def load_ratings(path=RATINGS):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def model_p_home(doc, league, home, away, neutral):
    from src.income import ratings as R
    lg = (doc or {}).get("leagues", {}).get(league)
    if not lg:
        return None
    return R.win_prob(lg["r"], lg["hfa"], home, away, neutral, league, lg["sigma"])


# ---- paper positions ---------------------------------------------------------

def screen(d, quotes, lines, ratings_doc, now, log=None):
    """Open a paper position on every in-band dog side not seen before."""
    n = 0
    today = now.date().isoformat()
    for slug, (t, start, bid, ask) in quotes.items():
        lg = league_of(slug)
        if t != "moneyline" or lg is None:
            continue
        h = favs.hours_to(start, now)
        if h is None or not 0 < h <= MAX_HOURS:
            continue
        for _band, side, px in favs.in_band(bid, ask, BAND):
            key = f"{slug}|{side}"
            if key in d["seen"]:
                continue
            d["seen"][key] = today
            pos = {"slug": slug, "side": side, "px": px, "t": now.isoformat(),
                   "hours": round(h, 2), "type": t, "game": favs.game_key(slug),
                   "spread": round(ask - bid, 4), "low": px, "hits": {},
                   "band": "dog", "targets": targets(), "league": lg,
                   "model": None, "book": None, "dog": None, "run": {}, "hit_px": {}}
            info = None
            try:
                info = lines.game(slug)
            except Exception:
                pass
            if info and info.get("home_abbr") and info.get("away_abbr"):
                # the long side pays on the FIRST slug token's team
                dog_home = info["ref_is_home"] == (side == "long")
                pos["dog"] = info["home_abbr"] if dog_home else info["away_abbr"]
                ph = model_p_home(ratings_doc, lg, info["home_abbr"], info["away_abbr"],
                                  info.get("neutral", False))
                if ph is not None:
                    pos["model"] = round(ph if dog_home else 1.0 - ph, 4)
                bh = info.get("p_home") if dog_home else info.get("p_away")
                if bh is not None:
                    pos["book"] = round(bh, 4)
            d["open"][key] = pos
            n += 1
            if log:
                log("fav_open", **{k: pos[k] for k in
                                   ("slug", "side", "px", "hours", "type", "game",
                                    "band", "league", "dog", "model", "book")})
    return n


def mark_live(d, slug, q, now_iso):
    """One live poll of a moneyline: count consecutive polls at each target.
    q is the executor's quote, already dropped if the market was not matching."""
    changed = False
    for side in ("long", "short"):
        pos = d["open"].get(f"{slug}|{side}")
        if not pos:
            continue
        bid, ask = (q or {}).get("bid"), (q or {}).get("ask")
        v = (bid if side == "long" else (None if ask is None else round(1.0 - ask, 6)))
        for name, tg in pos["targets"].items():
            if name in pos["hits"]:
                continue
            run = pos["run"].get(name, 0) + 1 if v is not None and v >= tg - 1e-9 else 0
            pos["run"][name] = run
            if run >= CONFIRM:
                pos["hits"][name] = now_iso
                pos["hit_px"][name] = v
            changed = True
        if v is not None:
            pos["low"] = min(pos["low"], v)
    return changed


def cycle(listing_ts, quotes, active, settle_fn, lines, ratings_doc, say=print,
          path=PATH, ledger=LEDGER, now=None):
    """Grade and screen once per listing refresh (marks come from mark_live)."""
    d = favs.load(path)
    if not quotes or listing_ts <= d["listing_ts"]:
        return None
    now = now or datetime.now(timezone.utc)

    def log(kind, **rec):
        from src.pm_us.jsonlog import append
        append(ledger, {"kind": kind, "ts": now.isoformat(), **rec})

    closed = favs.resolve(d, active, settle_fn, now, log)
    opened = screen(d, quotes, lines, ratings_doc, now, log)
    favs.prune(d, now)
    d["listing_ts"] = listing_ts
    favs.save(d, path)
    say(f"  dogs (PAPER): +{opened} opened, {closed} settled, {len(d['open'])} open")
    return opened, closed
