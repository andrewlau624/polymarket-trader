"""Two books, one event: the moneyline against the ladder's zero crossing.

The ladder strike `neg-0pt5` means the team gives away half a point, so it pays
exactly when the team wins outright. The moneyline market `aec-<game>` pays on
the same condition. They are priced in separate order books and must agree.

    P(neg-0.5 covers)  ==  P(moneyline wins)

Unlike the monotonicity trade this needs no ladder structure at all - just two
markets that settle identically. And the graveyard's "spread implies moneyline"
proposals were tested on the GLOBAL venue, never this one, which we have
already shown prices its own ladder inconsistently.

If they disagree by more than the round trip: buy the cheap one, sell the dear
one, and the pair settles to exactly zero difference no matter who wins.

Also prints every market FAMILY the venue lists, so we can see whether totals,
UFC round ladders, or half/quarter ladders exist - each would be the same
monotonicity machinery on a fresh set of books.

    python run_crossmarket.py                # families + moneyline vs ladder
    python run_crossmarket.py --families     # just the inventory, fast
"""

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

LOG = os.path.join("research", "crossmarket.jsonl")


def log(rec):
    """Persist findings. A foreground run that takes minutes must not lose
    everything when the ssh session drops, which is exactly what happened."""
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    rec["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")

from run_ladder import parse_strike

# aec-cfb-clmsn-cah-2026-09-25  <->  asc-cfb-clmsn-cah-2026-09-25-neg-0pt5
FAMILY = re.compile(r"^(?P<fam>[a-z]+)-(?P<sport>[a-z0-9]+)-(?P<rest>.+)$")
SUFFIX = re.compile(r"-(2h|1h|4q|1q|2q|3q)$")


def family_of(slug):
    m = FAMILY.match(slug or "")
    if not m:
        return "?", "?"
    return m.group("fam"), m.group("sport")


def main():
    ap = argparse.ArgumentParser(description="Cross-market consistency checks.")
    ap.add_argument("--families", action="store_true", help="inventory only")
    ap.add_argument("--cost", type=float, default=0.02)
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--max-games", type=int, default=15)
    args = ap.parse_args()

    from src.pm_us.client import UsClient
    c = UsClient()
    progs = c.all_programs()
    slugs = sorted({p["slug"] for p in progs if p.get("slug")})
    print(f"{len(slugs)} distinct markets")

    # ---- what families exist at all -------------------------------------
    fam = Counter()
    sub = Counter()
    for s in slugs:
        f, sp = family_of(s)
        base, k = parse_strike(s)
        fam[(f, sp, "ladder" if k is not None else "outright")] += 1
        m = SUFFIX.search(base or s)
        if m:
            sub[m.group(1)] += 1
    print("\n== MARKET FAMILIES ==")
    for (f, sp, kind), n in fam.most_common():
        print(f"  {f:>4}-{sp:<5} {kind:<9} {n:>6}")
    log({"kind": "families", "markets": len(slugs),
         "families": {f"{f}-{sp}-{kind}": n for (f, sp, kind), n in fam.items()},
         "sub_periods": dict(sub)})
    if sub:
        print("  sub-period ladders: " + ", ".join(f"{k}({v})" for k, v in sub.most_common()))
        print("  ^ thinner books than full-game, so more likely to be inconsistent")
    print("\n  Each 'ladder' row is a set of monotone strikes - the same trade the")
    print("  bot already runs, on a different underlying. Each 'outright' row can")
    print("  be checked against a ladder's zero crossing (below).")
    if args.families:
        c.close()
        return

    # ---- moneyline vs the ladder's zero crossing ------------------------
    ladders = defaultdict(dict)
    outrights = {}
    for s in slugs:
        base, k = parse_strike(s)
        if k is not None:
            ladders[base][k] = s
        else:
            outrights[s] = s

    pairs = []
    for base, ks in ladders.items():
        # the strike nearest zero from the GIVING side wins == outright win
        zero = min((k for k in ks if k < 0), key=abs, default=None)
        if zero is None:
            continue
        ml = base.replace("asc-", "aec-", 1)
        ml = SUFFIX.sub("", ml)
        if ml in outrights:
            pairs.append((base, ks[zero], zero, ml))
    print(f"\n== MONEYLINE vs LADDER ZERO ({len(pairs)} games have both) ==")
    if not pairs:
        print("  no game exposes both an outright and a ladder - nothing to compare.")
        c.close()
        return

    print(f"  {'game':<34} {'strike':>7} {'ladder':>16} {'moneyline':>16} {'edge':>7}")
    found = 0
    for base, lslug, k, ml in pairs[: args.max_games]:
        q = {}
        for name, sl in (("lad", lslug), ("ml", ml)):
            for attempt in range(3):
                try:
                    b, a, _s = c.book_levels(sl)
                    q[name] = (b[0][0] if b else None, b[0][1] if b else 0,
                               a[0][0] if a else None, a[0][1] if a else 0)
                    break
                except Exception:
                    time.sleep(1.5 * (2 ** attempt))
            time.sleep(args.pause)
        if "lad" not in q or "ml" not in q:
            continue
        lb, lbs, la, las = q["lad"]
        mb, mbs, ma, mas = q["ml"]
        if None in (lb, la, mb, ma):
            continue
        # buy the cheaper, sell the dearer; both settle on "team wins"
        e1 = mb - la            # sell moneyline, buy ladder
        e2 = lb - ma            # sell ladder, buy moneyline
        edge = max(e1, e2)
        flag = ""
        if edge > args.cost:
            found += 1
            which = ("sell ML buy ladder" if e1 >= e2 else "sell ladder buy ML")
            sz = (min(mbs, las) if e1 >= e2 else min(lbs, mas))
            flag = f"  <- {which}, {sz:.0f} sh, ${edge * sz:.2f}"
        print(f"  {base[8:42]:<34} {k:>+7.1f} {lb:>7.3f}/{la:<8.3f} "
              f"{mb:>7.3f}/{ma:<8.3f} {edge:>+7.3f}{flag}")
        log({"kind": "pair", "game": base, "strike": k, "moneyline": ml,
             "ladder_bid": lb, "ladder_ask": la, "ml_bid": mb, "ml_ask": ma,
             "edge": round(edge, 4),
             "tradeable": bool(edge > args.cost),
             "shares": (min(mbs, las) if e1 >= e2 else min(lbs, mas)),
             "side": ("sell_ml_buy_ladder" if e1 >= e2 else "sell_ladder_buy_ml")})
    # NOT "tradeable": pair records use that key as a boolean, and a summary
    # carrying a truthy COUNT under the same name broke every reader.
    log({"kind": "summary", "games_checked": len(pairs[: args.max_games]),
         "tradeable_count": found, "cost": args.cost})
    print(f"\n  {found} games priced their outright and their ladder's zero crossing")
    print(f"  more than {args.cost:.3f} apart. Both settle on the same event, so a")
    print(f"  gap is the same kind of free money as a monotonicity violation.")
    print(f"  NOTE: assumes neg-0.5 settles as 'wins outright'. A half-point line")
    print(f"  cannot push, so that should hold - but the trial confirms it.")
    c.close()


if __name__ == "__main__":
    main()
