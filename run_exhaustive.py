"""Event-exhaustive arbitrage: the only structure left on outright-only sports.

The venue's ladder family is CFB alone. NBA (222 markets), CBB (130) and NHL
(51) are outrights only, so there is no strike ladder to order and the
monotonicity trade has nothing to work with.

One structure survives without a ladder. When a set of markets covers mutually
exclusive and exhaustive outcomes of the same event - every team's championship,
every outcome of one game - their prices must sum to exactly 1. Under, buy them
all for less than the $1 that is certain to arrive. Over, sell them all.

    sum(ask_i) < 1  ->  buy every leg, collect 1 at settlement
    sum(bid_i) > 1  ->  sell every leg, pay out 1

The graveyard buried this - on the GLOBAL venue, which was liquid and
arbitraged. This venue is neither, its ladders are internally inconsistent by
6-11 cents, and it has never been checked here.

    python run_exhaustive.py            # find candidate groups, test them
    python run_exhaustive.py --list     # just show the groups
"""

import argparse
import collections
import re
import time

# aec-nba-bos-nyk-2026-11-04 -> the two sides of one game
GAME_RE = re.compile(r"^(?P<fam>[a-z]+-[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)"
                     r"-(?P<date>\d{4}-\d{2}-\d{2})$")


def group_key(slug):
    m = GAME_RE.match(slug or "")
    if not m:
        return None
    return f"{m.group('fam')}-{m.group('date')}-" + "-".join(sorted(
        (m.group("a"), m.group("b"))))


def main():
    ap = argparse.ArgumentParser(description="Event-exhaustive sum check.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cost", type=float, default=0.01)
    ap.add_argument("--max-groups", type=int, default=40)
    ap.add_argument("--pause", type=float, default=0.5)
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()
    rows = {}
    for params in ({}, {"limit": 500}, {"limit": 1000}):
        try:
            for m in c.markets(**params):
                sl = m.get("marketSlug") or m.get("slug")
                if sl:
                    rows[sl] = m
        except Exception:
            pass
        time.sleep(0.3)
    try:
        for p in c.all_programs():
            if p.get("slug"):
                rows.setdefault(p["slug"], {})
    except Exception:
        pass
    print(f"{len(rows)} markets")

    groups = collections.defaultdict(list)
    for sl in rows:
        k = group_key(sl)
        if k:
            groups[k].append(sl)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"{len(groups)} game groups, {len(multi)} with more than one market")
    if args.list or not multi:
        for k, v in list(multi.items())[:25]:
            print(f"  {k[:44]:<44} {len(v)} legs")
        if not multi:
            print("\n  No group has multiple legs, so every game is a single")
            print("  two-sided market and there is no exhaustive set to sum.")
            print("  On this venue that structure does not exist either.")
        c.close()
        return

    print(f"\n{'sum(ask)':>9} {'sum(bid)':>9} {'legs':>5}  group")
    found = 0
    for k, slugs in list(multi.items())[: args.max_groups]:
        asks, bids, sizes = [], [], []
        for sl in slugs:
            try:
                b, a, _s = c.book_levels(sl)
            except Exception:
                asks = []
                break
            if not b or not a:
                asks = []
                break
            bids.append(b[0][0]); asks.append(a[0][0])
            sizes.append(min(b[0][1], a[0][1]))
            time.sleep(args.pause)
        if not asks:
            continue
        sa, sb = sum(asks), sum(bids)
        flag = ""
        if sa < 1.0 - args.cost:
            flag = f"  <- BUY ALL, locks {1 - sa:.3f}/set x {min(sizes):.0f}"
            found += 1
        elif sb > 1.0 + args.cost:
            flag = f"  <- SELL ALL, locks {sb - 1:.3f}/set x {min(sizes):.0f}"
            found += 1
        print(f"{sa:>9.3f} {sb:>9.3f} {len(slugs):>5}  {k[:38]}{flag}")
    print(f"\n  {found} groups violate the exhaustive sum by more than {args.cost}.")
    c.close()


if __name__ == "__main__":
    main()
