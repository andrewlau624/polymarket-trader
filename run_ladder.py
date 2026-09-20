"""Trade the SHAPE of a spread ladder, not the outcome of the game.

SEMANTICS, derived from the live ladder and NOT from venue docs (confirm them):
a strike written `pos-7pt5` is the team RECEIVING 7.5 points; `neg-7pt5` is the
team GIVING 7.5. Writing the line as a signed L (pos -> +L, neg -> -L), the
contract pays 1 iff

    margin + L > 0        i.e.   margin > -L

so the price is NON-DECREASING in L: covering +5.5 is strictly easier than
covering +1.5. An earlier version of this file assumed `P(margin > k)` and
therefore reported the ladder's normal upward shape as nine risk-free
arbitrages. It printed negative implied probabilities and I shipped it anyway.
`--self-test` now pins the direction against real quotes.

Two tradeable structures, neither of which needs a view on who wins:

  1. MONOTONICITY. For L1 < L2, covering L2 is easier, so P(L2) >= P(L1). If
     bid(L1) > ask(L2) you can sell the harder leg and buy the easier one for a
     credit whose worst case is zero. Checked on EXECUTABLE prices, never mids:
     a mid-based check invents arbitrage out of a wide spread.

  2. KEY NUMBERS. Adjacent lines isolate an exact margin: the pair (L1, L2)
     pays iff -L2 < margin <= -L1. Football margins spike on 3 and 7 (NFL:
     14.5% on 3 against 2.9% smooth), so a ladder priced off a smooth curve
     misprices them. This only means anything once the ladder is monotone -
     an inconsistent ladder produces garbage implied point masses.

    python run_ladder.py --self-test
    python run_ladder.py --list
    python run_ladder.py --slug-prefix asc-cfb-clmsn-cah-2026-09-25

SIZE MATTERS. A 4c edge on 2 shares is 8 cents. Depth at the touch is printed;
read it before getting excited.
"""

import argparse
import os
import re
import time
from collections import defaultdict

import pandas as pd

STRIKE = re.compile(r"^(?P<base>.+?)-(?P<sign>pos|neg)-(?P<num>\d+)(?:pt(?P<frac>\d+)?)?$")
SCORES = os.path.join("data", "scores_{league}.csv")


def parse_strike(slug):
    """('asc-cfb-clmsn-cah-2026-09-25', +3.5) from '…-pos-3pt5'."""
    m = STRIKE.match(slug or "")
    if not m:
        return None, None
    num = float(m.group("num"))
    frac = m.group("frac")
    if frac:
        num += float(f"0.{frac}")
    return m.group("base"), (num if m.group("sign") == "pos" else -num)


def empirical_pmf(league):
    path = SCORES.format(league=league)
    if not os.path.exists(path):
        return None
    m = pd.read_csv(path)["margin"].abs()
    m = m[m > 0]
    return (m.value_counts() / len(m)).sort_index(), len(m)


def main():
    ap = argparse.ArgumentParser(description="Spread-ladder shape analysis.")
    ap.add_argument("--slug-prefix", default="", help="one game's ladder")
    ap.add_argument("--list", action="store_true", help="list games that have a ladder")
    ap.add_argument("--league", default="cfb", choices=("cfb", "nfl"))
    ap.add_argument("--min-strikes", type=int, default=3)
    ap.add_argument("--cost", type=float, default=0.02, help="round-trip, both legs")
    ap.add_argument("--pause", type=float, default=0.6,
                    help="seconds between book calls; 0.25 still hit RateLimitError")
    ap.add_argument("--near", type=int, default=0,
                    help="only fetch the N strikes closest to a pick'em. Violations "
                         "cluster there and it cuts the call count by 3x.")
    ap.add_argument("--scan-all", action="store_true",
                    help="sweep every game's ladder and rank by lockable dollars")
    ap.add_argument("--max-games", type=int, default=12)
    args = ap.parse_args()

    from src.pm_us.client import UsClient   # only the live path needs the SDK
    c = UsClient()
    try:
        progs = c.all_programs()
    except Exception as e:
        raise SystemExit(f"program list failed: {type(e).__name__} {e}")

    ladders = defaultdict(dict)
    for p in progs:
        base, k = parse_strike(p.get("slug"))
        if base is not None:
            ladders[base][k] = p["slug"]

    if args.list or not args.slug_prefix:
        print(f"{len(ladders)} games expose a spread ladder:")
        for base, ks in sorted(ladders.items(), key=lambda kv: -len(kv[1])):
            if len(ks) >= args.min_strikes:
                print(f"  {len(ks):>3} strikes  {base}   "
                      f"{sorted(ks)[:8]}{' …' if len(ks) > 8 else ''}")
        if not args.slug_prefix:
            print("\npass --slug-prefix <base> to analyse one.")
            return

    if args.scan_all:
        return scan_all(c, ladders, args)

    ks = ladders.get(args.slug_prefix)
    if not ks:
        raise SystemExit(f"no ladder found for {args.slug_prefix!r} (try --list)")

    print(f"\n{args.slug_prefix}: {len(ks)} strikes. EXECUTABLE prices; "
          f"pays iff margin > -line.")
    want = sorted(ks)
    if args.near:
        want = sorted(sorted(want, key=lambda k: abs(k))[: args.near])
    quotes = {}
    for i, k in enumerate(want):
        bids = asks = None
        for attempt in range(4):        # the SDK gives up after its own 4 tries
            try:
                bids, asks, _state = c.book_levels(ks[k])
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  {k:>+7.1f}  book failed: {type(e).__name__}")
                time.sleep(1.5 * (2 ** attempt))
        if bids is None and asks is None:
            continue
        b = (bids[0][0], bids[0][1]) if bids else (None, 0)
        a = (asks[0][0], asks[0][1]) if asks else (None, 0)
        quotes[k] = {"bid": b[0], "bid_sz": b[1], "ask": a[0], "ask_sz": a[1]}
        sp = (a[0] - b[0]) if (b[0] is not None and a[0] is not None) else float("nan")
        print(f"  {k:>+7.1f}  bid {str(b[0]):>6} x{b[1]:>7.0f}   "
              f"ask {str(a[0]):>6} x{a[1]:>7.0f}   spread {sp:.3f}")
        time.sleep(args.pause)

    report(quotes, args.league, args.cost)


def report(quotes, league, cost):
    ks = sorted(quotes)
    print("\n== MONOTONICITY (executable; worst case zero) ==")
    found = []
    for i, l1 in enumerate(ks):
        b1 = quotes[l1]["bid"]
        if b1 is None or quotes[l1]["bid_sz"] <= 0:
            continue
        for l2 in ks[i + 1:]:
            a2 = quotes[l2]["ask"]
            if a2 is None or quotes[l2]["ask_sz"] <= 0:
                continue
            credit = b1 - a2
            if credit > 1e-9:
                sz = min(quotes[l1]["bid_sz"], quotes[l2]["ask_sz"])
                found.append((credit, l1, l2, sz))
    for credit, l1, l2, sz in sorted(found, reverse=True):
        print(f"  sell {l1:+.1f} @ {quotes[l1]['bid']:.3f}  buy {l2:+.1f} @ "
              f"{quotes[l2]['ask']:.3f}  credit {credit:+.3f} x {sz:.0f} shares "
              f"= ${credit * sz:.2f} locked")
    if not found:
        print("  none - the ladder is internally consistent on executable prices.")
    else:
        print(f"  {len(found)} violations. Covering the higher line is strictly")
        print(f"  easier, so the pair can never lose. Both legs must fill.")

    pmf = empirical_pmf(league)
    if pmf is None:
        print(f"\n(no data/scores_{league}.csv - run fetch_scores.py)")
        return
    dist, n = pmf
    # the market's own win probability, from the line nearest zero, to split
    # P(|margin| = k) into the two signed sides
    near = min(ks, key=lambda k: abs(k))
    pw = quotes[near]["bid"], quotes[near]["ask"]
    p_win = (pw[0] + pw[1]) / 2 if None not in pw else 0.5
    print(f"\n== KEY NUMBERS (vs {n:,} {league.upper()} finals; "
          f"P(win)~{p_win:.2f} from the {near:+.1f} line) ==")
    if found:
        print("  WARNING: the ladder is not monotone. Implied point masses in the")
        print("  inconsistent region are meaningless - fix or ignore those first.")
    print(f"  {'margin':>7} {'implied':>9} {'actual':>8} {'edge':>8} {'net':>8}  action")
    shown = 0
    for l1, l2 in zip(ks[:-1], ks[1:]):
        span = [m for m in range(int(-l2) - 2, int(-l1) + 3) if -l2 < m <= -l1]
        if len(span) != 1:
            continue
        m = span[0]
        p1, p2 = quotes[l1], quotes[l2]
        if None in (p1["bid"], p1["ask"], p2["bid"], p2["ask"]):
            continue
        implied = ((p2["bid"] + p2["ask"]) / 2) - ((p1["bid"] + p1["ask"]) / 2)
        two_sided = float(dist.get(abs(m), 0.0))
        actual = two_sided * (p_win if m > 0 else (1.0 - p_win))
        edge = actual - implied
        net = abs(edge) - cost
        if net <= 0:
            continue
        shown += 1
        print(f"  {m:>7} {implied:>9.3f} {actual:>8.3f} {edge:>+8.3f} {net:>+8.3f}  "
              f"{'BUY' if edge > 0 else 'SELL'} the {l1:+.1f}/{l2:+.1f} vertical")
    if not shown:
        print("  no adjacent pair isolates one margin with an edge over cost.")
    print("\n  actual splits P(|margin|=k) by the market's win probability, which")
    print("  assumes the conditional split equals the unconditional one. Crude for")
    print("  a heavy favourite; fine near a pick'em.")


def violations(quotes):
    """(credit, low line, high line, shares) for every executable violation."""
    out, ks = [], sorted(quotes)
    for i, l1 in enumerate(ks):
        q1 = quotes[l1]
        if q1["bid"] is None or q1["bid_sz"] <= 0:
            continue
        for l2 in ks[i + 1:]:
            q2 = quotes[l2]
            if q2["ask"] is None or q2["ask_sz"] <= 0:
                continue
            credit = q1["bid"] - q2["ask"]
            if credit > 1e-9:
                out.append((credit, l1, l2, min(q1["bid_sz"], q2["ask_sz"])))
    return sorted(out, reverse=True)


def scan_all(c, ladders, args):
    """Sweep the slate. The question is aggregate capacity, not per-game edge."""
    games = [(b, ks) for b, ks in ladders.items() if len(ks) >= args.min_strikes]
    games.sort(key=lambda kv: -len(kv[1]))
    games = games[: args.max_games]
    n_calls = sum(min(len(ks), args.near or len(ks)) for _b, ks in games)
    print(f"sweeping {len(games)} ladders, ~{n_calls} book calls at {args.pause}s "
          f"= ~{n_calls * args.pause / 60:.1f} min\n")
    total = 0.0
    rows = []
    for base, ks in games:
        want = sorted(ks)
        if args.near:
            want = sorted(sorted(want, key=lambda k: abs(k))[: args.near])
        q = {}
        for k in want:
            for attempt in range(4):
                try:
                    bids, asks, _s = c.book_levels(ks[k])
                    q[k] = {"bid": bids[0][0] if bids else None,
                            "bid_sz": bids[0][1] if bids else 0,
                            "ask": asks[0][0] if asks else None,
                            "ask_sz": asks[0][1] if asks else 0}
                    break
                except Exception:
                    time.sleep(1.5 * (2 ** attempt))
            time.sleep(args.pause)
        v = violations(q)
        lock = sum(cr * sz for cr, _a, _b, sz in v)
        total += lock
        rows.append((lock, base, len(v), len(q)))
        print(f"  ${lock:>6.2f}  {len(v):>3} violations  {len(q):>3}/{len(want)} strikes "
              f"read   {base}")
    print(f"\n  TOTAL LOCKABLE ACROSS {len(games)} LADDERS: ${total:.2f}")
    print(f"  Against a ${9:.0f} account that is the number that matters. This edge")
    print(f"  is uncapturable at size, which is exactly why it is still here -")
    print(f"  and a small account is the only kind that can take all of it.")
    return rows


FIXTURE = {   # real clmsn-cah quotes, 2026-09-19
 -20.5: (0.03, 0.035), -17.5: (0.03, 0.035), -16.5: (0.10, 0.105),
 -14.5: (0.03, 0.12), -13.5: (0.215, 0.22), -6.5: (0.365, 0.37),
 -2.5: (0.495, 0.50), -1.5: (0.595, 0.60), -0.5: (0.58, 0.585),
 0.5: (0.55, 0.555), 1.5: (0.605, 0.61), 5.5: (0.565, 0.57),
 6.5: (0.66, 0.665), 7.5: (0.825, 0.83), 14.5: (0.985, 0.99),
 20.5: (0.985, 0.99)}


def self_test():
    """The ladder must read as non-decreasing in the line, and find 6 arbs."""
    q = {k: {"bid": v[0], "ask": v[1], "bid_sz": 100, "ask_sz": 100}
         for k, v in FIXTURE.items()}
    ks = sorted(q)
    mids = [(q[k]["bid"] + q[k]["ask"]) / 2 for k in ks]
    inc = sum(1 for a, b in zip(mids[:-1], mids[1:]) if b >= a - 1e-9)
    print(f"non-decreasing steps {inc}/{len(ks) - 1} "
          f"(must be a large majority, or the sign convention is inverted)")
    assert inc >= (len(ks) - 1) * 0.7, "ladder is not increasing - check semantics"
    n = 0
    for i, l1 in enumerate(ks):
        for l2 in ks[i + 1:]:
            if q[l1]["bid"] - q[l2]["ask"] > 1e-9:
                n += 1
    print(f"executable violations found: {n} (expected 6 on this fixture)")
    assert n == 6, f"expected 6, got {n}"
    report(q, "cfb", 0.02)
    print("\nself-test OK")


if __name__ == "__main__":
    import sys as _s
    if "--self-test" in _s.argv:
        self_test()
    else:
        main()
