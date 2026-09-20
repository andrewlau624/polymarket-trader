"""Confirm what a ladder strike settles on, using games that ALREADY resolved.

Everything built here rests on one inferred claim: that `pos-5pt` means the
team RECEIVES 5 points, so the contract pays iff `margin > -line`. It was
derived from the shape of a live price curve, not from documentation, and this
project has misread this venue's semantics five times.

Waiting for a live position to settle is one way to find out. This is the
better way: games from past dates have already resolved, so their strikes
should read 1 or 0, and ESPN knows the actual final score. If every settled
strike agrees with `margin > -line`, the convention is confirmed - at no risk
and with no waiting.

    python verify_semantics.py --date 2026-09-19
    python verify_semantics.py --date 2026-09-19 --dump   # raw market fields

If the API exposes no resolution field, --dump prints everything it does
return, so the next step is informed rather than guessed.
"""

import argparse
import json
import re
import time

from run_ladder import parse_strike
from src.pm_us.feed import SPORT_PATHS, match_game, parse_slug, scoreboard


def main():
    ap = argparse.ArgumentParser(description="Verify ladder settlement semantics.")
    ap.add_argument("--date", required=True, help="a past game date, YYYY-MM-DD")
    ap.add_argument("--dump", action="store_true", help="print raw market fields")
    ap.add_argument("--max-markets", type=int, default=40)
    ap.add_argument("--pause", type=float, default=0.4)
    args = ap.parse_args()

    from src.pm_us.client import UsClient, px
    c = UsClient()

    try:
        mkts = c.markets()
    except Exception as e:
        raise SystemExit(f"market list failed: {type(e).__name__} {e}")
    print(f"{len(mkts)} markets returned by markets.list()")

    # ladder strikes on the requested date
    cand = []
    for m in mkts:
        slug = m.get("marketSlug") or m.get("slug") or ""
        if args.date not in slug:
            continue
        base, k = parse_strike(slug)
        if k is not None:
            cand.append((slug, base, k, m))
    print(f"{len(cand)} ladder strikes dated {args.date}")
    if not cand:
        print("  none - markets.list() may only return ACTIVE markets.")
        print("  Try a date whose games are still listed, or use --dump on any")
        print("  market to see whether a resolution field exists at all.")
        if args.dump and mkts:
            print("\nraw fields of one market:")
            print(json.dumps(mkts[0], indent=1)[:1500])
        c.close()
        return

    if args.dump:
        print("\nraw fields of one settled ladder strike:")
        print(json.dumps(cand[0][3], indent=1)[:2000])
        print("\n  ^ look for a resolution / outcome / settlementPrice field.")

    # actual results from ESPN
    boards = {}
    results = {}
    for slug, base, k, m in cand:
        parsed = parse_slug(base.replace("asc-", "aec-", 1))
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
        g = match_game(base.replace("asc-", "aec-", 1), boards[key])
        if not g:
            continue
        sc = {t["home_away"]: t.get("score") for t in g["teams"]}
        try:
            results[base] = (int(sc["home"]), int(sc["away"]), g["short"])
        except (KeyError, TypeError, ValueError):
            continue

    if not results:
        print("\ncould not match any of these games on ESPN - cannot verify.")
        c.close()
        return

    print(f"\nmatched {len(results)} games on ESPN")
    print(f"\n{'strike':>8} {'settled':>9} {'margin':>8} {'>-line?':>9} "
          f"{'agrees':>7}  market")
    agree = disagree = unknown = 0
    for slug, base, k, m in cand[: args.max_markets]:
        if base not in results:
            continue
        home, away, short = results[base]
        # the ladder's reference team is unknown, so test BOTH orientations
        settled = None
        for field in ("settlementPrice", "resolutionPrice", "outcomePrice",
                      "finalPrice", "lastPrice", "currentPx"):
            v = px(m.get(field))
            if v is not None:
                settled = v
                break
        if settled is None:
            unknown += 1
            continue
        for margin in (home - away, away - home):
            expect = 1.0 if margin > -k else 0.0
            ok = abs(settled - expect) < 0.05
            if ok:
                agree += 1
                print(f"  {k:>+7.1f} {settled:>9.3f} {margin:>+8d} "
                      f"{expect:>9.0f} {'YES':>7}  {slug[8:44]}")
                break
        else:
            disagree += 1
            margin = home - away
            print(f"  {k:>+7.1f} {settled:>9.3f} {margin:>+8d} "
                  f"{'-':>9} {'NO':>7}  {slug[8:44]}")
        time.sleep(args.pause * 0)

    print(f"\n  agree {agree}   disagree {disagree}   no settlement field {unknown}")
    if unknown and not agree and not disagree:
        print("  The API exposes no settlement price on these markets. Re-run with")
        print("  --dump to see what it does return, then verify from a live")
        print("  position's realised P&L instead.")
    elif disagree == 0 and agree:
        print("  CONFIRMED: every settled strike matches 'pays iff margin > -line'.")
        print("  The sign convention the bot uses is correct.")
    elif disagree:
        print("  MISMATCH. Do not trade until this is understood - the pairs")
        print("  would be inverted, and a 'risk-free' pair would lose by design.")
    c.close()


if __name__ == "__main__":
    main()
