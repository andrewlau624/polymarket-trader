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
    """Append, rotating past 8MB. Unbounded growth here OOM'd a droplet."""
    from src.pm_us.jsonlog import append
    rec["ts"] = now()
    append(LOG, rec)


def load_state():
    """A corrupt state file is quarantined and fatal, never silently replaced
    by a blank one: a blank state forgets every resting order the bot owns."""
    if not os.path.exists(STATE):
        return {"deployed": 0.0, "pairs": [], "realized": 0.0, "unwind_cost": 0.0}
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (ValueError, OSError) as e:
        bad = f"{STATE}.corrupt-{int(time.time())}"
        os.replace(STATE, bad)
        raise SystemExit(f"state unreadable ({type(e).__name__}); moved to {bad}. "
                         f"Resting orders are still live - check `make money`.")


def save_state(s):
    """Atomic: temp file, fsync, rename. A crash mid-write leaves the old file."""
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    tmp = f"{STATE}.tmp-{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(s, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, STATE)


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
    # Only recent history matters for ranking, and this is called EVERY sweep.
    # Reading the whole file each time is what made the log size a memory
    # problem rather than just a disk one.
    from src.pm_us.jsonlog import tail_records
    hits = {}
    for r in tail_records(path, n=4000):
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


def quotes_for(c, ks, want, pause, throttle=None):
    """Book tops for a set of strikes, paced adaptively.

    The previous version slept a fixed `pause` AND retried with its own
    exponential backoff on top of the client's, so one rate-limited call could
    cost 14 seconds and a 300-call sweep took 15 minutes - 3.0s per call
    against a 0.6s target. AIMD finds the venue's actual tolerance instead.
    """
    from src.pm_us.throttle import Throttle, paced_call
    t = throttle if throttle is not None else Throttle(start=pause)
    q = {}
    for k in want:
        try:
            bids, asks, _s = paced_call(lambda: c.book_levels(ks[k]), t)
        except Exception:
            continue
        q[k] = {"bid": bids[0][0] if bids else None,
                "bid_sz": bids[0][1] if bids else 0,
                "ask": asks[0][0] if asks else None,
                "ask_sz": asks[0][1] if asks else 0}
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


def reconcile(c, state, args):
    """Settle up with the resting pairs from previous runs.

    Maker-first execution means orders sit across cron runs, so every run must
    first ask what happened to the last one. Four outcomes:

      both filled   -> the pair is on, credit banked, done
      one filled    -> a NAKED LEG. Wait while the other order still rests,
                       but unwind once it is stale - an unhedged leg is a
                       directional bet and this whole strategy exists not to
                       hold those.
      neither       -> still working; cancel if stale so capital is released
      vanished      -> treat as cancelled by the venue, release and re-scan
    """
    pending = state.get("pending") or []
    if not pending:
        return
    try:
        live = {str(o.get("orderId") or o.get("id")) for o in (c.open_orders() or [])}
    except Exception as e:
        print(f"  reconcile skipped: cannot read open orders ({type(e).__name__})")
        return
    # Without positions a vanished order cannot be told apart from a filled
    # one, and the old fallback ({}) booked every vanished pair as FILLED.
    try:
        pos = c.positions() or {}
    except Exception as e:
        print(f"  reconcile skipped: cannot read positions ({type(e).__name__})")
        return

    from datetime import datetime, timezone
    still = []
    for p in pending:
        ids = p.get("orders") or {}
        sell_open = str(ids.get(p["sell"])) in live
        buy_open = str(ids.get(p["buy"])) in live
        age_h = 0.0
        try:
            t0 = datetime.fromisoformat(p["ts"])
            age_h = (datetime.now(timezone.utc) - t0).total_seconds() / 3600.0
        except Exception:
            pass

        if not sell_open and not buy_open:
            # Both orders gone. Usually both filled - but cancel_all() from
            # another process also makes them vanish, and booking that as a
            # completed pair would credit profit that never happened. Verify
            # against positions before claiming it.
            net_s = _position_net(pos, p["sell"])
            net_b = _position_net(pos, p["buy"])
            if net_s >= 0 or net_b <= 0:
                print(f"  orders gone but positions do not confirm a fill "
                      f"({p['sell'][:26]}) - treating as CANCELLED, not filled")
                log({"kind": "vanished", "sell": p["sell"], "buy": p["buy"],
                     "net_sell": net_s, "net_buy": net_b})
                state["deployed"] = max(state["deployed"] - p["size"], 0.0)
                continue
            locked = p["credit"] * p["size"]
            state["realized"] += locked
            state.setdefault("pairs", []).append({**p, "closed": now()})
            log({"kind": "paired", "sell": p["sell"], "buy": p["buy"],
                 "credit": p["credit"], "size": p["size"], "locked": locked})
            print(f"  FILLED both legs: {p['sell'][:30]} / {p['buy'][:30]} "
                  f"+${locked:.2f}")
            continue

        if sell_open != buy_open:
            filled = p["buy"] if buy_open else p["sell"]
            resting = p["sell"] if buy_open else p["buy"]
            if age_h < args.stale_hours:
                print(f"  one leg filled ({filled[:34]}), other still resting "
                      f"[{age_h:.1f}h]")
                still.append(p)
                continue
            # stale and half-filled: cancel the straggler, unwind the position
            print(f"  !! stale half-fill after {age_h:.1f}h - unwinding "
                  f"{filled[:34]}")
            oid = ids.get(resting)
            if oid:
                try:
                    c.cancel(oid, resting)
                except Exception:
                    pass
            net = _position_net(pos, filled)
            if net:
                side = "sell" if net > 0 else "buy"
                try:
                    b, a, _s = c.book_levels(filled)
                    px = (b[0][0] if side == "sell" and b else
                          (a[0][0] if a else None))
                    if px:
                        c.place(filled, side, px, int(abs(net)), maker=False)
                        log({"kind": "unwound", "slug": filled, "qty": abs(net)})
                except Exception as e:
                    log({"kind": "UNWIND_FAILED", "slug": filled,
                         "error": f"{type(e).__name__}: {str(e)[:110]}"})
                    print(f"     unwind FAILED on {filled} - fix by hand")
            state["unwind_cost"] += args.cost * p["size"]
            state["deployed"] = max(state["deployed"] - p["size"], 0.0)
            continue

        if age_h >= args.stale_hours:
            for slug, oid in ids.items():
                try:
                    c.cancel(oid, slug)
                except Exception:
                    pass
            state["deployed"] = max(state["deployed"] - p["size"], 0.0)
            log({"kind": "expired", "sell": p["sell"], "buy": p["buy"],
                 "hours": round(age_h, 1)})
            print(f"  cancelled unfilled pair after {age_h:.1f}h")
            continue
        still.append(p)

    state["pending"] = still
    if still:
        print(f"  {len(still)} pair(s) still resting")


def _position_net(pos, slug):
    v = pos.get(slug)
    if not isinstance(v, dict):
        return 0.0
    try:
        return float(v.get("netPosition") or 0)
    except (TypeError, ValueError):
        return 0.0


def execute_pair(c, ks, credit, l1, l2, size, state, args, quotes=None):
    """Rest BOTH legs as maker. Taking them is a losing trade after fees.

    The venue charges takers 0.0695*p*(1-p) and PAYS makers 0.0125*p*(1-p)
    (docs.polymarket.us/fees, effective 2026-09-17). On a two-leg pair at
    typical prices that is 0.0341 a share out versus 0.0061 a share in - a
    0.040 swing, larger than the biggest violation ever observed here. Taking
    both legs made most of our violations NEGATIVE.

    Resting works because the edge is slow: violations stood in 12 of 15
    observations across ten sweeps over hours, so there is time to be filled.
    And resting a PAIR is hedged against level moves by construction - both
    legs sit on the same game - so only relative moves can hurt it. That is
    what polymm lacked when its single-sided quotes were picked off.

    Cron makes the waiting free: place now, reconcile on the next run.
    """
    s1, s2 = ks[l1], ks[l2]
    q1 = (quotes or {}).get(l1, {})
    q2 = (quotes or {}).get(l2, {})
    tick = args.tick

    # Price INSIDE the spread so the order rests and has queue priority, but
    # never crosses - a crossing "maker" order is rejected post-only, or worse,
    # silently becomes a taker.
    sell_px = args.sell_px
    if q1.get("ask") is not None:
        sell_px = max(round(q1["ask"] - tick, 3), (q1.get("bid") or 0) + tick)
    buy_px = args.buy_px
    if q2.get("bid") is not None:
        buy_px = min(round(q2["bid"] + tick, 3),
                     (q2.get("ask") or 1.0) - tick)
    if sell_px <= buy_px:
        return 0.0                      # no room to rest profitably

    resting_credit = sell_px - buy_px
    log({"kind": "attempt", "sell": s1, "buy": s2, "taker_credit": credit,
         "resting_credit": resting_credit, "sell_px": sell_px,
         "buy_px": buy_px, "size": size, "mode": "maker"})
    ids = {}
    for slug, side, px in ((s1, "sell", sell_px), (s2, "buy", buy_px)):
        try:
            o = c.place(slug, side, px, size, maker=True)
            ids[slug] = _oid(o)
        except Exception as e:
            log({"kind": "rest_failed", "slug": slug, "side": side,
                 "error": f"{type(e).__name__}: {str(e)[:120]}"})
            # one leg resting alone is not a position - cancel it and move on
            for done, oid in ids.items():
                try:
                    c.cancel(oid, done)
                except Exception:
                    pass
            return 0.0

    state.setdefault("pending", []).append({
        "ts": now(), "sell": s1, "buy": s2, "sell_px": sell_px,
        "buy_px": buy_px, "size": size, "credit": resting_credit,
        "orders": ids, "game": ks.get("_base", "")})
    state["deployed"] += capital_per_share(sell_px, buy_px) * size
    log({"kind": "resting", "sell": s1, "buy": s2,
         "credit": resting_credit, "size": size})
    return resting_credit * size


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
    ap.add_argument("--min-credit", type=float, default=0.002,
                    help="absolute floor on credit, below which fees and "
                         "rounding dominate. The real gate is --hurdle.")
    ap.add_argument("--hurdle", type=float, default=0.004,
                    help="required RETURN PER DAY of locked capital. A pair "
                         "earns credit c on ~$1 held T days to settlement, so "
                         "the credit needed is hurdle*T. A flat credit "
                         "threshold accepts 0.010 over 7 days (0.0014/day, "
                         "barely above zero) while rejecting 0.002 on a "
                         "quarter ladder (0.0167/day, twelve times better) - "
                         "which is exactly the fast-settling business.")
    ap.add_argument("--min-size", type=int, default=1)
    ap.add_argument("--max-size", type=int, default=25,
                    help="per-pair share cap for LIVE trading. A dry run ignores "
                         "it unless given explicitly, so measured depth is real "
                         "depth and not this number echoed back.")
    ap.add_argument("--near", type=int, default=12,
                    help="strikes nearest a pick'em to scan; violations cluster there")
    ap.add_argument("--min-strikes", type=int, default=2,
                    help="a monotonicity violation needs only TWO rungs - "
                         "bid(L1) > ask(L2) for L1 < L2. The old default of 6 "
                         "skipped small ladders, which is exactly the shape of "
                         "the sub-period (1h/1q/4q) markets that settle in "
                         "hours and are the only intra-day turnover available.")
    ap.add_argument("--pause", type=float, default=0.6, help="seconds between book calls")
    ap.add_argument("--cycle-min", type=float, default=20.0,
                    help="minutes between full sweeps of the slate")
    ap.add_argument("--max-games", type=int, default=0,
                    help="ladders per sweep; 0 = ALL. Breadth is the only thing "
                         "that scales: taking the touch caps at ~20 shares a "
                         "violation and fees make deeper levels negative, so "
                         "return grows with the number of PLACES you rest, not "
                         "the size in any one.")
    ap.add_argument("--base-shares", type=int, default=5,
                    help="size given to every qualifying candidate before any "
                         "surplus goes to the best ones")
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
    ap.add_argument("--tick", type=float, default=0.001,
                    help="venue tick, for pricing inside the spread")
    ap.add_argument("--take", action="store_true",
                    help="cross both legs instead of resting. Pays 0.0695*p*(1-p) "
                         "twice, which makes most observed violations negative. "
                         "Only sensible for a credit above ~0.036.")
    ap.add_argument("--verticals", action="store_true",
                    help="also take positive-EV verticals, not only risk-free "
                         "ones. Payoff is {0,+$1} so a loss is the premium, but "
                         "a half-fill is a naked directional leg.")
    ap.add_argument("--min-ev", type=float, default=0.02,
                    help="minimum EV per share for a vertical BET")
    ap.add_argument("--kelly-fraction", type=float, default=0.25,
                    help="fraction of Kelly for bets. Quarter, because the fair "
                         "probability is estimated and full Kelly on a point "
                         "estimate sizes as though it were certain.")
    ap.add_argument("--stale-hours", type=float, default=12.0,
                    help="cancel a resting pair that has not filled in this long")
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
        proven = None
        if os.path.exists(LOG):
            from src.pm_us.jsonlog import iter_records
            for r in iter_records(LOG):
                if r.get("kind") == "probe":
                    proven = r.get("result")
        if proven == "accepted":
            print("  short leg: PROVEN (a probe was accepted, see the trade log)")
        elif proven == "rejected":
            raise SystemExit("a probe was REJECTED: the short leg is not available, "
                             "so the paired trade cannot be executed here.")
        else:
            print("!! --live but no probe result on record. Run --probe first.")

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
            from src.pm_us.throttle import Throttle
            if not hasattr(main, "_throttle"):
                main._throttle = Throttle(start=args.pause)
            subs = sum(1 for b in ladders if sub_period(b))
            print(f"\n[{now()[:19]}] {len(ladders)} ladders "
                  f"({subs} sub-period, settle intra-game) | sports: "
                  + ", ".join(f"{k}({v})" for k, v in sorted(sports.items(),
                                                             key=lambda kv: -kv[1])))
            reconcile(c, state, args)
            save_state(state)
            hits = past_hits()
            if args.max_days:
                allb = list(ladders)
                ladders = {b: k for b, k in ladders.items()
                           if within_days(b, args.max_days)}
                print(f"  {len(ladders)} ladders settle within {args.max_days}d")
                if not ladders and allb:
                    # an empty window is not an empty venue: say when the next
                    # one actually resolves, since CFB only plays certain days
                    nxt = sorted({game_date(b) for b in allb if game_date(b)})
                    print(f"  nearest settlement dates: {', '.join(nxt[:5])}")
                    print(f"  every ladder here is college football, which plays "
                          f"Thu-Sat - raise --max-days to reach them.")
            ranked_all = rank_games(ladders, hits, args.rank)
            games = ranked_all[: args.max_games] if args.max_games else ranked_all
            if hits:
                top = [g for g, _k in games[:3]]
                print(f"  prioritising: {', '.join(t[8:38] for t in top)}")
            print(f"  hurdle {args.hurdle:.4f}/day -> needs "
                  f"{args.hurdle * 0.12:.4f} on a quarter ladder, "
                  f"{args.hurdle * 7:.3f} on a game a week out")
            found = locked = 0.0
            pool = []
            for base, ks in games:
                want = sorted(sorted(ks, key=lambda k: abs(k))[: args.near])
                q = quotes_for(c, ks, want, args.pause, main._throttle)
                # ONE scan, two risk profiles. A monotonicity violation is a
                # vertical whose entry cost is negative; a key-number vertical
                # is the same position bought for a small premium. Scanning only
                # for free money missed most of what is there - on one real
                # ladder this finds five tradeable verticals where the
                # arbitrage-only scan found a couple, best +0.096 a share
                # against the arb's +0.042.
                from src.pm_us.vertical import fair_from_ladder
                from src.pm_us.vertical import scan as vscan
                pmf = fair_from_ladder(q) if args.verticals else {}
                cands = vscan(q, pmf, tick=args.tick, maker=not args.take,
                              min_ev=args.min_ev)
                days_to = days_to_settle(base)
                need = max(args.hurdle * days_to, args.min_credit)
                for r in cands:
                    if not args.verticals and not r["risk_free"]:
                        continue
                    l1, l2, sz = r["l1"], r["l2"], r["depth"]
                    credit = -r["entry"]
                    if sz < args.min_size:
                        continue
                    # risk-free legs gate on return per day of locked capital;
                    # bets gate on EV, because their payoff is not the credit
                    if r["risk_free"]:
                        if credit < need:
                            continue
                        used = state["deployed"] / max(args.max_capital, 1e-9)
                        if credit < need * (1.0 + 3.0 * used):
                            continue
                    elif r["ev"] < max(args.min_ev, args.hurdle * days_to):
                        continue
                    # COLLECT. Sizing happens once, across every game, so that
                    # capital spreads by breadth instead of piling into whatever
                    # the first game happened to offer.
                    pool.append({**r, "base": base, "ks": ks, "q": q,
                                 "days": days_to, "depth_cap": sz})
            mins = (time.time() - t0) / 60
            # ONE allocation across everything found, breadth first
            from src.pm_us.allocate import plan, summarise
            free = max(args.max_capital - state["deployed"], 0.0)
            sized = plan(pool, free, base_shares=args.base_shares,
                         max_shares=args.max_size, min_shares=args.min_size)
            if pool:
                sm = summarise(sized, max(free, 1e-9))
                print(f"  {len(pool)} candidates -> {sm['positions']} positions, "
                      f"${sm['deployed']:.2f} of ${free:.2f} "
                      f"({sm['utilisation']:.0%}), expected ${sm['expected']:.2f}")
            for row in sized:
                size = int(min(row["shares"], row["depth_cap"]
                               if args.take else row["shares"]))
                if size < args.min_size:
                    continue
                r, base, ks, q = row, row["base"], row["ks"], row["q"]
                l1, l2 = r["l1"], r["l2"]
                found += r["ev"] * size
                tag = "ARB" if r["risk_free"] else "BET"
                d = row["days"]
                print(f"  {tag} {base[:28]:<28} {l1:+.1f}/{l2:+.1f} "
                      f"entry {r['entry']:+.4f} EV {r['ev']:+.4f} x{size} "
                      f"| {d:.2f}d")
                if not args.live:
                    log({"kind": "opportunity", "game": base, "sell": l1,
                         "buy": l2, "entry": round(r["entry"], 4),
                         "ev": round(r["ev"], 4), "size": size,
                         "risk_free": r["risk_free"], "sweep": sweep})
                    continue
                args.sell_px, args.buy_px = q[l1]["bid"], q[l2]["ask"]
                args.unwind_px = q[l1]["ask"] or (q[l1]["bid"] + args.cost)
                ks_named = dict(ks); ks_named["_base"] = base
                locked += execute_pair(c, ks_named, -r["entry"], l1, l2, size,
                                       state, args, quotes=q)
                save_state(state)

            print(f"  pacing: {main._throttle.stats()}")
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
