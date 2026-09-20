"""Test the Bo3 probability bounds against the cached esports tape.

Two relations, neither of which assumes a model:

  PRE-SERIES   |a-b|  <=  P(total>2.5)  <=  min(a+b, 2-a-b)
  POST-MAP-1   P(total>2.5) + P(map-1 winner takes map 2) = 1   (exact)

A market outside those is mispriced by the axioms, not by an opinion.

    python run_bo3.py
    python run_bo3.py --bar 30 --cost 0.02

TWO BUGS THIS FILE EXISTS TO NOT REPEAT:

  * Esports legs are TEAM NAMES, not Yes/No, and their order is not consistent
    across markets in one event (map1 ['Shifters','Karmine Corp'], map2
    ['Karmine Corp','Shifters']). Taking outcomes[0] compares two different
    teams and manufactures impossible violations. Every series is now anchored
    on the team named first in the question.
  * Requiring a trade in every market in the same bar gave 17 observations from
    257 events. These books do not print simultaneously; prices are now
    forward-filled onto a shared grid.

The tape carries TRADE prices, not bid/ask. A violation here is evidence the
relation breaks, not proof it is executable.
"""

import argparse
import collections
import json
import os

from src.esports.series import (grid, leg_series, load_tape, pick_leg,
                                resolution, role_of, series_format,
                                split_bounds, teams_of)

META = os.path.join("data", "pm_market_meta.json")


def events():
    import label_tape
    meta = json.load(open(META))
    ev = collections.defaultdict(dict)
    for cid, m in meta.items():
        if label_tape.classify(f"{m['question']} {m['event']} {m['event_slug']}") != "Esports":
            continue
        key = m["event_slug"] or m["event"]
        if not key:
            continue
        ev[key].setdefault("_q", []).append(m["question"])
        ev[key][role_of(m["question"])] = cid
    return ev


def main():
    ap = argparse.ArgumentParser(description="Bo3 bound violations on the tape.")
    ap.add_argument("--bar", type=int, default=60)
    ap.add_argument("--cost", type=float, default=0.02)
    ap.add_argument("--max-events", type=int, default=600)
    ap.add_argument("--max-stale", type=int, default=300,
                    help="seconds a price may be carried forward. Unlimited "
                         "ffill counts one stale print as dozens of violations.")
    args = ap.parse_args()

    ev = events()
    usable = {k: v for k, v in ev.items() if "total" in v and "map1" in v and "map2" in v}
    print(f"{len(ev)} esports events | {len(usable)} have map1 + map2 + total")

    pre_n = pre_bad = post_n = post_bad = 0
    pre_worst, post_worst = [], []
    pre_ev, post_ev = collections.Counter(), collections.Counter()
    skipped = collections.Counter()
    ev_seen = set()

    for key, roles in list(usable.items())[: args.max_events]:
        qs = roles.get("_q", [])
        ref = None
        for q in qs:
            t1, _t2 = teams_of(q)
            if t1:
                ref = t1
                break
        if not ref:
            skipped["no team in question"] += 1
            continue
        tapes = {r: load_tape(roles[r]) for r in ("map1", "map2", "total")}
        if any(v is None for v in tapes.values()):
            skipped["missing tape"] += 1
            continue
        legs = {}
        for r in ("map1", "map2"):
            outs = list(tapes[r]["outcome"].dropna().unique())
            legs[r] = pick_leg(outs, ref)
        outs_t = list(tapes["total"]["outcome"].dropna().unique())
        legs["total"] = pick_leg(outs_t, "Over")
        if any(v is None for v in legs.values()):
            skipped["leg not found"] += 1
            continue

        g = grid({r: leg_series(tapes[r], legs[r], args.bar) for r in tapes},
                 args.bar, require=("map1", "map2", "total"),
                 max_stale_s=args.max_stale)
        if g is None or len(g) < 3:
            skipped["thin leg or no overlap"] += 1
            continue
        m1_res = resolution(tapes["map1"], legs["map1"])
        ev_seen.add(key)

        for _ts, row in g.iterrows():
            a, b, o = float(row["map1"]), float(row["map2"]), float(row["total"])
            if not all(0.01 < x < 0.99 for x in (a, b, o)):
                continue
            if m1_res is not None and (a > 0.97 or a < 0.03):
                p_win2 = b if a > 0.5 else (1.0 - b)
                gap = (o + p_win2) - 1.0
                post_n += 1
                if abs(gap) > args.cost:
                    post_bad += 1
                    post_ev[key] = max(post_ev[key], abs(gap) - args.cost)
                    post_worst.append((abs(gap) - args.cost, key, o, p_win2))
            else:
                lo, hi = split_bounds(a, b)
                pre_n += 1
                excess = max(lo - o, o - hi)
                if excess > args.cost:
                    pre_bad += 1
                    pre_ev[key] = max(pre_ev[key], excess - args.cost)
                    pre_worst.append((excess - args.cost, key, a, b, o, lo, hi))

    print(f"events analysed: {len(ev_seen)}   skipped: {dict(skipped)}")
    print(f"\n== PRE-SERIES BOUND ==")
    print(f"  {pre_n:,} obs, {pre_bad:,} violating bars "
          f"({pre_bad / max(pre_n, 1):.1%})")
    print(f"  {len(pre_ev)} of {len(ev_seen)} EVENTS violate - bars are "
          f"forward-filled and not independent, so this is the real count")
    for k, e in sorted(pre_ev.items(), key=lambda kv: -kv[1])[:5]:
        print(f"    +{e:.3f}  {k[:46]}")
    print(f"\n== POST-MAP-1 IDENTITY ==")
    print(f"  {post_n:,} obs, {post_bad:,} violating bars "
          f"({post_bad / max(post_n, 1):.1%})")
    print(f"  {len(post_ev)} EVENTS violate")
    for k, e in sorted(post_ev.items(), key=lambda kv: -kv[1])[:5]:
        print(f"    +{e:.3f}  {k[:46]}")
    print(f"\n  Trade prints, not bid/ask.")


if __name__ == "__main__":
    main()
