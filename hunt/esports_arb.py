"""Best-of-3 esports coherence on Polymarket US, at EXECUTABLE prices.

    .venv/bin/python hunt/esports_arb.py            # one scan of every open esports event

Polymarket US lists, per bo3: the series winner (ML), ONE series handicap -1.5
(long = 'A wins 2-0') and total maps over 2.5. Three bundles pay >= $1 in every
outcome (A 2-0, A 2-1, B 2-0, B 2-1):
    (i)   A wins + not A 2-0          (A 2-0 implies A wins)
    (ii)  not A 2-0 + under 2.5       (A 2-0 and 3 maps are exclusive)
    (iii) A 2-0 + over 2.5 + B wins   (A wins implies 2-0 or 3 maps)
so buying one for less than $1 after fees is locked profit.
Each US market is one book quoted in its long side; buying the other side costs
1 - bid. Fees: Polymarket US taker 0.0695 p(1-p) per share on each leg.
"""

import json
import sys
import time

sys.path.insert(0, ".")
from src.xvenue import core                                        # noqa: E402
from xvenue_recorder import PM, get                                # noqa: E402

G = "https://gateway.polymarket.us/v1"
TAGS = ("cs2", "lol", "dota2", "val")
FEE = core.pm_fee


def books(e):
    """{'ML': market, 'H': market, 'OVER': market} for one bo3 event, or {}.
    H is the series -1.5 handicap: its LONG side is 'that team wins 2-0'."""
    out = {}
    for m in e.get("markets") or []:
        t = m.get("sportsMarketType") or ""
        if t == "esports_series_map_handicap" and m["slug"].endswith("1pt5"):
            out["H"] = m
        elif t == "esports_series_total_maps" and m["slug"].endswith("tot-2pt5"):
            out["OVER"] = m
        elif m.get("marketType") == "moneyline":
            out["ML"] = m
    return out if len(out) == 3 else {}


def long_team(m):
    s = next((x for x in m.get("marketSides") or [] if x.get("long")), {})
    return (s.get("team") or {}).get("id"), s.get("description")


def buy_cost(q, long_side):
    """(price, size) to BUY the long side (at the ask) or the short side (1 - bid)."""
    if not q or q[4] != "MARKET_STATE_OPEN":
        return None
    if long_side:
        return (q[2], q[3]) if q[2] is not None else None
    return (round(1 - q[0], 6), q[1]) if q[0] is not None else None


def check(mk, quotes):
    """Every bundle below pays >= $1 whatever happens, so 1 - cost is locked profit.
    A = the team whose -1.5 handicap is listed (H long = 'A wins 2-0')."""
    h_team, h_desc = long_team(mk["H"])
    if not str(h_desc or "").startswith("-"):
        return []                                   # need the -1.5 side as long
    ml_team, _ = long_team(mk["ML"])
    a_is_ml_long = ml_team == h_team
    ml_a = lambda: buy_cost(quotes["ML"], a_is_ml_long)        # buy 'A wins'
    ml_b = lambda: buy_cost(quotes["ML"], not a_is_ml_long)    # buy 'B wins'
    h_yes = lambda: buy_cost(quotes["H"], True)                # buy 'A 2-0'
    h_no = lambda: buy_cost(quotes["H"], False)                # buy 'not A 2-0'
    over = lambda: buy_cost(quotes["OVER"], True)
    under = lambda: buy_cost(quotes["OVER"], False)
    out = []
    for name, legs in (("(i)  A wins + not A2-0", (ml_a(), h_no())),
                       ("(ii) not A2-0 + under", (h_no(), under())),
                       ("(iii) A2-0 + over + B wins", (h_yes(), over(), ml_b()))):
        if all(legs):
            cost = sum(p + FEE(p) for p, _ in legs)
            out.append((name, round(1 - cost, 4), min(sz for _, sz in legs)))
    return out


def main():
    rows = []
    for tag in TAGS:
        d = get(f"{G}/events", {"limit": 200, "active": "true", "closed": "false", "tag_slug": tag}) or []
        ev = d.get("events", d) if isinstance(d, dict) else d
        for e in ev:
            if e.get("period") in ("FT", "F"):
                continue
            mk = books(e)
            if not mk:
                continue
            quotes = {}
            for k, m in mk.items():
                b = get(f"{PM}/markets/{m['slug']}/book")
                quotes[k] = core.pm_quote(b) if b else None
                time.sleep(0.2)
            for name, net, size in check(mk, quotes):
                rows.append((net, size, tag, e["slug"], e.get("period"), name))
    rows.sort(reverse=True)
    print(f"{len(rows)} checks on open bo3 events; best executable nets (per partition, after fees):")
    for r in rows[:15]:
        print(f"  net {r[0]:+.4f} size {r[1]:>8.1f}  {r[2]:<5} {r[3]:<40} period {r[4]}  {r[5]}")
    json.dump(rows, open("research/esports_arb_snapshot.json", "w"))


if __name__ == "__main__":
    main()
