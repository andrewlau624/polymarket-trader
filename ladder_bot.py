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
import sys
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
            _bids, asks, _s = c.book_levels(p["slug"])
        except Exception:
            time.sleep(1.0)
            continue
        if asks and asks[0][0] is not None and asks[0][0] < 0.90:
            cand = (p["slug"], asks[0][0])
            break
        time.sleep(0.4)
    if not cand:
        raise SystemExit("no market with a live ask to probe against")
    slug, ask = cand
    # RESTING, not marketable. The first version sold AT THE BID with
    # maker=False, which crosses and fills by construction - so "cancelling it
    # immediately" was impossible and the probe left two naked shorts behind.
    # Price it well above the ask, post-only, so it sits unfilled: acceptance
    # answers the question, and a resting order can actually be cancelled.
    px = min(round(ask + 0.08, 3), 0.99)
    print(f"  placing a RESTING sell of 1 share of {slug}")
    print(f"  at {px:.3f}, which is {px - ask:.3f} above the ask {ask:.3f}, post-only.")
    print("  It should not fill. If the venue accepts it, shorting is permitted.")
    if not args.yes:
        raise SystemExit("  re-run with --yes to actually send the probe.")
    try:
        o = c.place(slug, "sell", px, 1, maker=True)
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
        print("  Check `make account`: if a short position appeared, it filled "
              "anyway and needs closing with `make flatten-live`.")
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"  REJECTED: {msg}")
        print("  -> no short leg, so the paired arbitrage is NOT executable here.")
        print("     The ladder mispricing is real but unreachable without "
              "inventory in the leg you need to sell.")
        log({"kind": "probe", "result": "rejected", "slug": slug, "error": msg})


def past_hits(path=LOG):
    """How many opportunities each ladder has produced before.

    Twelve of fifteen durable violations sat on ONE ladder while the bot swept
    twenty-five evenly. Scanning where the violations have been is worth more
    than breadth: a full sweep takes ~15 minutes, and a violation can be taken
    by someone else in that time.
    """
    hits = {}
    if not os.path.exists(path):
        return hits
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "opportunity" and r.get("game"):
            hits[r["game"]] = hits.get(r["game"], 0) + 1
    return hits


def game_date(base):
    """'2026-09-25' from a ladder base slug, or None.

    NOT anchored to the end: sub-period ladders put the date mid-slug
    (`...-2026-09-19-4q`). An anchored pattern returned None for exactly the
    markets that settle in hours, so --max-days silently excluded the
    fastest-turnover books on the venue.
    """
    import re as _re
    m = _re.search(r"(\d{4}-\d{2}-\d{2})", base)
    return m.group(1) if m else None


def sub_period(base):
    """'4q', '2h', ... for a sub-period ladder, else None. These settle at the
    end of their period rather than the game, so capital recycles fastest."""
    import re as _re
    m = _re.search(r"-(1h|2h|1q|2q|3q|4q)(?:-|$)", base)
    return m.group(1) if m else None


def days_to_settle(base, today=None):
    """Days until this market resolves. Same-day counts as 0.25 (a few hours),
    a sub-period ladder as 0.12 - capital turnover is what frequency means."""
    from datetime import date
    d = game_date(base)
    if not d:
        return 7.0
    today = today or date.today()
    try:
        y, m, dd = (int(x) for x in d.split("-"))
    except ValueError:
        return 7.0
    gap = (date(y, m, dd) - today).days
    if gap > 0:
        return float(gap)
    return 0.12 if sub_period(base) else 0.25


def within_days(base, max_days, today=None):
    """Is this game inside max_days of today? 0/None means no limit."""
    if not max_days:
        return True
    from datetime import date, timedelta
    d = game_date(base)
    if not d:
        return False
    today = today or date.today()
    try:
        y, m, dd = (int(x) for x in d.split("-"))
    except ValueError:
        return False
    return date(y, m, dd) <= today + timedelta(days=max_days)


def rank_games(ladders, hits, rank="turnover"):
    """Productive ladders first, then near-dated ones, then the rest.

    The richest credit seen (+0.060) was on a game dated the same day, so
    ladders close to kickoff are worth more attention than ones a week out.
    """
    import re as _re

    def by_value(item):
        base, ks = item
        return (-hits.get(base, 0), game_date(base) or "9999-99-99", -len(ks))

    def by_turnover(item):
        # soonest settlement first: a 0.015 credit resolving tonight returns
        # 6%/day against 0.8%/day for a 0.040 credit resolving Thursday
        base, ks = item
        return (days_to_settle(base), -hits.get(base, 0), -len(ks))

    return sorted(ladders.items(),
                  key=by_turnover if rank == "turnover" else by_value)


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


def capital_per_share(sell_px, buy_px):
    """Dollars tied up by one share of the pair.

    The long leg costs buy_px. The short leg can settle at 1, so it needs
    (1 - sell_px) of collateral. Total = buy_px + 1 - sell_px = 1 - credit.
    So a pair ties up about a dollar to lock its credit, and the return on
    capital is credit / (1 - credit) - roughly 4% on a 0.04 credit, held
    until the game settles.

    This assumes the venue does NOT net the two legs. If it recognises the
    spread the real requirement is lower and more pairs fit; that is the
    optimistic case, so budget for this one.
    """
    return max(buy_px + (1.0 - sell_px), 0.01)


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
    state["deployed"] += capital_per_share(args.sell_px, args.buy_px) * size
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
                    help="DOLLARS of collateral to deploy in total. Each share of "
                         "a pair ties up about (1 - credit), so ~$1, until the game "
                         "settles. Set it from your actual free cash: this is the "
                         "binding constraint, not the number of violations.")
    ap.add_argument("--min-credit", type=float, default=0.01,
                    help="skip violations thinner than this")
    ap.add_argument("--min-size", type=int, default=1)
    ap.add_argument("--max-size", type=int, default=25,
                    help="per-pair share cap for LIVE trading. A dry run ignores "
                         "it unless given explicitly, so measured depth is real "
                         "depth and not this number echoed back.")
    ap.add_argument("--near", type=int, default=12,
                    help="strikes nearest a pick'em to scan; violations cluster there")
    ap.add_argument("--min-strikes", type=int, default=6)
    ap.add_argument("--pause", type=float, default=0.6, help="seconds between book calls")
    ap.add_argument("--cycle-min", type=float, default=20.0,
                    help="minutes between full sweeps of the slate")
    ap.add_argument("--max-games", type=int, default=25)
    ap.add_argument("--rank", default="turnover", choices=("turnover", "value"),
                    help="turnover = soonest settlement first, which maximises "
                         "return per day of locked capital. value = biggest "
                         "credit first, which maximises the one-off take.")
    ap.add_argument("--max-days", type=int, default=0,
                    help="only ladders whose game settles within N days (0 = all). "
                         "Use --max-days 1 for a first live trial: the pair settles "
                         "tonight instead of next weekend, so the settlement "
                         "semantics get confirmed in hours rather than a week.")
    ap.add_argument("--cost", type=float, default=0.02, help="assumed unwind cost")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    args.sell_px = args.buy_px = args.unwind_px = None  # filled per-violation
    # Nothing is placed in a dry run, so it must reveal REAL depth. Every
    # limit left in the sizing path gets echoed back as if it were a
    # measurement: --max-capital made every violation log exactly 5 shares,
    # and then --max-size made three of them log exactly 25. Third time, so
    # lift BOTH unless the operator set them on purpose.
    if not args.live:
        if "--max-capital" not in sys.argv:
            args.max_capital = 10_000.0
        if "--max-size" not in sys.argv:
            args.max_size = 100_000

    if not os.environ.get("POLYMARKET_US_KEY_ID"):
        raise SystemExit("set POLYMARKET_US_KEY_ID / POLYMARKET_US_SECRET_KEY")
    from src.pm_us.client import UsClient
    c = UsClient()

    if args.probe:
        probe(c, args)
        c.close()
        return

    lock = os.path.join("research", "ladder_bot.lock")
    os.makedirs(os.path.dirname(lock) or ".", exist_ok=True)
    if os.path.exists(lock):
        old_pid, old_mode = None, "?"
        try:
            parts = open(lock).read().strip().split()
            old_pid = int(parts[0])
            old_mode = parts[1] if len(parts) > 1 else "?"
            os.kill(old_pid, 0)
        except (ValueError, IndexError, ProcessLookupError, PermissionError):
            old_pid = None
        if old_pid:
            raise SystemExit(
                f"a {old_mode} ladder_bot already holds the lock (pid {old_pid}).\n"
                f"Two instances share one rate limit and halve each other's "
                f"coverage.\n"
                + (f"That one places no orders, so stopping it costs nothing:\n"
                   f"  make ladder-kill && make ladder-trial\n"
                   f"and restart it afterwards with `make ladder-bg`.\n"
                   if old_mode == "dry" else
                   f"Stop it with `make ladder-kill`.\n")
                + f"If pid {old_pid} is stale, remove {lock}.")
    open(lock, "w").write(f"{os.getpid()} {'live' if args.live else 'dry'}")

    mode = "LIVE" if args.live else "dry run"
    state = load_state()
    print(f"ladder bot | {mode} | cap ${args.max_capital:.2f} | "
          f"min credit {args.min_credit:.3f} | sweep every {args.cycle_min:.0f}min")
    print(f"  a pair ties up ~$1/share until settlement, so ${args.max_capital:.2f} "
          f"funds roughly {int(args.max_capital):d} share-pairs.")
    print(f"  at a typical 0.03 credit that is about "
          f"${args.max_capital * 0.03:.2f} locked per cycle of capital.")
    if args.live:
        print("!! --live but the short leg has not been proven. Run --probe first "
              "if you have not.")

    sweep = 0
    try:
        while True:
            t0 = time.time()
            sweep += 1
            try:
                ladders, sports = inventory(c, args.min_strikes)
            except Exception as e:
                print(f"inventory failed: {type(e).__name__} {e}; retrying")
                time.sleep(30)
                continue
            print(f"\n[{now()[:19]}] {len(ladders)} ladders | sports: "
                  + ", ".join(f"{k}({v})" for k, v in sorted(sports.items(),
                                                             key=lambda kv: -kv[1])))
            hits = past_hits()
            if args.max_days:
                ladders = {b: k for b, k in ladders.items()
                           if within_days(b, args.max_days)}
                print(f"  {len(ladders)} ladders settle within {args.max_days}d")
            games = rank_games(ladders, hits, args.rank)[: args.max_games]
            if hits:
                top = [g for g, _k in games[:3]]
                print(f"  prioritising: {', '.join(t[8:38] for t in top)}")
            found = locked = 0.0
            for base, ks in games:
                want = sorted(sorted(ks, key=lambda k: abs(k))[: args.near])
                q = quotes_for(c, ks, want, args.pause)
                # violations() is sorted best-credit-first, which matters:
                # capital is scarce, so it must not be spent on 0.005 edges
                # before a 0.04 one later in the same sweep.
                room = args.max_capital - state["deployed"]
                for credit, l1, l2, sz in violations(q):
                    if credit < args.min_credit or sz < args.min_size:
                        continue
                    # as capital runs out, demand a better edge for what is left
                    used = state["deployed"] / max(args.max_capital, 1e-9)
                    if credit < args.min_credit * (1.0 + 3.0 * used):
                        continue
                    if sz >= args.max_size and args.live:
                        print(f"    (size {sz} hit --max-size {args.max_size}; "
                              f"real depth may be larger)")
                    per_share = capital_per_share(q[l1]["bid"], q[l2]["ask"])
                    afford = int(max(room, 0) / per_share)
                    size = int(min(sz, args.max_size, afford))
                    if size < args.min_size:
                        continue
                    found += credit * size
                    d = days_to_settle(base)
                    per_day = (credit * size) / max(d, 0.01)
                    cap = per_share * size
                    line = (f"  {base[:32]:<32} sell {l1:+.1f} buy {l2:+.1f} "
                            f"credit {credit:+.3f} x{size} = ${credit * size:.2f}"
                            f"  | {d:.2f}d  ${per_day:.3f}/day  "
                            f"{per_day / max(cap, 0.01):.1%}/day")
                    if not args.live:
                        # spend the budget in simulation too, or every violation
                        # is sized as if it had the whole cap to itself and the
                        # sweep total becomes fiction
                        state["deployed"] += per_share * size
                        room -= per_share * size
                        print(line + "   [dry run]")
                        log({"kind": "opportunity", "game": base, "sell": l1,
                             "buy": l2, "credit": round(credit, 4), "size": size,
                             "value": round(credit * size, 4),
                             "sell_bid": q[l1]["bid"], "buy_ask": q[l2]["ask"],
                             "sweep": sweep})
                        continue
                    args.sell_px = q[l1]["bid"]
                    args.buy_px = q[l2]["ask"]
                    args.unwind_px = q[l1]["ask"] or (q[l1]["bid"] + args.cost)
                    got = execute_pair(c, ks, credit, l1, l2, size, state, args)
                    print(line + ("   [PAIRED]" if got else "   [failed]"))
                    locked += got
                    save_state(state)
            mins = (time.time() - t0) / 60
            print(f"  sweep {sweep} done in {mins:.1f}min | "
                  f"opportunity ${found:.2f} | locked ${locked:.2f} | "
                  f"lifetime ${state['realized']:.2f} "
                  f"(unwind cost ${state['unwind_cost']:.2f})")
            log({"kind": "sweep", "sweep": sweep, "ladders": len(ladders),
                 "games_scanned": len(games), "opportunity": round(found, 4),
                 "locked": round(locked, 4), "minutes": round(mins, 2),
                 "sports": sports, "live": bool(args.live)})
            if not args.live:
                state["deployed"] = 0.0      # simulated spend, reset each sweep
            if args.once:
                break
            time.sleep(max(0.0, args.cycle_min * 60 - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        save_state(state)
        try:
            os.remove(lock)
        except OSError:
            pass
        c.close()


if __name__ == "__main__":
    main()
