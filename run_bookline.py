"""Price a Polymarket ladder off a bookmaker line, then trade the SHAPE.

    python run_bookline.py --list
    python run_bookline.py --game asc-cfb-clmsn-cah-2026-09-25

For each strike it reports the venue's executable quote against a fair value
derived from one external line (ESPN publishes DraftKings spread + moneyline,
free, no key), and proposes the best MARKET-NEUTRAL pair: buy the strike that
is cheapest relative to fair, sell the one that is dearest.

Why a pair and not a directional bet. If the bookmaker's LEVEL is wrong, both
legs move together and the error cancels; only the relative SHAPE has to be
right. polymm bet the level, rested its quotes, and gave back 38% of gross to
the unhedged residual. This bets the shape and takes both legs at once.
"""

import argparse
import time

from run_ladder import parse_strike
from src.edge.bookline import devig, fair_ladder
from src.pm_us.feed import SPORT_PATHS, match_game, parse_slug, scoreboard

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports"


def espn_line(event_id, sport_path):
    """(spread_for_home, p_home, p_away, provider) or Nones."""
    try:
        r = requests.get(f"{SITE}/{sport_path}/summary",
                         params={"event": event_id}, timeout=20)
        r.raise_for_status()
        pc = (r.json().get("pickcenter") or [])
    except Exception:
        return None, None, None, None
    if not pc:
        return None, None, None, None
    p = pc[0]
    home = (p.get("homeTeamOdds") or {}).get("moneyLine")
    away = (p.get("awayTeamOdds") or {}).get("moneyLine")
    ph, pa = devig(home, away)
    return p.get("spread"), ph, pa, (p.get("provider") or {}).get("name")


def main():
    ap = argparse.ArgumentParser(description="Bookmaker-anchored ladder pricing.")
    ap.add_argument("--game", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--league", default="cfb")
    ap.add_argument("--near", type=int, default=14)
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--min-edge", type=float, default=0.03)
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()
    ladders = {}
    for p in c.all_programs():
        base, k = parse_strike(p.get("slug"))
        if base is not None:
            ladders.setdefault(base, {})[k] = p["slug"]
    ladders = {b: v for b, v in ladders.items() if len(v) >= 4}
    if args.list or not args.game:
        print(f"{len(ladders)} ladders:")
        for b, v in sorted(ladders.items(), key=lambda kv: -len(kv[1]))[:20]:
            print(f"  {len(v):>3} strikes  {b}")
        c.close()
        return

    ks = ladders.get(args.game)
    if not ks:
        raise SystemExit(f"no ladder for {args.game!r}")

    parsed = parse_slug(args.game.replace("asc-", "aec-", 1))
    if not parsed:
        raise SystemExit("cannot parse the game slug")
    sport, _t, date = parsed
    path = SPORT_PATHS.get(sport)
    board = scoreboard(path, date=date.replace("-", ""))
    g = match_game(args.game.replace("asc-", "aec-", 1), board)
    if not g:
        raise SystemExit("no ESPN game matched - cannot fetch a line")
    spread, p_home, p_away, provider = espn_line(g["event_id"], path)
    if spread is None:
        raise SystemExit(f"no bookmaker line published for {g['short']}")

    # the ladder is written from ONE team; decide which by the slug order
    home = next((t for t in g["teams"] if t["home_away"] == "home"), {})
    fav_is_home = (p_home or 0) >= (p_away or 0)
    p_fav = max(p_home or 0, p_away or 0)
    print(f"{args.game}")
    print(f"  ESPN/{provider}: spread {spread}  P(home)={p_home:.3f} "
          f"P(away)={p_away:.3f}  favourite={'home' if fav_is_home else 'away'}")

    want = sorted(sorted(ks, key=lambda k: abs(k))[: args.near])
    quotes = {}
    for k in want:
        for attempt in range(3):
            try:
                b, a, _s = c.book_levels(ks[k])
                quotes[k] = (b[0][0] if b else None, b[0][1] if b else 0,
                             a[0][0] if a else None, a[0][1] if a else 0)
                break
            except Exception:
                time.sleep(1.2 * (2 ** attempt))
        time.sleep(args.pause)
    c.close()

    fl, mu, sigma = fair_ladder(list(quotes), abs(float(spread)), p_fav, args.league)
    print(f"  implied margin ~ Normal({mu:.1f}, {sigma:.1f})\n")
    print(f"  {'line':>7} {'bid':>7} {'ask':>7} {'fair':>7} {'buy edge':>9} "
          f"{'sell edge':>10}")
    rows = []
    for k in sorted(quotes):
        bid, bsz, ask, asz = quotes[k]
        f = fl[k]
        be = (f - ask) if ask is not None else None      # buy: fair above ask
        se = (bid - f) if bid is not None else None      # sell: bid above fair
        rows.append((k, bid, bsz, ask, asz, f, be, se))
        print(f"  {k:>+7.1f} {str(bid):>7} {str(ask):>7} {f:>7.3f} "
              f"{('%+.3f' % be) if be is not None else '    -':>9} "
              f"{('%+.3f' % se) if se is not None else '    -':>10}")

    buys = [r for r in rows if r[6] is not None and r[6] > args.min_edge]
    sells = [r for r in rows if r[7] is not None and r[7] > args.min_edge]
    print()
    if not buys or not sells:
        print(f"  no market-neutral pair clears {args.min_edge:.3f} on both legs.")
        print("  A one-sided edge is a directional bet on the bookmaker's LEVEL,")
        print("  which is the exposure that cost polymm 38% of gross. Skipped.")
        return
    b = max(buys, key=lambda r: r[6])
    s = max(sells, key=lambda r: r[7])
    size = min(b[4], s[2])
    print(f"  PAIR  buy {b[0]:+.1f} @ {b[3]:.3f} (fair {b[5]:.3f}, +{b[6]:.3f})")
    print(f"        sell {s[0]:+.1f} @ {s[1]:.3f} (fair {s[5]:.3f}, +{s[7]:.3f})")
    print(f"        combined edge {b[6] + s[7]:+.3f} on {size:.0f} shares "
          f"= ${(b[6] + s[7]) * size:.2f}")
    print("  Both legs taken at once. If the bookmaker's level is wrong the two")
    print("  legs move together and it cancels - only the shape must be right.")


if __name__ == "__main__":
    main()
