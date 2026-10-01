"""Find reward-paying markets where one side of the book is SHORT of Target Size.

    .venv/bin/python hunt/reward_gaps.py <incentives.json> [--max N]

Polymarket US scores each side every second, but a side with less than Target
Size resting pays nobody (docs.polymarket.us/incentives/liquidity). If we bring
that side to Target Size we are paid that side's half of the pool for every second
it holds. The cost is the collateral:
  - a bid at p ties up p per share;
  - an ask (selling the long side) at p ties up 1 - p per share.
So an empty bid side near 0.1c, or an empty ask side near 99.9c, is cheap.

Only per-day pools are considered (period 'daily' / 'daily_event'). Payouts
under $1 per market per day are not paid, so those are flagged.
"""

import json
import sys
import time
from collections import Counter

sys.path.insert(0, ".")
from xvenue_recorder import PM, get                                  # noqa: E402

TICK = 0.001


def levels(rows):
    out = []
    for x in rows or []:
        px = x.get("px")
        out.append((float(px["value"] if isinstance(px, dict) else px), float(x["qty"])))
    return out


def side_gap(lv, best_first, tgt, df, ask):
    """(depth within the walk, our extra shares, our price, collateral, our score share)."""
    depth = sum(q for _, q in lv)
    need = max(tgt - depth, 0.0)
    if lv:
        price = lv[0][0]                   # join the best price: no ticks of discount
    else:
        price = 0.999 if ask else TICK     # empty side: post at the extreme
    if need == 0:
        # side already qualifies: estimate share of a token 10% of target at the touch
        need = 0.1 * tgt
        others = sum(q * df ** round(abs(lv[0][0] - p) / 0.01) for p, q in lv) if lv else 0.0
        share = need / (need + others)
    else:
        share_others = sum(q * df ** round(abs(price - p) / 0.01) for p, q in lv)
        share = need / (need + share_others)
    coll = need * ((1 - price) if ask else price)
    return depth, need, price, coll, share


def main():
    rows = json.load(open(sys.argv[1]))
    cap = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 400
    n = Counter(r["programId"] for r in rows)
    daily = [r for r in rows if r["period"] in ("daily", "daily_event") and r.get("state", "").endswith("OPEN")]
    daily.sort(key=lambda r: -float(r["rewardPool"]) / n[r["programId"]])
    out = []
    for r in daily[:cap]:
        b = get(f"{PM}/markets/{r['slug']}/book") or {}
        time.sleep(0.25)
        d = b.get("marketData", b)
        if d.get("state") != "MARKET_STATE_OPEN":
            continue
        bids = sorted(levels(d.get("bids")), reverse=True)
        asks = sorted(levels(d.get("offers")))
        tgt, df = float(r["targetSize"]), float(r["discountFactor"])
        per_day = float(r["rewardPool"]) / n[r["programId"]]
        for side, lv, ask in (("bid", bids, False), ("ask", asks, True)):
            depth, need, price, coll, share = side_gap(lv, True, tgt, df, ask)
            earn = per_day / 2 * share
            out.append({"slug": r["slug"], "prog": r["programId"], "side": side, "per_day": per_day,
                        "target": tgt, "maxSpread": r.get("maxSpread"), "depth": round(depth),
                        "add": round(need), "px": price, "collateral": round(coll, 2),
                        "earn_day": round(earn, 3), "roi_day": earn / coll if coll > 0 else 0.0,
                        "short_of_target": depth < tgt})
    json.dump(out, open("research/reward_gaps.json", "w"), indent=1)
    good = [x for x in out if x["short_of_target"] and x["earn_day"] >= 1.0]
    good.sort(key=lambda x: -x["roi_day"])
    print(f"{len(out)} sides scanned; short of target and >= $1/day for us: {len(good)}")
    for x in good[:40]:
        print(f"  {x['slug'][:44]:<44} {x['side']} depth {x['depth']:>6}/{x['target']:<6.0f} add {x['add']:>6} @ {x['px']:.3f} "
              f"collateral ${x['collateral']:>8.2f} earn ${x['earn_day']:>6.2f}/day ({x['roi_day']:.1%}/day) "
              f"maxSpread {x['maxSpread']} [{x['prog'][:28]}]")


if __name__ == "__main__":
    main()
