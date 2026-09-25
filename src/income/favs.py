"""Favourites and longshots, on paper: does either end of the board pay HERE?

The idea under test: markets that price one side at 85-96c, days before the
game, drift toward 99c often enough that small bets on all of them - held to
settlement or cashed out at a target - make money after fees. RESEARCH.md has
evidence FOR favourites (esports, Polymarket global, 0.75-0.90) and AGAINST the
drift (0.85-0.98 prices fell slightly over the next few bars). Neither was
measured here, so this measures it here, with no money at risk.

Everything comes from the market listing all_slugs() already pages through
every 30 minutes (bestBid/bestAsk per market), so screening ~9,000 contract
sides costs no extra requests. Grading uses the venue's own settlement value,
which covers props, totals and fights, not only games ESPN can score.

A position is one contract side:
  long   buy the listed side at the ask; pays `settlement`
  short  buy the other side at (1 - bid); pays `1 - settlement`

Every position records BOTH exits, so one sample answers both questions:
  hold     payout - entry - entry fee
  target   first time the exit price (bid for long, 1 - ask for short) reached
           0.97 / 0.98 / 0.99: target - entry - both fees; else the hold result

Longshots (band "long", 0.001-0.05) are the mirror question: the 1000-to-1
tickets. The global tape says they are the most overpriced thing on the board;
this checks the US venue. Their targets are multiples of entry (2x, 5x).

Marks are 30 minutes apart, so a target touched and lost between two
listings is missed. That biases the target exits DOWN, never up.
"""

import json
import os
import re
from datetime import datetime, timezone

from src.pm_us.fees import taker_fee
from src.pm_us.jsonlog import append

PATH = os.path.join("research", "favs.json")
LEDGER = os.path.join("research", "favs.jsonl")

BANDS = {"fav": (0.85, 0.96), "long": (0.001, 0.05)}
LO, HI = BANDS["fav"]
MAX_SPREAD = 0.03           # wider than this, the listed ask is not a real price
MAX_HOURS = 168.0           # game starts within a week; futures take months
TARGETS = (0.97, 0.98, 0.99)
SKIP_TYPES = {"futures", "election"}
SETTLE_PER_RUN = 60         # settlement lookups per listing refresh
GIVE_UP_DAYS = 21           # still unsettled this long after it left the listing
SEEN_DAYS = 45              # forget closed keys after this (they never relist)


def _px(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quote_row(m):
    """[type, gameStartTime, bid, ask] from one listing row, or None."""
    t = m.get("marketType") or ""
    if t in SKIP_TYPES:
        return None
    bid, ask = _px(m.get("bestBidQuote")), _px(m.get("bestAskQuote"))
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        return None
    return [t, m.get("gameStartTime"), bid, ask]


def game_key(slug):
    """Positions on one game are not independent: cluster by this."""
    m = re.match(r"^[a-z]+-(.+?\d{4}-\d{2}-\d{2})", slug or "")
    return m.group(1) if m else slug


def hours_to(iso, now):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t - now).total_seconds() / 3600.0


def in_band(bid, ask, bands=BANDS):
    """[(band, side, entry px)] for each side of one market inside a band."""
    out = []
    if ask - bid > MAX_SPREAD + 1e-9:
        return out
    for band, (lo, hi) in bands.items():
        if lo <= ask <= hi:
            out.append((band, "long", ask))
        if lo <= round(1.0 - bid, 6) <= hi:
            out.append((band, "short", round(1.0 - bid, 6)))
    return out


def targets_for(band, px):
    """{name: exit price}. Favourites cash out near 1; longshots at a multiple."""
    if band == "long":
        return {f"{m}x": round(m * px, 4) for m in (2, 5) if m * px < 1.0}
    return {str(t): t for t in TARGETS}


def pos_key(slug, side, band):
    return f"{slug}|{side}" if band == "fav" else f"{slug}|{side}|{band}"


def exit_px(side, bid, ask):
    return bid if side == "long" else round(1.0 - ask, 6)


def fresh():
    return {"listing_ts": 0.0, "open": {}, "seen": {}}


def load(path=PATH):
    try:
        with open(path) as fh:
            d = json.load(fh)
        base = fresh()
        base.update(d)
        return base
    except (OSError, ValueError):
        return fresh()


def save(d, path=PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)


def screen(d, quotes, now, log=None):
    """Open a paper position for every in-band side not seen before."""
    n = 0
    today = now.date().isoformat()
    for slug, (t, start, bid, ask) in quotes.items():
        h = hours_to(start, now)
        if h is None or not 0 < h <= MAX_HOURS:
            continue                        # started, or too far out
        for band, side, px in in_band(bid, ask):
            key = pos_key(slug, side, band)
            if key in d["seen"]:
                continue
            d["seen"][key] = today
            pos = {"slug": slug, "side": side, "px": px, "t": now.isoformat(),
                   "hours": round(h, 2), "type": t, "game": game_key(slug),
                   "spread": round(ask - bid, 4), "low": px, "hits": {},
                   "band": band, "targets": targets_for(band, px)}
            d["open"][key] = pos
            n += 1
            if log:
                log("fav_open", **{k: pos[k] for k in
                                   ("slug", "side", "px", "hours", "type", "game",
                                    "spread", "band")})
    return n


def mark(d, quotes, now):
    """Record the lowest exit price seen and the first touch of each target."""
    for pos in d["open"].values():
        q = quotes.get(pos["slug"])
        if not q:
            continue
        v = exit_px(pos["side"], q[2], q[3])
        pos["low"] = min(pos.get("low", pos["px"]), v)
        for name, tg in _targets(pos).items():
            if v >= tg - 1e-9 and name not in pos["hits"]:
                pos["hits"][name] = now.isoformat()


def _targets(pos):
    # positions opened before bands existed are favourites
    return pos.get("targets") or targets_for("fav", pos["px"])


def result(pos, settlement):
    """Per-share P&L of both exits, fees included."""
    payout = settlement if pos["side"] == "long" else 1.0 - settlement
    px = pos["px"]
    hold = payout - px - taker_fee(px)
    out = {"payout": round(payout, 4), "hold": round(hold, 5)}
    for name, tg in _targets(pos).items():
        out[f"x{name}"] = round(tg - px - taker_fee(px) - taker_fee(tg), 5) \
            if name in pos["hits"] else round(hold, 5)
    return out


def parse_settlement(r):
    if not isinstance(r, dict):
        return None
    for k in ("settlement", "settlementPrice"):
        if k in r:
            return _px(r[k])
    for v in r.values():
        if isinstance(v, dict):
            s = parse_settlement(v)
            if s is not None:
                return s
    return None


def resolve(d, active, settle_fn, now, log=None, budget=SETTLE_PER_RUN):
    """Grade positions whose market has left the active listing."""
    closed, tried = 0, 0
    if active is None:                      # listing incomplete: absence proves nothing
        return 0
    for key, pos in list(d["open"].items()):
        if pos["slug"] in active:
            pos.pop("gone", None)
            continue
        pos.setdefault("gone", now.isoformat())
        s = None
        if tried < budget:
            tried += 1
            try:
                s = parse_settlement(settle_fn(pos["slug"]))
            except Exception:
                s = None
        if s is None:
            gone = datetime.fromisoformat(pos["gone"])
            if (now - gone).days >= GIVE_UP_DAYS:
                d["open"].pop(key)
                if log:
                    log("fav_close", key=key, reason="unresolved", **_rec(pos))
            continue
        d["open"].pop(key)
        closed += 1
        if log:
            log("fav_close", key=key, reason="settled", settlement=s,
                **_rec(pos), **result(pos, s))
    return closed


def _rec(pos):
    return {**{k: pos[k] for k in ("slug", "side", "px", "hours", "type", "game",
                                   "spread", "low", "hits")},
            "band": pos.get("band", "fav")}


def prune(d, now):
    for key, day in list(d["seen"].items()):
        if key in d["open"]:
            continue
        try:
            age = (now.date() - datetime.fromisoformat(day).date()).days
        except ValueError:
            age = SEEN_DAYS
        if age >= SEEN_DAYS:
            d["seen"].pop(key)


def cycle(listing_ts, quotes, active, settle_fn, say=print, path=PATH, ledger=LEDGER,
          now=None):
    """One pass per listing refresh; a no-op when the listing has not changed."""
    d = load(path)
    if not quotes or listing_ts <= d["listing_ts"]:
        return None
    now = now or datetime.now(timezone.utc)

    def log(kind, **rec):
        append(ledger, {"kind": kind, "ts": now.isoformat(), **rec})

    mark(d, quotes, now)
    closed = resolve(d, active, settle_fn, now, log)
    opened = screen(d, quotes, now, log)
    prune(d, now)
    d["listing_ts"] = listing_ts
    save(d, path)
    say(f"  favs+longshots (PAPER): +{opened} opened, {closed} settled, "
        f"{len(d['open'])} open")
    return opened, closed
