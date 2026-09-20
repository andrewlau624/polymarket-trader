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

    # BOTH sources. all_programs() only returns markets carrying an active
    # reward programme, so a ladder without one is invisible to it - which is
    # how asc-cfb-ill-ohiost came back "no ladder" while plainly existing.
    slugs = set()
    try:
        slugs |= {p["slug"] for p in c.all_programs() if p.get("slug")}
    except Exception as e:
        print(f"  all_programs failed: {type(e).__name__}")
    for params in ({}, {"limit": 500}, {"limit": 1000}):
        try:
            slugs |= {m.get("marketSlug") or m.get("slug")
                      for m in c.markets(**params)}
        except Exception:
            pass
        time.sleep(0.25)
    slugs.discard(None)

    ladders = {}
    for sl in slugs:
        base, k = parse_strike(sl)
        if base is not None:
            ladders.setdefault(base, {})[k] = sl
    ladders = {b: v for b, v in ladders.items() if len(v) >= 4}
    print(f"{len(slugs)} markets, {len(ladders)} ladders")

    if args.list or not args.game:
        # only useful when a bookmaker line EXISTS, so check and rank by that
        print("\nladders with a published bookmaker line (the tradeable set):")
        boards, shown = {}, 0
        for b, v in sorted(ladders.items(), key=lambda kv: -len(kv[1])):
            parsed = parse_slug(b.replace("asc-", "aec-", 1))
            if not parsed:
                continue
            sport, _t, d = parsed
            path = SPORT_PATHS.get(sport)
            if not path:
                continue
            key = (path, d.replace("-", ""))
            if key not in boards:
                try:
                    boards[key] = scoreboard(path, date=key[1])
                except Exception:
                    boards[key] = []
                time.sleep(0.3)
            g = match_game(b.replace("asc-", "aec-", 1), boards[key])
            if not g:
                continue
            sp, ph, pa, prov = espn_line(g["event_id"], path)
            time.sleep(0.25)
            if sp is None:
                continue
            print(f"  {len(v):>3} strikes  spread {float(sp):>+6.1f}  "
                  f"[{prov}]  {b}")
            shown += 1
            if shown >= 12:
                break
        if not shown:
            print("  none - no ladder currently has a bookmaker line published.")
            print("  ESPN carries odds for roughly a quarter of games this far out;")
            print("  coverage improves closer to kickoff, so retry on game day.")
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

    # The ladder resolves on the FIRST slug token's team (the venue's own rules
    # text: asc-cfb-clmsn-cah settles on Clemson). Which side that is decides
    # the sign of every fair value, so establish it rather than assume.
    from src.pm_us.feed import _team_score
    tokens = parse_slug(args.game.replace("asc-", "aec-", 1))[1]
    home = next((t for t in g["teams"] if t["home_away"] == "home"), {})
    away = next((t for t in g["teams"] if t["home_away"] == "away"), {})
    ref_tok = tokens[0] if tokens else ""
    sh, sa = _team_score(ref_tok, home), _team_score(ref_tok, away)
    if sh == sa:
        raise SystemExit(f"cannot tell which side '{ref_tok}' is "
                         f"(home={home.get('abbrev')} away={away.get('abbrev')}). "
                         f"Refusing to price a ladder whose sign is unknown.")
    ref_is_home = sh > sa
    p_ref = (p_home if ref_is_home else p_away)
    ref_name = (home if ref_is_home else away).get("location") or ref_tok
    print(f"{args.game}")
    print(f"  ESPN/{provider}: home line {spread:+}  P(home)={p_home:.3f} "
          f"P(away)={p_away:.3f}")
    print(f"  ladder resolves on '{ref_tok}' = {ref_name} "
          f"({'home' if ref_is_home else 'away'}), P={p_ref:.3f}")

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

    fl, mu, sigma = fair_ladder(list(quotes), spread, p_ref, ref_is_home,
                                args.league)
    print(f"  {ref_name} margin ~ Normal({mu:+.1f}, {sigma:.1f})\n")
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
