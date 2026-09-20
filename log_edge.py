"""Record the US book and the ESPN event stream on one clock, risk-free.

This is the zero-capital experiment. It answers two questions that decide
whether anything directional is worth building:

  1. LATENCY - when a scoring play lands, how long until the book reprices?
     Measured against the play's ESPN wallclock. If the book moves BEFORE the
     ESPN API publishes the play, the market is faster than our feed and there
     is no edge available through this feed, however fast our code is. That is
     a real answer, and it costs nothing to get.

  2. DIVERGENCE - how far does the market mid sit from ESPN's own live win
     probability, and does the gap close? An independent model is the cheapest
     directional signal there is, if the venue is slow to agree with it.

Writes one JSONL where every record carries `ts` (epoch seconds, our clock):

    {"kind":"meta",  ...}                       once at startup
    {"kind":"book",  "slug":..., "bid":..., "ask":..., ...}
    {"kind":"play",  "slug":..., "wallclock":..., "home_wp":..., ...}

    python log_edge.py --espn-only            # no API keys needed
    python log_edge.py --minutes 180          # live games, book + events
    python log_edge.py --slugs a,b --minutes 60
"""

import argparse
import json
import os
import threading
import time

from src.pm_us import feed


def _writer(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a", buffering=1)
    lock = threading.Lock()

    def write(rec):
        rec["ts"] = time.time()
        with lock:
            fh.write(json.dumps(rec) + "\n")
    return write, fh


def discover(client, hours=12):
    """US markets whose slug matches a game ESPN has live or starting soon."""
    slugs = []
    try:
        for p in client.all_programs():
            if p.get("slug"):
                slugs.append(p["slug"])
    except Exception as e:
        print(f"program list failed: {type(e).__name__} {e}")
    seen, pairs = set(), []
    boards = {}
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        parsed = feed.parse_slug(slug)
        if not parsed:
            continue
        sport, _teams, date = parsed
        path = feed.SPORT_PATHS.get(sport)
        if not path:
            continue
        key = (path, date.replace("-", ""))
        if key not in boards:
            try:
                boards[key] = feed.scoreboard(path, date=key[1])
            except Exception:
                boards[key] = []
            time.sleep(0.3)
        g = feed.match_game(slug, boards[key])
        if g and g.get("state") in ("in", "pre"):
            pairs.append((slug, g, path))
    return pairs


def main():
    ap = argparse.ArgumentParser(description="Log the US book against ESPN events.")
    ap.add_argument("--slugs", default="", help="comma-separated market slugs (default: discover)")
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--book-interval", type=float, default=1.0, help="seconds between book polls")
    ap.add_argument("--event-interval", type=float, default=5.0, help="seconds between ESPN polls")
    ap.add_argument("--espn-only", action="store_true", help="skip the book (no API keys needed)")
    ap.add_argument("--out", default="research/us_edge_log.jsonl")
    args = ap.parse_args()

    client = None
    if not args.espn_only:
        from src.pm_us.client import UsClient
        if not os.environ.get("POLYMARKET_US_KEY_ID"):
            raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY "
                             "(or pass --espn-only)")
        client = UsClient()

    if args.slugs:
        pairs = []
        boards = {}
        for slug in [s.strip() for s in args.slugs.split(",") if s.strip()]:
            parsed = feed.parse_slug(slug)
            if not parsed:
                print(f"  ! {slug}: unrecognized slug shape, skipped")
                continue
            sport, _t, date = parsed
            path = feed.SPORT_PATHS.get(sport)
            if not path:
                print(f"  ! {slug}: no ESPN sport for '{sport}', skipped")
                continue
            key = (path, date.replace("-", ""))
            boards.setdefault(key, feed.scoreboard(path, date=key[1]))
            g = feed.match_game(slug, boards[key])
            if not g:
                print(f"  ! {slug}: no ESPN game matched, skipped")
                continue
            pairs.append((slug, g, path))
    else:
        if client is None:
            raise SystemExit("--espn-only needs --slugs (there is no venue to discover from)")
        pairs = discover(client)

    if not pairs:
        raise SystemExit("nothing to watch (no live/upcoming game matched a market slug)")

    write, fh = _writer(args.out)
    print(f"watching {len(pairs)} market(s), logging to {args.out}")
    for slug, g, _path in pairs:
        print(f"  {slug[:44]:<44} -> {g['short']} ({g['state']}) event {g['event_id']}")
    write({"kind": "meta", "watch": [{"slug": s, "event_id": g["event_id"],
                                      "short": g["short"], "sport": p}
                                     for s, g, p in pairs],
           "book_interval": args.book_interval, "event_interval": args.event_interval})

    stop = threading.Event()
    deadline = time.time() + args.minutes * 60

    def book_loop():
        while not stop.is_set() and time.time() < deadline:
            t0 = time.time()
            for slug, _g, _p in pairs:
                try:
                    bids, asks, state = client.book_levels(slug)
                except Exception as e:
                    write({"kind": "book_error", "slug": slug,
                           "error": f"{type(e).__name__}: {str(e)[:80]}"})
                    continue
                write({"kind": "book", "slug": slug, "state": state,
                       "bid": bids[0][0] if bids else None,
                       "bid_sz": bids[0][1] if bids else None,
                       "ask": asks[0][0] if asks else None,
                       "ask_sz": asks[0][1] if asks else None,
                       "depth_b": len(bids), "depth_a": len(asks)})
            stop.wait(max(0.0, args.book_interval - (time.time() - t0)))

    def event_loop():
        seen = set()
        while not stop.is_set() and time.time() < deadline:
            t0 = time.time()
            for slug, g, path in pairs:
                try:
                    rows = feed.plays(g["event_id"], path)
                except Exception as e:
                    write({"kind": "play_error", "slug": slug,
                           "error": f"{type(e).__name__}: {str(e)[:80]}"})
                    continue
                for p in rows:
                    key = (g["event_id"], p["play_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    write({"kind": "play", "slug": slug, "event_id": g["event_id"], **p})
            stop.wait(max(0.0, args.event_interval - (time.time() - t0)))

    threads = [threading.Thread(target=event_loop, daemon=True)]
    if client is not None:
        threads.insert(0, threading.Thread(target=book_loop, daemon=True))
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=3)
        fh.close()
        if client is not None:
            client.close()
        print(f"log written to {args.out}")


if __name__ == "__main__":
    main()
