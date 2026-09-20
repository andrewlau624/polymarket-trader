"""Enumerate EVERY market the venue lists, not just the ones paying rewards.

The family inventory so far came from all_programs(), which returns markets
with an ACTIVE LIQUIDITY PROGRAM attached. A market with no reward program
never appears there. So "this venue has no esports" may have been a statement
about the reward programme, not about the venue - and markets() returned only
20 rows unpaginated, which is plainly not the whole book.

This hammers markets.list() with every pagination and filter spelling it might
accept, and reports what actually exists.

    python list_all_markets.py
    python list_all_markets.py --probe   # show which params the API accepted
"""

import argparse
import json
import re
import time
from collections import Counter

PARAM_SETS = [
    {}, {"limit": 500}, {"limit": 1000}, {"page_size": 500}, {"pageSize": 500},
    {"limit": 500, "offset": 0}, {"limit": 500, "status": "active"},
    {"limit": 500, "closed": "false"}, {"limit": 500, "active": "true"},
    {"limit": 500, "sport": "esports"}, {"limit": 500, "league": "lol"},
    {"limit": 500, "category": "esports"}, {"limit": 500, "category": "sports"},
]


def slug_of(m):
    return m.get("marketSlug") or m.get("slug") or ""


def main():
    ap = argparse.ArgumentParser(description="Full market inventory.")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--pages", type=int, default=25)
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()
    seen = {}

    # 1. try each parameter spelling once, keep whatever comes back
    for params in PARAM_SETS:
        try:
            got = c.markets(**params)
        except Exception as e:
            if args.probe:
                print(f"  {params} -> {type(e).__name__}: {str(e)[:70]}")
            continue
        new = sum(1 for m in got if slug_of(m) and slug_of(m) not in seen)
        for m in got:
            if slug_of(m):
                seen[slug_of(m)] = m
        if args.probe:
            print(f"  {params} -> {len(got)} rows, {new} new (total {len(seen)})")
        time.sleep(0.3)

    # 2. page with whatever token the response carries
    token, page = None, 0
    while page < args.pages:
        params = {"limit": 500}
        if token:
            params["page_token"] = token
        try:
            resp = c.c.markets.list(params)
        except Exception:
            break
        rows = resp.get("markets", []) if isinstance(resp, dict) else []
        for m in rows:
            if slug_of(m):
                seen[slug_of(m)] = m
        token = resp.get("nextPageToken") if isinstance(resp, dict) else None
        page += 1
        if not token or not rows:
            break
        time.sleep(0.3)
    c.close()

    print(f"\n{len(seen)} distinct markets found (paged {page} time(s))")
    if not seen:
        raise SystemExit("markets.list() returned nothing usable")

    fam = Counter()
    league = Counter()
    for sl, m in seen.items():
        head = "-".join(sl.split("-")[:2]) if "-" in sl else sl
        fam[head] += 1
        for k in ("league", "sport", "category", "sportsMarketType", "marketType"):
            v = m.get(k)
            if v:
                league[f"{k}={v}"] += 1
    print("\nby slug prefix:")
    for k, n in fam.most_common(20):
        print(f"  {k:<18} {n:>6}")
    print("\nby declared field:")
    for k, n in league.most_common(20):
        print(f"  {k:<34} {n:>6}")

    # anything that smells like esports
    ES = re.compile(r"lol|valorant|csgo|cs2|dota|overwatch|rocket|esport|league-of|"
                    r"starcraft|rainbow|apex|fortnite", re.I)
    hits = [(sl, m) for sl, m in seen.items()
            if ES.search(sl) or ES.search(str(m.get("question") or ""))]
    print(f"\nesports-looking markets: {len(hits)}")
    for sl, m in hits[:15]:
        print(f"  {sl[:52]:<52} {str(m.get('question'))[:40]}")
    if not hits:
        print("  none in this listing.")
        print("  If the site shows esports, this endpoint is not the whole book -")
        print("  paste a market slug from the website and I will fetch it directly.")


if __name__ == "__main__":
    main()
