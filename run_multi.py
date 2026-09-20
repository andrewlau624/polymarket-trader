"""Find multi-outcome (negative-risk) events and test the bundle constraint.

The previous exhaustive check grouped markets by a `team-team-date` slug
pattern, so it could only ever see two-sided games - and duly reported that no
group had more than one leg. That was a statement about the regex, not the
venue. Any event whose outcomes are NOT two teams (a field of candidates, a
range of values, the cpoc-ussec family) was invisible to it.

Multi-outcome events carry the strongest constraint in prediction markets:

    mutually exclusive and exhaustive  =>  sum of YES prices == 1

    sum(ask_i) < 1  ->  buy every leg, collect exactly $1 at settlement
    sum(bid_i) > 1  ->  sell every leg, pay out exactly $1

Polymarket calls these negative-risk markets because a complete set of NO
positions merges back into collateral. That mechanic is what makes the bundle
settleable rather than merely notional.

This groups by the venue's OWN event metadata rather than by slug shape, so it
sees whatever structure actually exists.

    python run_multi.py            # group, then test every group
    python run_multi.py --list     # just the groups
"""

import argparse
import collections
import re
import time

STRIKE = re.compile(r"-(?:pos|neg)-\d+(?:pt\d*)?$")


def event_key(m, slug):
    """Best available grouping key: explicit event metadata, else a slug stem."""
    for field in ("eventSlug", "event_slug", "eventId", "event_id", "groupItemTitle",
                  "negRiskMarketID", "negRiskMarketId", "conditionId"):
        v = m.get(field)
        if v:
            return f"{field}:{v}"
    ev = m.get("events")
    if isinstance(ev, list) and ev:
        e = ev[0]
        if isinstance(e, dict) and (e.get("slug") or e.get("id")):
            return f"event:{e.get('slug') or e.get('id')}"
    # fall back to the slug with any trailing outcome token removed
    parts = (slug or "").split("-")
    return "stem:" + "-".join(parts[:-1]) if len(parts) > 3 else "stem:" + (slug or "")


def main():
    ap = argparse.ArgumentParser(description="Multi-outcome bundle arbitrage.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cost", type=float, default=0.01)
    ap.add_argument("--min-legs", type=int, default=3)
    ap.add_argument("--max-groups", type=int, default=25)
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
        time.sleep(0.25)
    print(f"{len(rows)} markets with metadata")

    # what grouping fields does this venue even expose?
    seen_fields = collections.Counter()
    for m in rows.values():
        for k in m:
            if any(w in k.lower() for w in ("event", "group", "negrisk", "series")):
                seen_fields[k] += 1
    print("grouping-ish fields present: "
          + (", ".join(f"{k}({v})" for k, v in seen_fields.most_common(8)) or "none"))

    groups = collections.defaultdict(list)
    for sl, m in rows.items():
        if STRIKE.search(sl):
            continue                      # ladder strikes are a different trade
        groups[event_key(m, sl)].append(sl)
    multi = {k: v for k, v in groups.items() if len(v) >= args.min_legs}
    print(f"{len(groups)} groups, {len(multi)} with >= {args.min_legs} legs\n")

    if not multi:
        print("No multi-outcome event on this venue. Every market is its own")
        print("two-sided contract, so the bundle constraint has nothing to bind.")
        c.close()
        return
    for k, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  {len(v):>3} legs  {k[:44]}")
        for sl in v[:4]:
            print(f"        {sl[:62]}")
    if args.list:
        c.close()
        return

    print(f"\n{'sum(ask)':>9} {'sum(bid)':>9} {'legs':>5}  group")
    found = 0
    for k, slugs in sorted(multi.items(), key=lambda kv: -len(kv[1]))[: args.max_groups]:
        asks, bids, sizes = [], [], []
        ok = True
        for sl in slugs:
            try:
                b, a, _s = c.book_levels(sl)
            except Exception:
                ok = False
                break
            if not b or not a:
                ok = False
                break
            bids.append(b[0][0]); asks.append(a[0][0])
            sizes.append(min(b[0][1], a[0][1]))
            time.sleep(args.pause)
        if not ok:
            continue
        sa, sb = sum(asks), sum(bids)
        flag = ""
        if sa < 1.0 - args.cost:
            flag = f"  <- BUY ALL, locks {1 - sa:.3f} x {min(sizes):.0f}"
            found += 1
        elif sb > 1.0 + args.cost:
            flag = f"  <- SELL ALL, locks {sb - 1:.3f} x {min(sizes):.0f}"
            found += 1
        print(f"{sa:>9.3f} {sb:>9.3f} {len(slugs):>5}  {k[:34]}{flag}")
    print(f"\n  {found} bundles violate the sum by more than {args.cost}.")
    c.close()


if __name__ == "__main__":
    main()
