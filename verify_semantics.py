"""Read the venue's OWN settlement rules instead of inferring them.

markets.list() returns a `description` field containing the resolution rules in
plain English, plus `marketSides` with each side's team and its settled `price`.
That is documentation, and this project spent a long time inferring around it -
deriving the spread convention from the shape of a price curve and getting the
sign wrong once already.

This finds a SPREAD market, prints its description verbatim, and shows each
side with its team and price. The description states what the line means; the
sides say which team it is written from.

    python verify_semantics.py                 # hunt for a spread market
    python verify_semantics.py --slug <slug>   # a specific one
    python verify_semantics.py --settled       # settled ones, with prices

markets.list() returned only 20 markets unfiltered, so this pages through and
tries several filter spellings rather than assuming one.
"""

import argparse
import json
import time


def fetch(c, **params):
    try:
        return c.markets(**params)
    except Exception as e:
        print(f"  markets({params}) -> {type(e).__name__}: {str(e)[:90]}")
        return []


def show(m, verbose=True):
    slug = m.get("marketSlug") or m.get("slug") or "?"
    print(f"\n{'=' * 74}")
    print(f"slug          {slug}")
    print(f"question      {m.get('question')}")
    print(f"marketType    {m.get('marketType')} / {m.get('sportsMarketType')}")
    print(f"closed        {m.get('closed')}   active {m.get('active')}")
    for k in ("line", "spread", "handicap", "points", "threshold", "value"):
        if m.get(k) is not None:
            print(f"{k:<13} {m.get(k)}")
    desc = m.get("description") or ""
    if desc:
        print("\n--- DESCRIPTION (the venue's own settlement rule) ---")
        print(desc[:1400])
        print("--- end ---")
    sides = m.get("marketSides") or []
    if sides:
        print(f"\nmarketSides ({len(sides)}):")
        for sd in sides:
            team = (sd.get("team") or {})
            print(f"  price {str(sd.get('price')):>7}  long={sd.get('long')}  "
                  f"{str(sd.get('description'))[:22]:<22} "
                  f"team={team.get('safeName') or team.get('name') or '-'}")
    if verbose and not desc:
        print(json.dumps(m, indent=1)[:1200])


def main():
    ap = argparse.ArgumentParser(description="Read the venue's settlement rules.")
    ap.add_argument("--slug", default="")
    ap.add_argument("--settled", action="store_true")
    ap.add_argument("--pages", type=int, default=12)
    ap.add_argument("--date", default="", help="kept for compatibility; ignored")
    ap.add_argument("--dump", action="store_true", help="kept for compatibility")
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()

    if args.slug:
        for params in ({"slug": args.slug}, {"market_slug": args.slug},
                       {"slugs": args.slug}, {"marketSlug": args.slug}):
            got = fetch(c, **params)
            hit = [m for m in got
                   if (m.get("marketSlug") or m.get("slug")) == args.slug]
            if hit:
                show(hit[0])
                c.close()
                return
            time.sleep(0.3)
        print(f"could not fetch {args.slug} by any filter spelling; paging instead")

    # page through, collecting anything that is not a moneyline
    seen, spreads, token = {}, [], None
    for page in range(args.pages):
        params = {"limit": 100}
        if token:
            params["page_token"] = token
        got = fetch(c, **params)
        if not got:
            break
        for m in got:
            sl = m.get("marketSlug") or m.get("slug")
            if sl and sl not in seen:
                seen[sl] = m
                mt = f"{m.get('marketType')} {m.get('sportsMarketType')}".lower()
                if "spread" in mt or "handicap" in mt or (sl or "").startswith("asc-"):
                    spreads.append(m)
        token = None
        time.sleep(0.3)
        if len(got) < 100:
            break

    from collections import Counter
    types = Counter(f"{m.get('marketType')}/{m.get('sportsMarketType')}"
                    for m in seen.values())
    print(f"\n{len(seen)} distinct markets seen. types: "
          + ", ".join(f"{k}({v})" for k, v in types.most_common(8)))

    if not spreads:
        print("\nNo spread market in the listing. Pass one explicitly:")
        print("  make verify VSLUG=asc-cfb-clmsn-cah-2026-09-25-neg-0pt5")
        if seen:
            print("\nshowing a moneyline for reference (its rule is unambiguous):")
            show(next(iter(seen.values())))
        c.close()
        return

    pool = [m for m in spreads if m.get("closed")] if args.settled else spreads
    for m in (pool or spreads)[:3]:
        show(m)
    print("\nRead the DESCRIPTION above. It states what the line means, which is")
    print("the one thing this project has been inferring from price shape.")
    c.close()


if __name__ == "__main__":
    main()
