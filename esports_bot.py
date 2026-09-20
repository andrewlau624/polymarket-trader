"""Esports-only trading bot: arbitrage first, then statistical edge.

Runs continuously. Esports plays around the clock, so unlike the football
ladder work there is no schedule to wait on.

THREE LAYERS, in strict priority order. Capital goes to the surest thing first.

  1. NO-ARBITRAGE (src/esports/noarb.py)
     A Bo3 has six possible outcomes; every market on it is an indicator over
     those six. An LP asks whether any probability vector reproduces the quoted
     prices. When none does, it returns the portfolio and the profit guaranteed
     in the WORST outcome, priced at bid/ask and capped at real depth. No edge
     estimate is involved, so Kelly does not apply - only capital and depth
     limit it. The classic case: once map 1 is decided, "over 2.5 maps" and
     "map-1 winner takes map 2" are complementary events whose prices must sum
     to 1. On the cached tape that broke in 12 of 106 events by 0.06-0.115.

  2. STATISTICAL EDGE (measured, RESEARCH.md S21)
     Buy legs priced 0.75-0.90: +0.107, CI [+0.038,+0.164], n=70, split-half
     +0.096/+0.117. Sell legs priced 0.15-0.60: +0.070, CI [+0.019,+0.119],
     n=324. Sized on the CI's LOW end at quarter Kelly, never the point
     estimate.

  3. MODEL DIVERGENCE (src/esports/winprob.py)
     CS2 and Valorant are an exact race-to-13 Markov chain; LoL is a digital
     option on gold difference. Inverting the market price gives the per-round
     edge it implies, which is the sharpest diagnostic available: a team down
     3-9 priced at 0.25 implies winning 63% of remaining rounds. Requires live
     game state, so it stays disabled until a feed is wired in.

Risk (src/esports/risk.py) caps per position, PER EVENT - four legs on one
series are one bet - and in total, with a drawdown breaker that halts rather
than shrinks.

    python esports_bot.py --discover      # what esports exists on the venue
    python esports_bot.py                 # dry run, continuous
    python esports_bot.py --live --bankroll 50
"""

import argparse
import collections
import json
import os
import re
import time
from datetime import datetime, timezone

from src.esports.noarb import find_arbitrage
from src.esports.risk import Book, Limits, register, size_arbitrage, size_edge_trade
from src.esports.series import role_of, series_format, teams_of

LOG = os.path.join("research", "esports_trades.jsonl")
# Word-boundary patterns, NOT substrings. A bare "val" matched Valparaiso,
# Colorado Avalanche, Utah Valley and Virginia Cavaliers - nineteen hits,
# nineteen false positives, and very nearly a confident wrong answer.
ES_RE = re.compile(
    r"\b(lol|valorant|val|cs2|csgo|dota|ow2)\b"
    r"|league\s+of\s+legends|counter[- ]strike|overwatch|rocket\s+league"
    r"|starcraft|\besports?\b|rainbow\s+six|apex\s+legends",
    re.I)

# measured buckets: (low price, high price, side, edge CI low end)
EDGES = [(0.75, 0.90, "buy", 0.038), (0.15, 0.60, "sell", 0.019)]


def now():
    return datetime.now(timezone.utc).isoformat()


def log(rec):
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    rec["ts"] = now()
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def is_esports(slug, question=""):
    return bool(ES_RE.search(f"{slug} {question}"))


def discover(c):
    """Esports markets grouped into events. Tries programs then the full list."""
    rows = {}
    try:
        for p in c.all_programs():
            if p.get("slug"):
                rows[p["slug"]] = p.get("question", "")
    except Exception as e:
        print(f"  all_programs failed: {type(e).__name__}")
    for params in ({}, {"limit": 500}, {"limit": 500, "category": "esports"}):
        try:
            for m in c.markets(**params):
                sl = m.get("marketSlug") or m.get("slug")
                if sl:
                    rows[sl] = m.get("question") or ""
        except Exception:
            pass
        time.sleep(0.3)

    fams = collections.Counter()
    for sl in rows:
        parts = sl.split("-")
        if len(parts) >= 2:
            fams[f"{parts[0]}-{parts[1]}"] += 1
    print("\nevery market family on the venue:")
    for name, n in fams.most_common(24):
        kind = "LADDER" if name.startswith("asc") else "outright"
        print(f"  {name:<14} {n:>6}  {kind}")
    ladders = sorted(n for n in fams if n.startswith("asc"))
    print(f"  ladder families: {', '.join(ladders) or 'none'}")
    nightly = [n for n in fams if n.split('-')[-1] in ("nhl", "nba", "cbb", "mlb")]
    if nightly:
        print(f"  NIGHTLY sports present: {', '.join(sorted(nightly))}")
        print(f"  -> a ladder on any of these makes the ladder trade nightly.")

    es = {sl: q for sl, q in rows.items() if is_esports(sl, q)}
    events = collections.defaultdict(dict)
    for sl, q in es.items():
        # an event key: strip the role suffix from the slug
        key = sl
        for cut in ("-map", "-game", "-total", "-ou"):
            if cut in sl:
                key = sl.split(cut)[0]
                break
        events[key][role_of(q) if q else sl] = (sl, q)
    return rows, es, events


def quote(c, slug):
    for attempt in range(3):
        try:
            bids, asks, _s = c.book_levels(slug)
            return ((bids[0][0] if bids else None), (bids[0][1] if bids else 0),
                    (asks[0][0] if asks else None), (asks[0][1] if asks else 0))
        except Exception:
            time.sleep(1.2 * (2 ** attempt))
    return (None, 0, None, 0)


def market_spec(role):
    """('map', 2) / ('match', None) / ('over', 2.5) from a role label."""
    if role.startswith("map"):
        try:
            return ("map", int(role[3:]))
        except ValueError:
            return None
    if role == "match":
        return ("match", None)
    if role == "total":
        return ("over", 2.5)
    return None


def main():
    ap = argparse.ArgumentParser(description="Esports-only trading bot.")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--bankroll", type=float, default=20.0)
    ap.add_argument("--cycle", type=float, default=3.0, help="minutes per sweep")
    ap.add_argument("--max-events", type=int, default=30)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--min-arb", type=float, default=0.01)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("POLYMARKET_US_KEY_ID"):
        raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
    from src.pm_us.client import UsClient
    c = UsClient()

    rows, es, events = discover(c)
    print(f"{len(rows)} markets seen | {len(es)} look like esports | "
          f"{len(events)} events")
    if args.discover or not es:
        for key, roles in list(events.items())[:20]:
            print(f"  {key[:48]:<48} roles={sorted(roles)}")
        if not es:
            print("\nNo esports markets found on this venue.")
            print("Everything below needs them. If the website shows esports,")
            print("paste a slug and the discovery filter can be corrected -")
            print("this looks for lol/valorant/cs2/dota in slug and question.")
        c.close()
        return

    book = Book(limits=Limits(bankroll=args.bankroll))
    book.check_drawdown()
    print(f"\nmode={'LIVE' if args.live else 'dry run'} bankroll ${args.bankroll:.2f} "
          f"| quarter Kelly on CI low ends | halt at "
          f"{book.limits.max_drawdown:.0%} drawdown")

    try:
        while True:
            t0 = time.time()
            n_arb = n_edge = 0
            for key, roles in list(events.items())[: args.max_events]:
                if book.halted:
                    print(f"HALTED: {book.halt_reason}")
                    break
                specs, quotes, labels = [], [], []
                for role, (slug, _q) in sorted(roles.items()):
                    sp = market_spec(role)
                    if sp is None:
                        continue
                    q4 = quote(c, slug)
                    if q4[0] is None and q4[2] is None:
                        continue
                    specs.append(sp)
                    quotes.append(q4)
                    labels.append((role, slug))
                    time.sleep(args.pause)
                if len(specs) < 2:
                    continue

                bo = series_format([q for _r, (_s, q) in roles.items()]) or 3

                # --- layer 1: arbitrage ---------------------------------
                arb = find_arbitrage(specs, quotes, best_of=bo,
                                     tol=args.min_arb, unit_cap=200)
                if arb:
                    n_arb += 1
                    print(f"  ARB {key[:38]} guaranteed ${arb['profit']:.2f}")
                    for leg in arb["legs"]:
                        print(f"      {leg}")
                    log({"kind": "arb", "event": key, "profit": arb["profit"],
                         "legs": [list(map(str, l)) for l in arb["legs"]],
                         "live": bool(args.live)})
                    continue     # an arb supersedes any edge view on this event

                # --- layer 2: measured statistical edge -----------------
                for (role, slug), (bid, _bs, ask, _as) in zip(labels, quotes):
                    for lo, hi, side, edge_low in EDGES:
                        px = ask if side == "buy" else bid
                        if px is None or not (lo <= px < hi):
                            continue
                        units, why = size_edge_trade(book, key, px, edge_low, side)
                        if units <= 0:
                            continue
                        n_edge += 1
                        risk = px if side == "buy" else (1 - px)
                        print(f"  EDGE {side} {role} @ {px:.3f} x{units:.1f} "
                              f"on {key[:30]}")
                        log({"kind": "edge", "event": key, "slug": slug,
                             "side": side, "price": px, "units": units,
                             "edge_low": edge_low, "why": why,
                             "live": bool(args.live)})
                        if args.live:
                            try:
                                c.place(slug, side, px, int(units), maker=False)
                            except Exception as e:
                                log({"kind": "place_failed", "slug": slug,
                                     "error": f"{type(e).__name__}: {str(e)[:110]}"})
                        register(book, key, units * risk)

            print(f"[{now()[:19]}] sweep in {(time.time()-t0)/60:.1f}min | "
                  f"{n_arb} arbs | {n_edge} edge trades | "
                  f"deployed ${book.deployed:.2f}")
            log({"kind": "sweep", "arbs": n_arb, "edges": n_edge,
                 "deployed": book.deployed, "halted": book.halted})
            if args.once or book.halted:
                break
            time.sleep(max(0.0, args.cycle * 60 - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        c.close()


if __name__ == "__main__":
    main()
