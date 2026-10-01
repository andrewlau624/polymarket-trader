"""M2: fill the EMPTY side of daily reward markets on Polymarket US (TEST_PLAN.md).

    .venv/bin/python hunt/reward_bot.py                 # dry run: print the plan
    .venv/bin/python hunt/reward_bot.py --live          # place post-only orders (keys needed)
    .venv/bin/python hunt/reward_bot.py --earnings      # what the venue says we earned

A side of a reward market earns nothing in a second where it holds less than
Target Size, and with no Max Spread each side is scored alone. So a side nobody
quotes is the cheapest reward on the venue: a bid at 0.1c for Target Size ties up
0.001 x size and is the only order the side has. If someone sells into it we own
a longshot at 0.1c; the collateral is the most we can lose.

Ledger: research/reward_bot.jsonl (one line per order placed or skipped).
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from xvenue_recorder import PM, get                                  # noqa: E402

GATEWAY = "https://gateway.polymarket.us/v1"
LEDGER = os.path.join("research", "reward_bot.jsonl")
MIN_PAY = 1.10            # $/day modelled for our side; the venue pays nothing under $1
MAX_PER_MARKET = 25.0     # collateral
MAX_TOTAL = 100.0
MIN_DAYS_LEFT = 5
EMPTY_FRAC = 0.10         # side holds < 10% of Target Size
EXTREME_BID, EXTREME_ASK = 0.001, 0.999


def programmes():
    rows, tok = [], None
    while True:
        q = {"statuses": "active"}
        if tok:
            q["page_token"] = tok
        d = get(f"{GATEWAY}/incentives", q) or {}
        for m in d.get("programs", []):
            for t in m.get("timePeriods") or []:
                rows.append({"slug": m["marketSlug"], "state": m.get("instrumentState"), **t})
        tok = d.get("nextPageToken")
        if not tok:
            return rows
        time.sleep(0.25)


def levels(rows):
    out = []
    for x in rows or []:
        px = x.get("px")
        out.append((float(px["value"] if isinstance(px, dict) else px), float(x["qty"])))
    return out


def candidates(progs):
    """Daily, no Max Spread, open, and the per-market half-pool clears MIN_PAY."""
    n = Counter(r["programId"] for r in progs)
    out = []
    for r in progs:
        if r.get("period") not in ("daily", "daily_event") or r.get("maxSpread") is not None:
            continue
        if not str(r.get("state", "")).endswith("OPEN") or r.get("status") != "active":
            continue
        half = float(r["rewardPool"]) / n[r["programId"]] / 2
        if half >= MIN_PAY:
            out.append({**r, "half": half, "target": float(r["targetSize"])})
    return out


TICK = 0.001
MIN_SHARE = 0.90


def plan_side(book, target, half, df=0.3):
    """Orders that complete an (almost) empty side at an extreme price, or [].

    Our order scores qty x df^(ticks behind the side's best price); anyone already
    resting scores the same way from their own price. We only post where our
    share of the side would be >= MIN_SHARE."""
    d = book.get("marketData", book)
    if d.get("state") != "MARKET_STATE_OPEN":
        return []
    out = []
    for side, lv, px in (("buy", levels(d.get("bids")), EXTREME_BID), ("sell", levels(d.get("offers")), EXTREME_ASK)):
        depth = sum(q for _, q in lv)
        if depth >= EMPTY_FRAC * target:
            continue
        if side == "buy" and lv and max(p for p, _ in lv) > 0.01:
            continue                       # someone bids above 1c: not an empty side
        if side == "sell" and lv and min(p for p, _ in lv) < 0.99:
            continue
        qty = int(target - depth) + 1
        best = max([px] + [p for p, _ in lv]) if side == "buy" else min([px] + [p for p, _ in lv])
        ours = qty * df ** round(abs(best - px) / TICK)
        theirs = sum(q * df ** round(abs(best - p) / TICK) for p, q in lv)
        share = ours / (ours + theirs) if ours + theirs else 0.0
        if share < MIN_SHARE:
            continue
        coll = qty * (px if side == "buy" else 1 - px)
        out.append({"side": side, "price": px, "qty": qty, "collateral": round(coll, 2),
                    "pay_day": round(half * share, 3)})
    return out


def slug_date_passed(slug, today=None):
    """True if the slug names a date (YYYY-MM-DD or MM-DD-YYYY) that is already past:
    the event has happened and the market can close any time."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    for y, mo, d in re.findall(r"(20\d\d)-(\d\d)-(\d\d)", slug):
        if f"{y}-{mo}-{d}" < today:
            return True
    for mo, d, y in re.findall(r"(\d\d)-(\d\d)-(20\d\d)", slug):
        if f"{y}-{mo}-{d}" < today:
            return True
    return False


def days_left(slug):
    m = (get(f"{GATEWAY}/market/slug/{slug}") or {}).get("market") or {}
    if m.get("closed") or not m.get("active", True):
        return -1
    try:
        end = datetime.fromisoformat(m["endDate"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return -1
    return (end - datetime.now(timezone.utc)).total_seconds() / 86400


def build_plan(say=print, budget=MAX_TOTAL):
    progs = programmes()
    cands = candidates(progs)
    say(f"{len(progs)} active reward periods; {len(cands)} daily, no Max Spread, half-pool >= ${MIN_PAY}")
    plan, total = [], 0.0
    for r in sorted(cands, key=lambda r: -r["half"]):
        book = get(f"{PM}/markets/{r['slug']}/book") or {}
        time.sleep(0.25)
        for o in plan_side(book, r["target"], r["half"], float(r.get("discountFactor") or 0.3)):
            if o["collateral"] > MAX_PER_MARKET or total + o["collateral"] > budget:
                continue
            if slug_date_passed(r["slug"]):
                continue
            dl = days_left(r["slug"])
            time.sleep(0.25)
            if dl < MIN_DAYS_LEFT:
                continue
            total += o["collateral"]
            plan.append({"slug": r["slug"], "program": r["programId"], "days_left": round(dl, 1), **o})
    plan.sort(key=lambda o: -o["pay_day"] / max(o["collateral"], 0.01))
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--earnings", action="store_true")
    ap.add_argument("--budget", type=float, default=MAX_TOTAL, help="total collateral, $")
    a = ap.parse_args(argv)
    if a.earnings:
        from src.pm_us.client import UsClient
        print(json.dumps(UsClient().earnings(), indent=1, default=str)[:6000])
        return 0
    plan = build_plan(budget=min(a.budget, MAX_TOTAL))
    pay = sum(o["pay_day"] for o in plan)
    coll = sum(o["collateral"] for o in plan)
    print(f"\nplan: {len(plan)} orders, collateral ${coll:.2f}, modelled ${pay:.2f}/day if nobody joins")
    for o in plan:
        print(f"  {o['side']:<4} {o['qty']:>6} @ {o['price']:.3f}  {o['slug'][:46]:<46} "
              f"collateral ${o['collateral']:>6.2f}  ${o['pay_day']:.2f}/day  {o['days_left']}d left  [{o['program'][:30]}]")
    if not a.live:
        print("\ndry run: nothing placed. --live places these as post-only GTC orders.")
        return 0
    from src.pm_us.client import UsClient
    c = UsClient()
    have = {(o.get("marketSlug"), (o.get("intent") or "")) for o in c.open_orders()}
    with open(LEDGER, "a") as fh:
        for o in plan:
            rec = {"t": time.time(), **o}
            if any(s == o["slug"] for s, _ in have):
                rec["result"] = "skipped: we already have an open order here"
            else:
                try:
                    rec["result"] = c.place(o["slug"], o["side"], o["price"], o["qty"], maker=True)
                except Exception as e:
                    rec["result"] = f"error {type(e).__name__}: {str(e)[:200]}"
            fh.write(json.dumps(rec, default=str) + "\n")
            print(f"  {o['slug'][:46]}: {str(rec['result'])[:120]}")
            time.sleep(0.3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
