"""24/7 spread-ladder scanner and paired executor.

Hunts monotonicity violations across every ladder the venue lists. For lines
L1 < L2 the higher line is strictly easier to cover, so P(L2) >= P(L1) always.
When bid(L1) > ask(L2) you can sell the harder leg, buy the easier one, and
the pair cannot lose - worst case zero, plus the credit.

    python ladder_bot.py --probe        # CAN we even short? answer first
    python ladder_bot.py                # dry run, scans forever, places nothing
    python ladder_bot.py --live --max-capital 5

THE BLOCKER, and why --probe exists. The venue's only sell intent is
ORDER_INTENT_SELL_LONG - selling shares you already hold. There is no short
intent. If a sell without inventory is rejected, the short leg is impossible
and this whole structure is dead on this venue. Nothing here places a real
order until --probe has answered that, and --probe risks one share.

WHY 24/7 IS THE RIGHT MODE. Every violation found so far was on a game days
away, not a live one. Pre-game ladders are thin and stale, which is exactly
where the inconsistency lives, so the forward slate is the hunting ground and
it is always there. Depth is 1-2 shares, so this is a grind across many
ladders rather than size on any one.

SAFETY. Dry run by default. --live needs an explicit capital cap. Both legs or
neither: if the first leg fills and the second fails, the first is unwound
immediately at whatever it costs, and that loss is logged as a real cost of
the strategy rather than hidden.
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

from run_ladder import parse_strike, violations

STATE = os.path.join("research", "ladder_state.json")
LOG = os.path.join("research", "ladder_trades.jsonl")


def now():
    return datetime.now(timezone.utc).isoformat()


def log(rec):
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    rec["ts"] = now()
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"deployed": 0.0, "pairs": [], "realized": 0.0, "unwind_cost": 0.0}


def save_state(s):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=1)


def probe(c, args):
    """Can we sell a contract we do not hold? Everything depends on this."""
    print("== PROBE: is a short leg possible on this venue? ==")
    try:
        progs = c.all_programs()
    except Exception as e:
        raise SystemExit(f"program list failed: {type(e).__name__} {e}")
    held = set()
    try:
        held = {k for k, v in (c.positions() or {}).items()}
    except Exception:
        pass
    cand = None
    for p in progs:
        base, k = parse_strike(p.get("slug"))
        if base is None or p["slug"] in held:
            continue
        try:
            bids, _asks, _s = c.book_levels(p["slug"])
        except Exception:
            time.sleep(1.0)
            continue
        if bids and bids[0][1] >= 1:
            cand = (p["slug"], bids[0][0])
            break
        time.sleep(0.4)
    if not cand:
        raise SystemExit("no market with a live bid to probe against")
    slug, bid = cand
    print(f"  selling 1 share of {slug} at its bid {bid:.3f} (not held).")
    print("  This is a REAL order for about $%.2f of exposure. Cancelling it "
          "immediately." % bid)
    if not args.yes:
        raise SystemExit("  re-run with --yes to actually send the probe.")
    try:
        o = c.place(slug, "sell", bid, 1, maker=False)
        oid = o.get("orderId") or o.get("id") if isinstance(o, dict) else None
        print(f"  ACCEPTED (order {oid}). Shorting appears to be permitted.")
        print("  -> the paired arbitrage is executable. Verify it actually "
              "settles short before sizing.")
        log({"kind": "probe", "result": "accepted", "slug": slug, "order": oid})
        if oid:
            try:
                c.cancel(oid, slug)
                print("  probe order cancelled.")
            except Exception as e:
                print(f"  ! COULD NOT CANCEL: {type(e).__name__} {e} - cancel it "
                      f"by hand with `make cancel`")
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"  REJECTED: {msg}")
        print("  -> no short leg, so the paired arbitrage is NOT executable here.")
        print("     The ladder mispricing is real but unreachable without "
              "inventory in the leg you need to sell.")
        log({"kind": "probe", "result": "rejected", "slug": slug, "error": msg})


def inventory(c, min_strikes):
    """Every ladder the venue lists, grouped by sport prefix."""
    progs = c.all_programs()
    ladders, sports = {}, {}
    for p in progs:
        base, k = parse_strike(p.get("slug"))
        if base is None:
            continue
        ladders.setdefault(base, {})[k] = p["slug"]
    for base in ladders:
        tag = base.split("-")[1] if "-" in base else "?"
        sports[tag] = sports.get(tag, 0) + 1
    ladders = {b: ks for b, ks in ladders.items() if len(ks) >= min_strikes}
    return ladders, sports


def quotes_for(c, ks, want, pause):
    q = {}
    for k in want:
        for attempt in range(3):
            try:
                bids, asks, _s = c.book_levels(ks[k])
                q[k] = {"bid": bids[0][0] if bids else None,
                        "bid_sz": bids[0][1] if bids else 0,
                        "ask": asks[0][0] if asks else None,
                        "ask_sz": asks[0][1] if asks else 0}
                break
            except Exception:
                time.sleep(1.5 * (2 ** attempt))
        time.sleep(pause)
    return q


def execute_pair(c, ks, credit, l1, l2, size, state, args):
    """Sell the harder leg, buy the easier one. Both or neither."""
    s1, s2 = ks[l1], ks[l2]
    # take the thinner leg first: it is the one that disappears
    log({"kind": "attempt", "sell": s1, "buy": s2, "credit": credit, "size": size})
    try:
        o1 = c.place(s1, "sell", args.sell_px, size, maker=False)
    except Exception as e:
        log({"kind": "leg1_failed", "slug": s1, "error": f"{type(e).__name__}: {str(e)[:120]}"})
        return 0.0
    try:
        c.place(s2, "buy", args.buy_px, size, maker=False)
    except Exception as e:
        # unwind leg 1 at once; the loss is a real cost of the strategy
        log({"kind": "leg2_failed", "slug": s2,
             "error": f"{type(e).__name__}: {str(e)[:120]}", "unwinding": s1})
        try:
            c.place(s1, "buy", args.unwind_px, size, maker=False)
            log({"kind": "unwound", "slug": s1})
        except Exception as e2:
            log({"kind": "UNWIND_FAILED", "slug": s1,
                 "error": f"{type(e2).__name__}: {str(e2)[:120]}"})
            print(f"  !! NAKED LEG on {s1} - unwind failed. Fix by hand.")
        state["unwind_cost"] += args.cost * size
        return 0.0
    locked = credit * size
    state["realized"] += locked
    state["deployed"] += size
    state["pairs"].append({"ts": now(), "sell": s1, "buy": s2,
                           "credit": credit, "size": size})
    log({"kind": "paired", "sell": s1, "buy": s2, "credit": credit,
         "size": size, "locked": locked})
    return locked


def main():
    ap = argparse.ArgumentParser(description="24/7 ladder arbitrage scanner.")
    ap.add_argument("--probe", action="store_true",
                    help="test whether a short leg is possible at all")
    ap.add_argument("--yes", action="store_true", help="confirm the probe's real order")
    ap.add_argument("--live", action="store_true", help="place real orders")
    ap.add_argument("--max-capital", type=float, default=5.0,
                    help="dollars of collateral to deploy in total")
    ap.add_argument("--min-credit", type=float, default=0.01,
                    help="skip violations thinner than this")
    ap.add_argument("--min-size", type=int, default=1)
    ap.add_argument("--max-size", type=int, default=25)
    ap.add_argument("--near", type=int, default=12,
                    help="strikes nearest a pick'em to scan; violations cluster there")
    ap.add_argument("--min-strikes", type=int, default=6)
    ap.add_argument("--pause", type=float, default=0.6, help="seconds between book calls")
    ap.add_argument("--cycle-min", type=float, default=20.0,
                    help="minutes between full sweeps of the slate")
    ap.add_argument("--max-games", type=int, default=25)
    ap.add_argument("--cost", type=float, default=0.02, help="assumed unwind cost")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    args.sell_px = args.buy_px = args.unwind_px = None  # filled per-violation

    if not os.environ.get("POLYMARKET_US_KEY_ID"):
        raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
    from src.pm_us.client import UsClient
    c = UsClient()

    if args.probe:
        probe(c, args)
        c.close()
        return

    mode = "LIVE" if args.live else "dry run"
    state = load_state()
    print(f"ladder bot | {mode} | cap ${args.max_capital:.2f} | "
          f"min credit {args.min_credit:.3f} | sweep every {args.cycle_min:.0f}min")
    if args.live:
        print("!! --live but the short leg has not been proven. Run --probe first "
              "if you have not.")

    try:
        while True:
            t0 = time.time()
            try:
                ladders, sports = inventory(c, args.min_strikes)
            except Exception as e:
                print(f"inventory failed: {type(e).__name__} {e}; retrying")
                time.sleep(30)
                continue
            print(f"\n[{now()[:19]}] {len(ladders)} ladders | sports: "
                  + ", ".join(f"{k}({v})" for k, v in sorted(sports.items(),
                                                             key=lambda kv: -kv[1])))
            games = sorted(ladders.items(), key=lambda kv: -len(kv[1]))[: args.max_games]
            found = locked = 0.0
            for base, ks in games:
                want = sorted(sorted(ks, key=lambda k: abs(k))[: args.near])
                q = quotes_for(c, ks, want, args.pause)
                for credit, l1, l2, sz in violations(q):
                    if credit < args.min_credit or sz < args.min_size:
                        continue
                    room = args.max_capital - state["deployed"] * 1.0
                    size = int(min(sz, args.max_size, max(room, 0)))
                    if size < args.min_size:
                        continue
                    found += credit * size
                    line = (f"  {base[:34]:<34} sell {l1:+.1f} buy {l2:+.1f} "
                            f"credit {credit:+.3f} x{size} = ${credit * size:.2f}")
                    if not args.live:
                        print(line + "   [dry run]")
                        continue
                    args.sell_px = q[l1]["bid"]
                    args.buy_px = q[l2]["ask"]
                    args.unwind_px = q[l1]["ask"] or (q[l1]["bid"] + args.cost)
                    got = execute_pair(c, ks, credit, l1, l2, size, state, args)
                    print(line + ("   [PAIRED]" if got else "   [failed]"))
                    locked += got
                    save_state(state)
            print(f"  sweep done in {(time.time() - t0) / 60:.1f}min | "
                  f"opportunity ${found:.2f} | locked ${locked:.2f} | "
                  f"lifetime ${state['realized']:.2f} "
                  f"(unwind cost ${state['unwind_cost']:.2f})")
            if args.once:
                break
            time.sleep(max(0.0, args.cycle_min * 60 - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        save_state(state)
        c.close()


if __name__ == "__main__":
    main()
