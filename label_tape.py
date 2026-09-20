"""Label each cached trade tape with its market question / event, via gamma.

The tape is keyed only by conditionId, so nothing in data/pm_trades says what
sport or topic a market was. This fetches that once and caches it, which is
what lets run_calibration.py --by-category work.

    python label_tape.py            # fill data/pm_market_meta.json
    python label_tape.py --refresh  # re-fetch everything
"""

import argparse
import glob
import json
import os
import time

import requests

GAMMA = "https://gamma-api.polymarket.com/markets"
TAPE = os.path.join("data", "pm_trades")
META = os.path.join("data", "pm_market_meta.json")

# checked in order; first hit wins
RULES = [
    ("Esports", ("valorant", "league of legends", "lol:", "cs2", "counter-strike",
                 "dota", "overwatch", "rocket league", "esports", "map 1", "map 2",
                 "map 3", "games total")),
    ("Crypto", ("bitcoin", "btc", "ethereum", "eth", "solana", "crypto", "dogecoin",
                "xrp", "stablecoin", "token")),
    ("Politics", ("president", "election", "senate", "congress", "governor",
                  "parliament", "prime minister", "nominee", "impeach", "cabinet",
                  "supreme court", "shutdown")),
    ("Sports", ("nba", "nfl", "mlb", "nhl", "ufc", "premier league", "la liga",
                "champions league", "super bowl", "world cup", "tennis", "golf",
                "f1", "formula 1", "boxing", "serie a", "bundesliga", "free agency",
                "play for the", "vs.", " vs ")),
]


def classify(text):
    t = (text or "").lower()
    for name, keys in RULES:
        if any(k in t for k in keys):
            return name
    return "Other"


def fetch(ids, batch=40, pause=0.25):
    out = {}
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        q = "&".join(f"condition_ids={c}" for c in chunk)
        try:
            r = requests.get(f"{GAMMA}?{q}&closed=true&limit={batch}", timeout=30)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            print(f"  batch {i // batch}: {type(e).__name__} {str(e)[:60]}")
            time.sleep(1.0)
            continue
        for m in rows if isinstance(rows, list) else []:
            cid = m.get("conditionId")
            if not cid:
                continue
            ev = (m.get("events") or [{}])[0]
            out[cid] = {"question": m.get("question") or "",
                        "event": ev.get("title") or "",
                        "event_slug": ev.get("slug") or ""}
        print(f"  {min(i + batch, len(ids))}/{len(ids)} fetched, {len(out)} labelled",
              end="\r", flush=True)
        time.sleep(pause)
    print()
    return out


def main():
    ap = argparse.ArgumentParser(description="Cache gamma metadata for the tape.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--batch", type=int, default=40)
    args = ap.parse_args()

    have = {}
    if os.path.exists(META) and not args.refresh:
        have = json.load(open(META))
    ids = [os.path.basename(f)[:-4] for f in sorted(glob.glob(os.path.join(TAPE, "*.csv")))]
    todo = [c for c in ids if c not in have]
    print(f"{len(ids)} tapes, {len(have)} already labelled, {len(todo)} to fetch")
    if todo:
        have.update(fetch(todo, batch=args.batch))
        json.dump(have, open(META, "w"))
    cats = {}
    for cid, m in have.items():
        c = classify(f"{m['question']} {m['event']} {m['event_slug']}")
        cats[c] = cats.get(c, 0) + 1
    print(f"\nwrote {META} ({len(have)} markets)")
    for c, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {c:<10} {n:>5}")


if __name__ == "__main__":
    main()
