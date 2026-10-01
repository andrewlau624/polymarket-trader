"""Kalshi vs Polymarket US on the same game: pairing, executable quotes, fees.

Pure functions only - the recorder does the HTTP. TEST_PLAN.md is the spec.

Polymarket US lists ONE book per game. `marketSides[].long` names the team the
book is quoted in; the other team is bought at 1 - bid. Long is whichever team
the venue lists first, not reliably home or away, so it is always read, never
assumed.

Kalshi lists one market per team. Each has a YES book and a NO book; the YES
ask is 1 - best NO bid. "Team B wins" can be bought as YES on B's market or as
NO on A's market, and either can be cheaper.

Both venues name the game by its ET calendar date (a Monday-night NFL game is
'2026-10-05' on both, though it starts 00:15Z on the 6th).
"""

import math
import re

LEAGUES = {                      # Polymarket slug league -> Kalshi series
    "nfl": "KXNFLGAME", "cfb": "KXNCAAFGAME", "mlb": "KXMLBGAME",
    "nhl": "KXNHLGAME", "nba": "KXNBAGAME", "wnba": "KXWNBAGAME",
    "ufc": "KXUFCFIGHT",
}
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()

PM_TAKER = 0.0695
K_TAKER = 0.07


# --- fees ----------------------------------------------------------------
def pm_fee(p, c=1):
    """Polymarket US taker fee in dollars for c shares at price p."""
    return PM_TAKER * c * p * (1.0 - p)


def k_fee(p, c=1):
    """Kalshi taker fee in dollars for ONE order of c contracts at price p,
    rounded UP to the cent (the conservative reading; see TEST_PLAN.md)."""
    raw = K_TAKER * c * p * (1.0 - p)
    return math.ceil(round(raw * 100, 6)) / 100.0


# --- pairing -------------------------------------------------------------
def kalshi_event(ticker):
    """'KXNFLGAME-26OCT05ATLNO-NO' -> ('KXNFLGAME', '2026-10-05', 'NO').
    MLB carries a start time: 'KXMLBGAME-26OCT011400PHIATL-PHI'."""
    parts = ticker.split("-")
    if len(parts) != 3:
        return None
    m = re.match(r"(\d\d)([A-Z]{3})(\d\d)", parts[1])
    if not m or m.group(2) not in MON:
        return None
    yy, mon, dd = m.groups()
    return parts[0], f"20{yy}-{MON.index(mon) + 1:02d}-{dd}", parts[2]


def _tokens(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return ["state" if t == "st" else t for t in s.split()]


def side_names(side):
    t = side.get("team") or {}
    return [side.get("description"), t.get("name"), t.get("alias"), t.get("safeName")]


def side_codes(side):
    t = side.get("team") or {}
    return {(t.get(k) or "").upper() for k in ("abbreviation", "displayAbbreviation")} - {""}


def score(side, kmkt):
    """How surely a Polymarket side is this Kalshi team market: 1.0 on a code
    match or when every word of Kalshi's team name is in a Polymarket name."""
    code = kalshi_event(kmkt["ticker"])[2]
    if code in side_codes(side):
        return 1.0
    kt = _tokens(kmkt.get("yes_sub_title"))
    if not kt:
        return 0.0
    best = 0.0
    for name in side_names(side):
        pt = _tokens(name)
        if not pt:
            continue
        hit = sum(1 for w in kt if w in pt or (len(w) == 1 and any(x.startswith(w) for x in pt)))
        best = max(best, hit / len(kt))
        if len(pt) >= 2 and all(w in kt for w in pt):     # 'Imanol Rodriguez' in
            best = 1.0                                     # 'Imanol Rodriguez Pillado'
    return best


def assign(sides, kmkts):
    """Map the two Polymarket sides onto two Kalshi markets, or None.
    Requires both pairings sure (1.0) and the crossed assignment not also sure.
    Partial scores are common and meaningless ('Oregon St.' vs 'Colorado
    State' shares 'state'), so only a sure crossed pairing makes it ambiguous."""
    if len(sides) != 2 or len(kmkts) != 2:
        return None
    a, b = sides
    s = [[score(x, k) for k in kmkts] for x in (a, b)]
    straight = min(s[0][0], s[1][1])
    crossed = min(s[0][1], s[1][0])
    if straight >= 0.999 and crossed < 0.999:
        return {id(a): kmkts[0], id(b): kmkts[1]}
    if crossed >= 0.999 and straight < 0.999:
        return {id(a): kmkts[1], id(b): kmkts[0]}
    return None


def pair_games(pm_markets, k_markets):
    """[{game, league, date, start, pm_slug, long, short, k_long, k_short}].

    long/short are Polymarket's sides (display names); k_long/k_short are the
    Kalshi tickers for the SAME teams. A game is dropped unless exactly one
    Kalshi event on that date maps one-to-one."""
    events = {}
    for m in k_markets:
        ev = kalshi_event(m.get("ticker", ""))
        if ev:
            events.setdefault((ev[0], ev[1], m["event_ticker"]), []).append(m)
    out = []
    for p in pm_markets:
        slug = p.get("slug", "")
        parts = slug.split("-")
        if len(parts) < 3 or parts[0] != "aec" or parts[1] not in LEAGUES:
            continue
        if p.get("marketType") != "moneyline":
            continue
        series, date = LEAGUES[parts[1]], slug[-10:]
        sides = p.get("marketSides") or []
        hits = []
        for (ser, d, evt), ms in events.items():
            if ser == series and d == date:
                m = assign(sides, sorted(ms, key=lambda x: x["ticker"]))
                if m:
                    hits.append((evt, m))
        if len(hits) != 1:
            continue
        evt, m = hits[0]
        longs = [s for s in sides if s.get("long")]
        shorts = [s for s in sides if not s.get("long")]
        if len(longs) != 1 or len(shorts) != 1:
            continue
        out.append({"game": evt, "league": parts[1], "date": date,
                    "start": p.get("gameStartTime"), "pm_slug": slug,
                    "long": longs[0].get("description"), "short": shorts[0].get("description"),
                    "k_long": m[id(longs[0])]["ticker"], "k_short": m[id(shorts[0])]["ticker"]})
    return out


# --- quotes --------------------------------------------------------------
def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pm_quote(book):
    """Polymarket US book JSON -> [bid, bid_sz, ask, ask_sz, state] for LONG."""
    b = book.get("marketData", book) if isinstance(book, dict) else {}

    def lv(rows):
        out = []
        for r in rows or []:
            px = r.get("px")
            px = f(px.get("value") if isinstance(px, dict) else px)
            q = f(r.get("qty"))
            if px is not None and q:
                out.append((px, q))
        return out
    bids = sorted(lv(b.get("bids")), reverse=True)
    asks = sorted(lv(b.get("offers")))
    return [bids[0][0] if bids else None, bids[0][1] if bids else 0.0,
            asks[0][0] if asks else None, asks[0][1] if asks else 0.0, b.get("state")]


def k_quote(m):
    """Kalshi market JSON -> [yes_bid, size, yes_ask, size, status].
    A zero price means no order on that side."""
    yb, ya = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
    yb = yb if yb and yb > 0 else None
    ya = ya if ya and ya < 1 else None
    return [yb, f(m.get("yes_bid_size_fp")) or 0.0, ya, f(m.get("yes_ask_size_fp")) or 0.0,
            m.get("status")]


def buys(row):
    """Every way to buy each team, as {team: [(venue, how, price, size)]}.
    team is 'L' (Polymarket's long team) or 'S'. row holds pm, kl, ks quotes."""
    pm, kl, ks = row["pm"], row["kl"], row["ks"]
    out = {"L": [], "S": []}
    if pm and pm[4] == "MARKET_STATE_OPEN":
        if pm[2] is not None:
            out["L"].append(("pm", "long", pm[2], pm[3]))
        if pm[0] is not None:
            out["S"].append(("pm", "short", round(1 - pm[0], 6), pm[1]))
    for team, own, other in (("L", kl, ks), ("S", ks, kl)):
        if own and own[4] == "active" and own[2] is not None:
            out[team].append(("k", "yes", own[2], own[3]))
        if other and other[4] == "active" and other[0] is not None:   # NO on the other team
            out[team].append(("k", "no", round(1 - other[0], 6), other[1]))
    return out


def fee(venue, p, c):
    return pm_fee(p, c) if venue == "pm" else k_fee(p, c)


def locked_pairs(row, cap=100):
    """S1: the best cross-venue pair per direction. [{dir, net, c, legs}] where
    net is per $1 pair after both fees at size c (c = min top depth, <= cap).

    A pair is one long-team leg and one short-team leg on different venues, so
    there are exactly two directions, named by where the LONG team is bought:
    'L@pm' (L on Polymarket, S on Kalshi) and 'L@k'. Iterating from the short
    team too would count every pair twice."""
    bs = buys(row)
    best = {}
    for a in bs["L"]:
        for b in bs["S"]:
            if a[0] == b[0]:
                continue                         # same venue: that is just its spread
            c = int(min(a[3], b[3], cap))
            if c < 1:
                continue
            cost = a[2] + b[2] + (fee(a[0], a[2], c) + fee(b[0], b[2], c)) / c
            net = 1.0 - cost
            d = f"L@{a[0]}"
            if d not in best or net > best[d]["net"]:
                best[d] = {"dir": d, "net": round(net, 5), "c": c, "legs": [a[:3], b[:3]]}
    return list(best.values())


def mid(q):
    if not q or q[0] is None or q[2] is None:
        return None, None
    return (q[0] + q[2]) / 2.0, q[2] - q[0]


def fair(row, team, venue):
    """The OTHER venue's mid for `team`, if its spread <= 0.03 (TEST_PLAN S2)."""
    if venue == "pm":
        q = row["kl"] if team == "L" else row["ks"]
        if not q or q[4] != "active":
            return None
        m, w = mid(q)
    else:
        q = row["pm"]
        if not q or q[4] != "MARKET_STATE_OPEN":
            return None
        m, w = mid(q)
        if m is not None and team == "S":
            m = 1 - m
    return m if m is not None and w <= 0.03 + 1e-9 else None


def cheap_entries(row, edge=0.010, cap=100):
    """S2 triggers in one snapshot: [(team, venue, how, ask, fair, c)].
    Kalshi entries are YES on the team's own market; fee per share is taken at
    the order size c = min(top depth, cap)."""
    out = []
    for team, opts in buys(row).items():
        for venue, how, px, sz in opts:
            c = int(min(sz, cap))
            if c < 1 or (venue == "k" and how != "yes"):
                continue
            fv = fair(row, team, venue)
            if fv is None:
                continue
            if px + fee(venue, px, c) / c <= fv - edge:
                out.append((team, venue, how, px, round(fv, 4), c))
    return out


# --- S3: rest on Polymarket, hedge on Kalshi -----------------------------
PM_MAKER = 0.0125
PM_TICK = 0.001


def pm_rebate(p, c=1):
    """Polymarket US maker rebate in dollars (money IN)."""
    return PM_MAKER * c * p * (1.0 - p)


def k_best_buy(row, team, min_c=10):
    """Cheapest Kalshi way to buy `team` with >= min_c depth: (px, depth) or None."""
    opts = [(px, sz) for v, _how, px, sz in buys(row)[team] if v == "k" and sz >= min_c]
    return min(opts) if opts else None


def s3_post(row, team, cap=100, hurdle=0.005):
    """A paper resting order on Polymarket that would net >= hurdle if filled
    now and hedged on Kalshi now, or None. Returns {team, px (our price for
    `team`), rest (the long-book price we rest at), side}.

    team L: bid on the long book at bid + tick.  team S: offer the long book at
    ask - tick, which buys S at 1 - that price. Both must stay inside the spread."""
    pm = row.get("pm")
    if not pm or pm[4] != "MARKET_STATE_OPEN" or pm[0] is None or pm[2] is None:
        return None
    other = "S" if team == "L" else "L"
    h = k_best_buy(row, other)
    if not h:
        return None
    if team == "L":
        rest = round(pm[0] + PM_TICK, 6)
        if rest >= pm[2]:
            return None
        px = rest
    else:
        rest = round(pm[2] - PM_TICK, 6)
        if rest <= pm[0]:
            return None
        px = round(1.0 - rest, 6)
    c = int(min(h[1], cap))
    net = 1.0 - (px - pm_rebate(px)) - (h[0] + k_fee(h[0], c) / c)
    if net < hurdle:
        return None
    return {"team": team, "px": px, "rest": rest, "net_at_post": round(net, 5)}


def s3_filled(order, row):
    """Did the market trade through our resting price by this observation?"""
    pm = row.get("pm")
    if not pm:
        return False
    if order["team"] == "L":
        return pm[2] is not None and pm[2] <= order["rest"]
    return pm[0] is not None and pm[0] >= order["rest"]


def s3_hedge(order, row, cap=100):
    """Net per pair hedging a filled order on Kalshi at THIS observation, and
    the size; None if the hedge is not available (a failure, see report)."""
    other = "S" if order["team"] == "L" else "L"
    h = k_best_buy(row, other)
    if not h:
        return None
    c = int(min(h[1], cap))
    px = order["px"]
    return round(1.0 - (px - pm_rebate(px)) - (h[0] + k_fee(h[0], c) / c), 5), c
