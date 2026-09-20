"""Populate data/pm_trades for a category, so calibration can be run on it.

The cached tape arrived as ~77% esports, which meant the calibration result
was an esports result being read as a general one. This fills the gap for
whatever category you actually trade.

    python fetch_tape.py --category Sports --max-events 250
    python fetch_tape.py --category Crypto --max-events 150

Tapes land in data/pm_trades (fetch_trades caches by conditionId), so
label_tape.py and run_calibration.py --by-category pick them up next run.
"""

import argparse
import time

from src.pm.history import CATEGORY_TAGS, binary_markets, events_by_tag, fetch_trades, is_match_event


def main():
    ap = argparse.ArgumentParser(description="Cache the trade tape for a category.")
    ap.add_argument("--category", default="Sports", choices=sorted(CATEGORY_TAGS))
    ap.add_argument("--max-events", type=int, default=250)
    ap.add_argument("--max-markets-per-event", type=int, default=2)
    ap.add_argument("--match-only", action="store_true",
                    help="only per-match events (fast resolving), not season futures")
    ap.add_argument("--pause", type=float, default=0.2)
    args = ap.parse_args()

    tag = CATEGORY_TAGS[args.category]
    print(f"{args.category} (tag {tag}): listing up to {args.max_events} closed events…")
    evs = events_by_tag(tag, closed=True, max_events=args.max_events)
    if args.match_only:
        evs = [e for e in evs if is_match_event(e)]
    print(f"  {len(evs)} events")

    got = skipped = 0
    for i, ev in enumerate(evs, 1):
        for m in binary_markets(ev)[: args.max_markets_per_event]:
            cid = m.get("conditionId")
            if not cid:
                continue
            try:
                t = fetch_trades(cid)
                got += 1 if t is not None and len(t) else 0
            except Exception as e:
                skipped += 1
                print(f"\n  {cid[:12]}: {type(e).__name__} {str(e)[:50]}")
            time.sleep(args.pause)
        print(f"  event {i}/{len(evs)}  tapes {got}  skipped {skipped}",
              end="\r", flush=True)
    print(f"\ndone: {got} tapes cached, {skipped} skipped")
    print("next:  python label_tape.py  &&  python run_calibration.py --by-category")


if __name__ == "__main__":
    main()
